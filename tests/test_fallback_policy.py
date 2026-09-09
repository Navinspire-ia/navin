# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Model switching policy: a blocked model never ends the turn.

The rule: a model that blocks, for whatever reason, is asked again at most
twice (only when a retry can help), then the next model of the list takes the
step and the switch is announced. Text matching stays specific: it decides
whether a retry is worth the wait, and prose such as ``empty`` or ``balance``
in an error body must not be read as a rate limit.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from navin.providers.fallback_policy import (
    CIRCUIT_MAX_COOLDOWN_S,
    JITTER_RATIO,
    STICKY_MAX_DELAY_S,
    apply_jitter,
    circuit_cooldown_s,
    is_blocking_failure,
    is_worth_retrying,
    model_switch_notice,
    retry_delay,
    should_switch_model,
    sticky_retry_budget,
    text_indicates_availability_problem,
)


@dataclass
class Err:
    """Minimal stand-in for an error LLMResponse."""

    content: str | None = None
    finish_reason: str = "error"
    error_status_code: int | None = None
    error_kind: str | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_retry_after_s: float | None = None
    error_should_retry: bool | None = None
    retry_after: float | None = None


class TestTextMatchingIsSpecific:
    @pytest.mark.parametrize(
        "text",
        [
            "the prompt contains an empty field",
            "your account balance sheet tool failed",
            "the connection between these two ideas is unclear",
            "tool returned an empty list",
            "timeoutish naming is not an error",
            "the overloadedQueue class needs refactoring",
        ],
    )
    def test_ordinary_prose_is_not_read_as_an_availability_problem(self, text):
        # These are the exact shapes that used to be read as a rate limit. A
        # healthy answer is never an error response, so it is never switched.
        assert text_indicates_availability_problem(text) is False
        assert should_switch_model(Err(content=text, finish_reason="stop")) is False

    @pytest.mark.parametrize(
        "text",
        [
            "429 rate limit exceeded",
            "upstream is overloaded, try again",
            "the request timed out",
            "API returned empty choices",
            "no choices returned by provider",
            "Service Unavailable",
            "insufficient balance",
            "out of credits",
        ],
    )
    def test_real_availability_failures_still_switch(self, text):
        assert text_indicates_availability_problem(text) is True
        assert should_switch_model(Err(content=text)) is True

    def test_matching_is_case_insensitive(self):
        assert text_indicates_availability_problem("RATE LIMIT reached") is True

    def test_provider_identifiers_embedded_in_text_still_match(self):
        # Underscores must stay permeable so real provider codes are recognized.
        assert text_indicates_availability_problem("error: rate_limit_exceeded") is True

    def test_empty_text(self):
        assert text_indicates_availability_problem(None) is False
        assert text_indicates_availability_problem("") is False


class TestBlockingFailures:
    """Definitive for this model: switch at once, never retry."""

    @pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422])
    def test_client_errors_switch_without_a_retry(self, status):
        # A bad key, a dropped slug, a request shape this vendor rejects: asking
        # the same model again returns the same answer, another model may not.
        err = Err(error_status_code=status, content="rate limit")
        assert is_blocking_failure(err) is True
        assert is_worth_retrying(err) is False
        assert should_switch_model(err) is True

    @pytest.mark.parametrize(
        "kind",
        ["authentication", "permission", "content_filter", "refusal", "context_length"],
    )
    def test_blocking_kinds_switch_without_a_retry(self, kind):
        err = Err(error_kind=kind)
        assert is_worth_retrying(err) is False
        assert should_switch_model(err) is True

    def test_an_explicit_do_not_retry_is_honoured_for_the_retry_only(self):
        err = Err(error_should_retry=False, error_kind="timeout")
        assert is_worth_retrying(err) is False
        assert should_switch_model(err) is True

    def test_a_successful_response_is_not_a_failure(self):
        assert should_switch_model(Err(finish_reason="stop", content="hello")) is False
        assert should_switch_model(Err(finish_reason="tool_calls", content=None)) is False


class TestSwitchDecision:
    @pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 599])
    def test_availability_statuses_switch(self, status):
        assert should_switch_model(Err(error_status_code=status)) is True

    @pytest.mark.parametrize(
        "kind", ["timeout", "connection", "server_error", "rate_limit", "overloaded"]
    )
    def test_transient_kinds_switch(self, kind):
        assert should_switch_model(Err(error_kind=kind)) is True

    def test_provider_enums_still_allow_substring_matching(self):
        # These are provider enums, not prose, so a bare token is unambiguous here.
        assert should_switch_model(Err(error_code="insufficient_balance")) is True
        assert should_switch_model(Err(error_type="empty_choices")) is True

    def test_an_explicit_retry_hint_switches(self):
        assert should_switch_model(Err(error_should_retry=True)) is True

    def test_an_unrecognized_error_switches_too(self):
        # "Blocks for whatever reason": an error nobody classified is still a
        # step the chosen model did not deliver.
        assert should_switch_model(Err(content="something odd happened")) is True

    def test_sanitized_upstream_and_generic_errors_switch(self):
        # After the chat-safe rewrite, the bubble no longer contains "502" or
        # "rate limit". Failover still has to fire, otherwise the turn dies
        # on the model the user picked.
        from navin.providers.user_facing_errors import user_facing_llm_error

        upstream = user_facing_llm_error(
            "{'code': 502, 'message': 'Upstream error', "
            "'metadata': {'error_type': 'provider_unavailable'}}"
        )
        generic = user_facing_llm_error("")
        assert should_switch_model(Err(content=upstream)) is True
        assert should_switch_model(Err(content=generic)) is True
        assert should_switch_model(Err(content=f"Error: {generic}")) is True

    def test_generic_error_with_a_client_status_switches_at_once(self):
        from navin.providers.user_facing_errors import user_facing_llm_error

        generic = user_facing_llm_error("")
        err = Err(content=generic, error_status_code=400)
        assert should_switch_model(err) is True
        assert is_worth_retrying(err) is False


class TestStickyBudget:
    def test_transient_failures_use_the_full_budget(self):
        assert sticky_retry_budget(Err(error_status_code=503), 2) == 2
        assert sticky_retry_budget(Err(error_kind="overloaded"), 2) == 2

    def test_definitive_failures_get_no_retry(self):
        assert sticky_retry_budget(Err(error_status_code=401), 2) == 0
        assert sticky_retry_budget(Err(error_kind="refusal"), 2) == 0

    def test_a_timeout_already_spent_its_window(self):
        assert sticky_retry_budget(Err(error_kind="timeout"), 2) == 0
        assert sticky_retry_budget(Err(error_status_code=408), 2) == 0

    def test_a_long_retry_after_switches_instead_of_waiting(self):
        # The provider said "not before 30s": asking again in 10s is not honouring
        # it, switching is.
        assert sticky_retry_budget(Err(error_status_code=429, error_retry_after_s=30), 2) == 0
        assert sticky_retry_budget(Err(error_status_code=429, error_retry_after_s=3), 2) == 2

    def test_an_unclassified_glitch_gets_the_budget(self):
        assert sticky_retry_budget(Err(content="Expecting value: line 1 column 1"), 2) == 2

    def test_a_zero_or_negative_budget_stays_zero(self):
        assert sticky_retry_budget(Err(error_status_code=503), 0) == 0
        assert sticky_retry_budget(Err(error_status_code=503), -3) == 0


class TestCircuitCooldown:
    def test_no_hint_means_the_default_breaker(self):
        assert circuit_cooldown_s(Err(error_status_code=503)) is None

    def test_a_short_hint_is_a_sticky_wait_not_a_cooldown(self):
        assert circuit_cooldown_s(Err(error_status_code=429, error_retry_after_s=5)) is None

    def test_a_long_hint_skips_the_model_for_that_long(self):
        assert circuit_cooldown_s(Err(error_status_code=429, error_retry_after_s=45)) == 45.0

    def test_the_cooldown_is_capped(self):
        assert (
            circuit_cooldown_s(Err(error_status_code=429, retry_after=3600))
            == CIRCUIT_MAX_COOLDOWN_S
        )

    def test_an_exhausted_account_is_skipped_for_the_full_cooldown(self):
        assert circuit_cooldown_s(Err(error_status_code=402)) == CIRCUIT_MAX_COOLDOWN_S
        assert circuit_cooldown_s(Err(error_code="insufficient_quota")) == CIRCUIT_MAX_COOLDOWN_S
        assert (
            circuit_cooldown_s(Err(content="Your credit balance is too low to access the API"))
            == CIRCUIT_MAX_COOLDOWN_S
        )

    def test_a_load_balancer_error_is_not_an_empty_wallet(self):
        # "balance" inside "load balancer" used to read as an exhausted account.
        busy = Err(error_status_code=502, content="502: upstream load balancer timeout")
        assert circuit_cooldown_s(busy) is None
        assert is_worth_retrying(busy) is True

    def test_a_malformed_hint_is_ignored(self):
        assert circuit_cooldown_s(Err(error_status_code=429, retry_after="soon")) is None


class TestStickyRetryDecision:
    @pytest.mark.parametrize("status", [409, 429, 500, 502, 503, 504])
    def test_busy_endpoints_are_worth_waiting_for(self, status):
        assert is_worth_retrying(Err(error_status_code=status)) is True

    @pytest.mark.parametrize(
        "err",
        [
            Err(error_code="insufficient_balance"),
            Err(error_type="quota_exceeded"),
            Err(content="insufficient quota for this month"),
            Err(content="out of credits"),
        ],
    )
    def test_exhausted_accounts_are_not_worth_waiting_for(self, err):
        # Quota and balance do not refill in seconds; waiting only delays the turn.
        assert should_switch_model(err) is True
        assert is_worth_retrying(err) is False

    def test_a_blocking_failure_is_never_retried(self):
        assert is_worth_retrying(Err(error_status_code=400)) is False

    def test_a_transient_kind_without_status_is_retried(self):
        assert is_worth_retrying(Err(error_kind="overloaded")) is True

    def test_a_non_retryable_status_is_not_retried(self):
        assert is_worth_retrying(Err(error_status_code=501)) is False

    def test_a_timeout_is_not_retried_on_the_same_model(self):
        # Each timeout already cost a full request or idle window; the next
        # model answers in seconds.
        assert is_worth_retrying(Err(error_kind="timeout")) is False
        assert is_worth_retrying(Err(error_status_code=408)) is False
        assert should_switch_model(Err(error_kind="timeout")) is True


def _ceiling(base: float) -> float:
    """Widest a jittered delay may get for *base*."""
    return base * (1 + JITTER_RATIO)


class TestRetryDelay:
    def test_the_wait_grows_between_attempts(self):
        # Compared at their floors, so the jitter roll cannot flip the order.
        first, second, third = (
            retry_delay(n, base=1.5) / (1 + JITTER_RATIO) for n in (1, 2, 3)
        )
        assert first < second < third

    def test_the_provider_hint_wins_over_our_guess(self):
        # Guessing shorter than the provider asked only earns another rate limit,
        # so the hint is the floor - jitter may only push the retry later.
        assert 7.0 <= retry_delay(1, Err(error_retry_after_s=7.0)) <= _ceiling(7.0)
        assert 4.0 <= retry_delay(1, Err(retry_after=4.0)) <= _ceiling(4.0)

    def test_an_absurd_hint_is_capped(self):
        assert 30.0 <= retry_delay(1, Err(error_retry_after_s=9999)) <= _ceiling(30.0)

    def test_a_malformed_hint_falls_back_to_backoff(self):
        assert retry_delay(1, Err(error_retry_after_s="soon")) > 0

    def test_the_backoff_is_capped(self):
        assert 20.0 <= retry_delay(50) <= _ceiling(20.0)

    def test_no_response_still_yields_a_wait(self):
        assert retry_delay(1) > 0

    def test_the_sticky_ceiling_is_hard(self):
        # With another model available, no single wait for the chosen one may
        # exceed the sticky ceiling, hint or not, jitter included.
        assert retry_delay(1, Err(error_retry_after_s=25), max_delay=STICKY_MAX_DELAY_S) == STICKY_MAX_DELAY_S
        assert retry_delay(50, max_delay=STICKY_MAX_DELAY_S) == STICKY_MAX_DELAY_S
        assert retry_delay(1, max_delay=STICKY_MAX_DELAY_S) < STICKY_MAX_DELAY_S


class TestJitter:
    """Retries must not re-converge on the instant that caused the limit.

    Every subscriber runs against the same OpenRouter account, so a shared
    rate limit hands them all the same delay at the same moment. Undithered,
    they come back together and rebuild the spike.
    """

    def test_a_retry_is_never_pulled_earlier(self):
        # The floor is the point: retrying before the wait we were told to take
        # just earns another refusal.
        assert all(apply_jitter(10.0) >= 10.0 for _ in range(2_000))

    def test_the_spread_stays_within_its_ratio(self):
        assert max(apply_jitter(10.0) for _ in range(2_000)) <= _ceiling(10.0)

    def test_nothing_to_wait_stays_nothing(self):
        assert apply_jitter(0) == 0.0
        assert apply_jitter(-5) == 0.0

    def test_the_roll_drives_the_result(self):
        assert apply_jitter(8.0, rand=lambda: 0.0) == 8.0
        assert apply_jitter(8.0, rand=lambda: 1.0) == _ceiling(8.0)

    def test_clients_sharing_one_limit_land_apart(self):
        # A fleet taking the same 429 must not queue up on one instant.
        waits = {apply_jitter(12.0) for _ in range(500)}
        assert len(waits) > 100


class TestSwitchNotice:
    def test_the_notice_names_both_models(self):
        notice = model_switch_notice("gemini-3.6", "deepseek-v4")
        assert "gemini-3.6" in notice
        assert "deepseek-v4" in notice


class FakeProvider:
    """Records calls and replays a scripted sequence of responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, **_kwargs):
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return Err(finish_reason="stop", content="ok")

    async def chat_stream(self, **kwargs):
        return await self.chat(**kwargs)

    def get_default_model(self):
        return "gemini-3.6"

    @property
    def generation(self):
        return None


@dataclass
class Preset:
    model: str = "deepseek-v4"
    max_tokens: int = 1024
    temperature: float = 0.7
    reasoning_effort: str | None = None


def _provider(primary, presets, fallback=None, on_switch=None):
    from navin.providers.fallback_provider import FallbackProvider

    return FallbackProvider(
        primary,
        presets,
        lambda _preset: fallback or FakeProvider([]),
        on_model_switch=on_switch,
    )


class TestStickyPrimary:
    def test_the_chosen_model_is_retried_before_any_switch(self, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        primary = FakeProvider(
            [
                Err(error_kind="overloaded", content="overloaded"),
                Err(finish_reason="stop", content="from gemini"),
            ]
        )
        fallback = FakeProvider([])
        result = asyncio.run(_provider(primary, [Preset()], fallback).chat(model="gemini-3.6"))
        assert result.content == "from gemini"
        assert primary.calls == 2
        # The whole point: the fallback was never touched.
        assert fallback.calls == 0

    def test_retries_are_bounded_then_the_switch_happens(self, monkeypatch):
        from navin.providers.fallback_provider import PRIMARY_STICKY_RETRIES

        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        primary = FakeProvider(
            [Err(error_kind="overloaded", content="overloaded")] * 10
        )
        fallback = FakeProvider([Err(finish_reason="stop", content="from deepseek")])
        result = asyncio.run(_provider(primary, [Preset()], fallback).chat(model="gemini-3.6"))
        assert primary.calls == PRIMARY_STICKY_RETRIES + 1
        assert result.content == "from deepseek"

    @pytest.mark.parametrize(
        "err",
        [
            Err(error_status_code=400, content="bad request"),
            Err(error_status_code=401, content="invalid api key"),
            Err(error_status_code=404, content="model not found"),
            Err(error_kind="refusal", content="I cannot help with that"),
            Err(error_kind="content_filter", content="blocked"),
            Err(error_kind="timeout", content="request timed out"),
        ],
    )
    def test_a_definitive_failure_switches_without_a_retry(self, monkeypatch, err):
        # A retry cannot change a 4xx, a refusal or a key problem, and a timeout
        # already cost a full window: the next model takes the step at once.
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        primary = FakeProvider([err] * 5)
        fallback = FakeProvider([Err(finish_reason="stop", content="from deepseek")])
        result = asyncio.run(_provider(primary, [Preset()], fallback).chat(model="gemini-3.6"))
        assert primary.calls == 1
        assert fallback.calls == 1
        assert result.content == "from deepseek"

    def test_an_unclassified_error_is_retried_then_switched(self, monkeypatch):
        from navin.providers.fallback_provider import PRIMARY_STICKY_RETRIES

        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        primary = FakeProvider(
            [Err(content="the tool returned an empty list of files")] * 10
        )
        fallback = FakeProvider([Err(finish_reason="stop", content="from deepseek")])
        result = asyncio.run(_provider(primary, [Preset()], fallback).chat(model="gemini-3.6"))
        assert primary.calls == PRIMARY_STICKY_RETRIES + 1
        assert fallback.calls == 1
        assert result.content == "from deepseek"

    def test_a_long_retry_after_opens_the_circuit_for_the_next_steps(self, monkeypatch):
        # The provider named a 40s wait: no sticky retry (switch now), and the
        # following steps go straight to the fallback instead of paying the
        # refusal again until the failure counter trips.
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        primary = FakeProvider(
            [Err(error_status_code=429, error_retry_after_s=40, content="rate limit")] * 5
        )
        fallback = FakeProvider([Err(finish_reason="stop", content="from deepseek")] * 5)
        provider = _provider(primary, [Preset()], fallback)
        first = asyncio.run(provider.chat(model="gemini-3.6"))
        second = asyncio.run(provider.chat(model="gemini-3.6"))
        assert first.content == second.content == "from deepseek"
        assert primary.calls == 1
        assert fallback.calls == 2
        assert provider._primary_cooldown_s == 40.0

    def test_the_circuit_closes_again_on_a_success(self, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        primary = FakeProvider(
            [
                Err(error_status_code=402, content="insufficient credits"),
                Err(finish_reason="stop", content="from gemini"),
            ]
        )
        fallback = FakeProvider([Err(finish_reason="stop", content="from deepseek")] * 5)
        provider = _provider(primary, [Preset()], fallback)
        asyncio.run(provider.chat(model="gemini-3.6"))
        assert provider._primary_tripped_at is not None
        provider._primary_tripped_at -= provider._primary_cooldown_s  # cooldown elapsed
        result = asyncio.run(provider.chat(model="gemini-3.6"))
        assert result.content == "from gemini"
        assert provider._primary_tripped_at is None
        assert provider._primary_failures == 0

    def test_a_first_try_success_costs_nothing_extra(self):
        primary = FakeProvider([Err(finish_reason="stop", content="ok")])
        result = asyncio.run(_provider(primary, [Preset()]).chat(model="gemini-3.6"))
        assert primary.calls == 1
        assert result.content == "ok"


class TestStickinessIsScoped:
    def test_the_free_chain_hops_immediately(self, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        from navin.providers.fallback_provider import FallbackProvider

        primary = FakeProvider([Err(error_status_code=429, content="rate limit")] * 5)
        fallback = FakeProvider([Err(finish_reason="stop", content="next free model")])
        provider = FallbackProvider(
            primary,
            [Preset()],
            lambda _p: fallback,
            sticky_retries=0,
        )
        result = asyncio.run(provider.chat(model="something:free"))
        assert primary.calls == 1
        assert result.content == "next free model"

    def test_a_negative_setting_is_clamped(self):
        from navin.providers.fallback_provider import FallbackProvider

        primary = FakeProvider([Err(finish_reason="stop", content="ok")])
        provider = FallbackProvider(
            primary, [Preset()], lambda _p: FakeProvider([]), sticky_retries=-5
        )
        asyncio.run(provider.chat(model="m"))
        assert primary.calls == 1


class TestLadderAndChainCompose:
    """The chain does the per-model retries; the outer ladder adds one pass.

    Stacked naively, the two ladders multiplied: every outer attempt re-ran
    (1 + sticky) primary calls plus the whole fallback chain, four times. Now
    the rule is the same wherever the call comes from (two quick retries on the
    chosen model, then the next model) and ``chat_with_retry`` re-runs a chain
    once more at most, to ride out a moment where everything was busy.
    """

    def test_the_rule_holds_inside_the_outer_ladder(self, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        from navin.providers.base import _IN_RETRY_LADDER
        from navin.providers.fallback_provider import PRIMARY_STICKY_RETRIES

        primary = FakeProvider(
            [Err(error_kind="overloaded", content="overloaded")] * 10
        )
        fallback = FakeProvider([Err(finish_reason="stop", content="from deepseek")])
        provider = _provider(primary, [Preset()], fallback)

        async def run():
            token = _IN_RETRY_LADDER.set(True)
            try:
                return await provider.chat(model="gemini-3.6")
            finally:
                _IN_RETRY_LADDER.reset(token)

        result = asyncio.run(run())
        assert primary.calls == PRIMARY_STICKY_RETRIES + 1
        assert result.content == "from deepseek"

    def test_chat_with_retry_gives_a_chain_one_extra_pass(self, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        from navin.providers.fallback_provider import PRIMARY_STICKY_RETRIES

        primary = FakeProvider(
            [Err(error_kind="overloaded", content="overloaded")] * 50
        )
        fallback = FakeProvider(
            [Err(error_kind="overloaded", content="also overloaded")] * 50
        )
        provider = _provider(primary, [Preset()], fallback)
        result = asyncio.run(provider.chat_with_retry(
            messages=[{"role": "user", "content": "hi"}],
            model="gemini-3.6",
            max_tokens=100,
            temperature=0.5,
            reasoning_effort=None,
        ))
        assert result.finish_reason == "error"
        # Two passes over the chain: the chosen model gets its retries in each,
        # the fallback is asked once per pass.
        assert fallback.calls == 2
        assert primary.calls == 2 * (PRIMARY_STICKY_RETRIES + 1)

    def test_a_single_model_keeps_the_full_ladder(self, monkeypatch):
        # Without another model to switch to, waiting is all there is.
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        from navin.providers.base import LLMProvider

        primary = FakeProvider(
            [Err(error_kind="overloaded", content="overloaded")] * 50
        )
        provider = _provider(primary, [], None)
        result = asyncio.run(provider.chat_with_retry(
            messages=[{"role": "user", "content": "hi"}],
            model="gemini-3.6",
            max_tokens=100,
            temperature=0.5,
            reasoning_effort=None,
        ))
        assert result.finish_reason == "error"
        assert primary.calls == len(LLMProvider._CHAT_RETRY_DELAYS) + 1

    def test_a_stalled_provider_gets_one_retry_not_the_whole_ladder(self, monkeypatch):
        """Each timeout already cost a full request window (two minutes).

        Retrying four times parked one step for eight silent minutes; two
        consecutive stalls mean the provider is down for this step.
        """
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        primary = FakeProvider(
            [Err(error_kind="timeout", content="request timed out")] * 50
        )
        fallback = FakeProvider(
            [Err(error_kind="timeout", content="request timed out")] * 50
        )
        provider = _provider(primary, [Preset()], fallback)
        result = asyncio.run(provider.chat_with_retry(
            messages=[{"role": "user", "content": "hi"}],
            model="gemini-3.6",
            max_tokens=100,
            temperature=0.5,
            reasoning_effort=None,
        ))
        assert result.finish_reason == "error"
        assert fallback.calls == 2

    def test_a_stalled_provider_gets_one_retry_not_the_whole_ladder(self, monkeypatch):
        """Each timeout already cost a full request window (two minutes).

        Retrying four times parked one step for eight silent minutes; two
        consecutive stalls mean the provider is down for this step.
        """
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        primary = FakeProvider(
            [Err(error_kind="timeout", content="request timed out")] * 50
        )
        fallback = FakeProvider(
            [Err(error_kind="timeout", content="request timed out")] * 50
        )
        provider = _provider(primary, [Preset()], fallback)
        result = asyncio.run(provider.chat_with_retry(
            messages=[{"role": "user", "content": "hi"}],
            model="gemini-3.6",
            max_tokens=100,
            temperature=0.5,
            reasoning_effort=None,
        ))
        assert result.finish_reason == "error"
        assert fallback.calls == 2

    def test_direct_chat_keeps_the_sticky_wait(self, monkeypatch):
        # A bare .chat call follows the same rule: two quick retries on the
        # chosen model, then the visible switch.
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        from navin.providers.fallback_provider import PRIMARY_STICKY_RETRIES

        primary = FakeProvider(
            [Err(error_kind="overloaded", content="overloaded")] * 10
        )
        fallback = FakeProvider([Err(finish_reason="stop", content="from deepseek")])
        result = asyncio.run(_provider(primary, [Preset()], fallback).chat(model="gemini-3.6"))
        assert primary.calls == PRIMARY_STICKY_RETRIES + 1
        assert result.content == "from deepseek"


class TestSwitchIsAnnounced:
    def test_a_switch_notifies_with_both_model_names(self, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        seen: list[tuple[str, str]] = []
        primary = FakeProvider([Err(error_status_code=429, content="rate limit")] * 5)
        fallback = FakeProvider([Err(finish_reason="stop", content="from deepseek")])
        provider = _provider(
            primary,
            [Preset()],
            fallback,
            on_switch=lambda chosen, served: seen.append((chosen, served)),
        )
        asyncio.run(provider.chat(model="gemini-3.6"))
        assert seen == [("gemini-3.6", "deepseek-v4")]

    def test_an_async_callback_is_awaited(self, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        seen: list[tuple[str, str]] = []

        async def notify(chosen, served):
            seen.append((chosen, served))

        primary = FakeProvider([Err(error_status_code=429, content="rate limit")] * 5)
        fallback = FakeProvider([Err(finish_reason="stop", content="ok")])
        provider = _provider(primary, [Preset()], fallback, on_switch=notify)
        asyncio.run(provider.chat(model="gemini-3.6"))
        assert seen == [("gemini-3.6", "deepseek-v4")]

    def test_no_switch_means_no_notification(self):
        seen: list[tuple[str, str]] = []
        primary = FakeProvider([Err(finish_reason="stop", content="ok")])
        provider = _provider(
            primary, [Preset()], on_switch=lambda c, s: seen.append((c, s))
        )
        asyncio.run(provider.chat(model="gemini-3.6"))
        assert seen == []

    def test_a_failing_callback_never_breaks_the_turn(self, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _no_sleep)

        def boom(_chosen, _served):
            raise RuntimeError("bus down")

        primary = FakeProvider([Err(error_status_code=429, content="rate limit")] * 5)
        fallback = FakeProvider([Err(finish_reason="stop", content="delivered")])
        provider = _provider(primary, [Preset()], fallback, on_switch=boom)
        result = asyncio.run(provider.chat(model="gemini-3.6"))
        assert result.content == "delivered"


class TestNoticeReachesTheUser:
    """End to end: a substitution must not stay buried in the logs."""

    def setup_method(self):
        from navin.providers.model_switch_notice import set_model_switch_publisher

        set_model_switch_publisher(None)

    teardown_method = setup_method

    def test_the_provider_publishes_through_the_registered_publisher(self, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        from navin.providers.factory import FallbackProvider
        from navin.providers.model_switch_notice import (
            notify_model_switch,
            set_model_switch_publisher,
        )

        seen: list[tuple[str, str]] = []
        set_model_switch_publisher(lambda chosen, served: seen.append((chosen, served)))

        primary = FakeProvider([Err(error_status_code=429, content="rate limit")] * 5)
        fallback = FakeProvider([Err(finish_reason="stop", content="ok")])
        provider = FallbackProvider(
            primary,
            [Preset()],
            lambda _p: fallback,
            on_model_switch=notify_model_switch,
        )
        asyncio.run(provider.chat(model="gemini-3.6"))
        assert seen == [("gemini-3.6", "deepseek-v4")]

    def test_nothing_registered_is_silent_not_fatal(self, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        from navin.providers.model_switch_notice import notify_model_switch

        asyncio.run(notify_model_switch("gemini-3.6", "deepseek-v4"))

    def test_an_identical_model_is_not_reported_as_a_switch(self):
        from navin.providers.model_switch_notice import (
            notify_model_switch,
            set_model_switch_publisher,
        )

        seen: list[tuple[str, str]] = []
        set_model_switch_publisher(lambda chosen, served: seen.append((chosen, served)))
        asyncio.run(notify_model_switch("gemini-3.6", "gemini-3.6"))
        asyncio.run(notify_model_switch("gemini-3.6", ""))
        assert seen == []

    def test_a_broken_publisher_never_breaks_the_turn(self):
        from navin.providers.model_switch_notice import (
            notify_model_switch,
            set_model_switch_publisher,
        )

        def boom(_chosen, _served):
            raise RuntimeError("bus down")

        set_model_switch_publisher(boom)
        asyncio.run(notify_model_switch("gemini-3.6", "deepseek-v4"))

    def test_the_bus_event_becomes_a_user_facing_notice(self):
        from navin.bus.runtime_events import ModelFailedOver
        from navin.session.webui_turns import WebuiTurnCoordinator

        published: list = []

        class _Bus:
            async def publish_outbound(self, message):
                published.append(message)

        coordinator = object.__new__(WebuiTurnCoordinator)
        coordinator.bus = _Bus()
        asyncio.run(
            coordinator._handle_model_failed_over(
                ModelFailedOver(chosen_model="gemini-3.6", served_model="deepseek-v4")
            )
        )
        assert len(published) == 1
        event = published[0].event
        assert "deepseek-v4" in event.title
        assert "gemini-3.6" in (event.detail or "")
        # Info stays in the bell; a warning would toast over the live turn.
        assert event.level == "info"
        # A run of substitutions must fold into one entry, not scroll the panel.
        assert event.key == "model-failover:gemini-3.6"

    def test_the_publisher_emits_on_the_runtime_bus(self):
        from navin.bus.runtime_events import ModelFailedOver, RuntimeEventPublisher

        seen: list[ModelFailedOver] = []

        async def _emit():
            publisher = RuntimeEventPublisher()
            publisher.bus.subscribe(seen.append, ModelFailedOver)
            # publish_nowait needs a running loop, so this cannot be a sync call.
            publisher.model_failed_over("gemini-3.6", "deepseek-v4")
            await asyncio.sleep(0)

        asyncio.run(_emit())
        assert seen[0].chosen_model == "gemini-3.6"
        assert seen[0].served_model == "deepseek-v4"


class StreamFakeProvider(FakeProvider):
    """Like FakeProvider, but emits a content delta before each response."""

    async def chat_stream(self, **kwargs):
        on_delta = kwargs.get("on_content_delta")
        if on_delta:
            maybe = on_delta("partial")
            if hasattr(maybe, "__await__"):
                await maybe
        return await self.chat(**kwargs)


class TestStreamedFailover:
    def test_a_502_after_partial_stream_still_switches(self, monkeypatch):
        # Gemini often streams a thinking token, then 502s. The old rule skipped
        # failover to avoid duplicate output. The turn then died on the model
        # the user picked even though DeepSeek was sitting unused.
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        primary = StreamFakeProvider(
            [Err(error_status_code=502, error_kind="server_error", content="upstream")]
            * 10
        )
        fallback = StreamFakeProvider(
            [Err(finish_reason="stop", content="from deepseek")]
        )
        recovered: list[bool] = []

        async def recover():
            recovered.append(True)

        async def delta(_text):
            return None

        result = asyncio.run(
            _provider(primary, [Preset()], fallback).chat_stream(
                model="gemini-3.6",
                on_content_delta=delta,
                on_stream_recover=recover,
            )
        )
        assert result.content == "from deepseek"
        assert recovered == [True]
        assert primary.calls == 1
        assert fallback.calls == 1

    def test_a_400_after_partial_stream_switches_in_a_new_segment(self, monkeypatch):
        # A vendor rejecting the request mid-stream is still a step the chosen
        # model did not deliver: the partial segment is closed and the next
        # model answers in a fresh one, with no duplicate output.
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        primary = StreamFakeProvider(
            [Err(error_status_code=400, content="bad request")]
        )
        fallback = StreamFakeProvider(
            [Err(finish_reason="stop", content="from deepseek")]
        )
        recovered: list[bool] = []

        async def recover():
            recovered.append(True)

        result = asyncio.run(
            _provider(primary, [Preset()], fallback).chat_stream(
                model="gemini-3.6",
                on_content_delta=_async_noop,
                on_stream_recover=recover,
            )
        )
        assert result.content == "from deepseek"
        assert recovered == [True]
        assert primary.calls == 1
        assert fallback.calls == 1


async def _async_noop(_text=None):
    return None


async def _no_sleep(_seconds):
    return None
