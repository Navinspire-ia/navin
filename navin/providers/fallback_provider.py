# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Provider wrapper that transparently fails over to fallback models on error."""

from __future__ import annotations

import asyncio
import math
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from loguru import logger

from navin.providers.base import (
    LLMProvider,
    LLMResponse,
    resolve_stream_idle_timeout_s,
    wait_stream_idle,
)
from navin.providers.fallback_policy import (
    QUOTA_CEILING_KIND,
    circuit_cooldown_s,
    is_exhausted_account,
)
from navin.providers.model_switch_notice import notify_relay

# Circuit breaker tuned to match OpenAICompatProvider's Responses API breaker.
_PRIMARY_FAILURE_THRESHOLD = 3
_PRIMARY_COOLDOWN_S = 60
_MISSING = object()

#: Extra attempts on the chosen model before the switch. The operator's rule:
#: a model that blocks is asked again up to three times, then the next model
#: of the list takes the step. Only failures that clear in seconds use the
#: budget; a definitive refusal or a timeout switches at once (fallback_policy).
PRIMARY_STICKY_RETRIES = 3

# A streamed model that shows no progress hands the step to the next model of
# the relay. The provider's own idle timer also counts keep-alive chunks, so a
# model sending nothing useful held the step until the runner's wall clock cut
# the whole relay (ten minutes) without any other model being asked. Content,
# reasoning and tool-call deltas reset the window. Unset, it follows
# NAVIN_STREAM_IDLE_TIMEOUT_S (two minutes by default: reasoning models often
# think silently for more than one); 0 disables it.
_MODEL_RELAY_IDLE_ENV = "NAVIN_MODEL_RELAY_IDLE_S"


def _positive_env_timeout(name: str, default: float) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring invalid {}={!r}", name, raw)
        return default
    if not math.isfinite(value) or value < 0:
        logger.warning("Ignoring invalid {}={!r}", name, raw)
        return default
    return value or None


def _relay_idle_timeout_s() -> float | None:
    """Seconds a streamed model may go without progress; None turns the watch off."""
    return _positive_env_timeout(_MODEL_RELAY_IDLE_ENV, resolve_stream_idle_timeout_s())


#: Why a model is left aside: its provider refused it, or it reached a ceiling.
_REFUSED = "refused"
_CEILING = "ceiling"

# Models every chain leaves aside, with the time.monotonic() moment each one
# comes back and why: refused by its provider (403 "not available for this
# account", 404, 401), or at a usage ceiling (quota, spent budget, empty
# balance). Shared by every FallbackProvider: a model that cannot answer is not
# asked again on each new step or turn, nor by another chat or subagent whose
# chain names it.
_REFUSED_UNTIL: dict[str, tuple[float, str]] = {}


def _set_aside(model: str, seconds: float, reason: str) -> float:
    """Leave *model* aside for *seconds*; a longer wait already set wins."""
    until = time.monotonic() + max(1.0, float(seconds))
    known = _REFUSED_UNTIL.get(model)
    if known is not None and known[0] >= until:
        return known[0]
    _REFUSED_UNTIL[model] = (until, reason)
    return until


def _aside(model: str) -> tuple[float, str] | None:
    """When *model* comes back and why it is left aside; None when it can answer."""
    known = _REFUSED_UNTIL.get(model)
    if known is None:
        return None
    if time.monotonic() >= known[0]:
        _REFUSED_UNTIL.pop(model, None)
        return None
    return known


class FallbackProvider(LLMProvider):
    """Wrap a primary provider and transparently failover to fallback models.

    When the primary model returns an error before content has been streamed,
    the wrapper tries each fallback model in order. When content was already
    streamed, the caller may close the current stream segment (``on_stream_recover``)
    and the wrapper continues failover with later deltas in a new segment. Each
    fallback model may reside on a different provider - a factory callable
    creates the underlying provider on-the-fly.

    Key design:
    - The chosen model gets up to ``sticky_retries`` quick retries for failures
      that clear on their own, then the chain moves on. Every error switches.
    - Failover is request-scoped (the wrapper itself is stateless between turns).
    - Recursive failover is prevented by the factory returning plain providers.
    - The chosen model is circuit-broken after repeated failures, or as soon as
      its provider names a wait longer than a sticky retry (Retry-After) or the
      account is out of credit, so the next steps do not pay the same refusal.
    """

    supports_stream_recover_callback = True

    def __init__(
        self,
        primary: LLMProvider,
        fallback_presets: list[Any],
        provider_factory: Callable[[Any], LLMProvider],
        on_model_switch: Callable[[str, str], Any] | None = None,
        sticky_retries: int = PRIMARY_STICKY_RETRIES,
        model_labels: Mapping[str, str] | None = None,
        relay_on_limit: bool = False,
    ):
        self._primary = primary
        # Display names by model slug ("Claude Fable 5.1"), for what the user reads.
        self._model_labels = dict(model_labels or {})
        # A relay answered the last step: the working line names it until the
        # chosen model answers again.
        self._relayed = False
        self._fallback_presets = list(fallback_presets)
        self._provider_factory = provider_factory
        self._has_fallbacks = bool(fallback_presets)
        self._primary_failures = 0
        self._primary_tripped_at: float | None = None
        self._primary_cooldown_s: float = _PRIMARY_COOLDOWN_S
        # Set to 0 for the automatic free-tier chain: there, rate limits are the
        # normal state and hopping to the next free model is the whole design.
        # A model the user picked deserves the wait instead.
        self._sticky_retries = max(0, int(sticky_retries))
        # On the plan's ladder every model draws on its own quota: a model at
        # its rate limit hands the step to the next one at once. Asked again, a
        # saturated Claude Opus cost up to four refusals of twenty seconds each
        # before Fable took the step (2026-11-06, "ca tourne en rond").
        self._relay_on_limit = bool(relay_on_limit)
        # Notified with (chosen_model, served_model) when a turn is answered by a
        # model the user did not pick. Without this the swap is invisible and the
        # UI keeps showing the chosen model.
        self._on_model_switch = on_model_switch

    async def _announce_switch(self, primary_model: str, served_model: str) -> None:
        if self._on_model_switch is None or served_model == primary_model:
            return
        try:
            result = self._on_model_switch(primary_model, served_model)
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:  # noqa: BLE001 - never fail a turn over a notice
            logger.warning("Model switch notification failed: {}", exc)

    def _label(self, model: str) -> str:
        return self._model_labels.get(model) or model.rsplit("/", 1)[-1]

    @staticmethod
    def _leave_aside(
        model: str,
        response: LLMResponse,
        cooldown_s: float,
        ceilings: dict[str, float],
        *,
        share_refusal: bool,
    ) -> None:
        """Put a model that cannot answer on the list every chain skips.

        A ceiling always goes there. A refusal of the chosen model stays with
        this chain: fixing a key in Settings must not leave it aside elsewhere.
        """
        if is_exhausted_account(response):
            ceilings[model] = _set_aside(model, cooldown_s, _CEILING)
        elif share_refusal and getattr(response, "error_status_code", None) in (401, 403, 404):
            _set_aside(model, cooldown_s, _REFUSED)

    def _ceiling_response(
        self, ceilings: dict[str, float], last_response: LLMResponse | None
    ) -> LLMResponse:
        """The answer of a relay whose every model reached its usage ceiling."""
        names = [self._label(model) for model in ceilings]
        wait_s = max(0.0, min(ceilings.values()) - time.monotonic())
        minutes = max(1, math.ceil(wait_s / 60))
        if len(names) == 1:
            reached = f"{names[0]} reached its usage limit"
            back = f"It comes back in about {minutes} min"
        else:
            reached = f"{', '.join(names[:-1])} and {names[-1]} reached their usage limit"
            back = f"The first one comes back in about {minutes} min"
        return LLMResponse(
            content=(
                f"{reached}, so no model could take over this step. "
                f"{back}; pick another model to keep going now."
            ),
            finish_reason="error",
            error_kind=QUOTA_CEILING_KIND,
            error_should_retry=False,
            error_retry_after_s=wait_s,
            error_detail=getattr(last_response, "error_detail", None),
            error_provider=getattr(last_response, "error_provider", None),
        )

    @property
    def generation(self):
        return self._primary.generation

    @generation.setter
    def generation(self, value):
        self._primary.generation = value

    # The credentials of a chain are those of the model the user picked. Code
    # that classifies a turn by its key (plan quota vs the user's own provider
    # credit, soft budget) must see them through the wrapper: hidden, a 402 on
    # the Navin managed key was reported as "your own API key is out of credit".
    @property
    def api_key(self) -> str | None:
        return getattr(self._primary, "api_key", None)

    @api_key.setter
    def api_key(self, value: str | None) -> None:
        self._primary.api_key = value

    @property
    def api_base(self) -> str | None:
        return getattr(self._primary, "api_base", None)

    @api_base.setter
    def api_base(self, value: str | None) -> None:
        self._primary.api_base = value

    def get_default_model(self) -> str:
        return self._primary.get_default_model()

    @property
    def supports_progress_deltas(self) -> bool:
        return bool(getattr(self._primary, "supports_progress_deltas", False))

    def _primary_available(self) -> bool:
        """Return True if the primary provider is not currently tripped."""
        if self._primary_tripped_at is None:
            return True
        if time.monotonic() - self._primary_tripped_at >= self._primary_cooldown_s:
            # Half-open: allow one probe attempt.
            return True
        return False

    def _open_primary_circuit(self, primary_model: str, cooldown_s: float, why: str) -> None:
        self._primary_tripped_at = time.monotonic()
        self._primary_cooldown_s = max(1.0, float(cooldown_s))
        logger.warning(
            "Primary model '{}' circuit open for {:.0f}s: {}",
            primary_model, self._primary_cooldown_s, why,
        )

    async def chat(self, **kwargs: Any) -> LLMResponse:
        if not self._has_fallbacks:
            return await self._primary.chat(**kwargs)
        return await self._try_with_fallback(
            lambda p, kw: p.chat(**kw), kwargs, has_streamed=None
        )

    async def chat_stream(self, **kwargs: Any) -> LLMResponse:
        on_stream_recover = kwargs.pop("on_stream_recover", None)
        if not self._has_fallbacks:
            return await self._primary.chat_stream(**kwargs)

        has_streamed: list[bool] = [False]
        original_delta = kwargs.get("on_content_delta")

        async def _tracking_delta(text: str) -> None:
            if text:
                has_streamed[0] = True
            if original_delta:
                await original_delta(text)

        kwargs["on_content_delta"] = _tracking_delta
        return await self._try_with_fallback(
            lambda p, kw: p.chat_stream(**kw),
            kwargs,
            has_streamed=has_streamed,
            on_stream_recover=on_stream_recover,
        )

    @staticmethod
    def _observe_abandoned_call(task: asyncio.Task[LLMResponse]) -> None:
        """Report a late exception if a timed-out provider ignored cancellation."""
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.opt(exception=True).warning("Cancelled model call failed after its relay moved on")

    async def _call_model(
        self,
        call: Callable[[LLMProvider, dict[str, Any]], Awaitable[LLMResponse]],
        provider: LLMProvider,
        kwargs: dict[str, Any],
        model: str,
        *,
        streaming: bool,
    ) -> LLMResponse:
        """Bound a streamed model's silence, not the work it is visibly doing.

        Content, reasoning and tool-call deltas reset the window; transport
        keep-alives and retry notices do not. ``wait_stream_idle`` grants a
        fresh window after a frozen event loop, where a plain wait_for would
        cancel a healthy reply on WSL wake-up. A call that does not stream
        shows no progress (summaries, compaction), so it keeps its provider's
        own request timeout.
        """
        idle_s = _relay_idle_timeout_s() if streaming else None
        if idle_s is None:
            try:
                return await call(provider, kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Model '{}' call raised before failover: {}", model, exc)
                return self._unexpected_error_response(exc)

        activity = asyncio.Queue[None](maxsize=1)
        last_activity = time.monotonic()
        last_signal = 0.0
        active = True
        call_kwargs = dict(kwargs)

        def _watch(callback: Any) -> Callable[[Any], Awaitable[None]]:
            async def _tracked(value: Any) -> None:
                nonlocal last_activity, last_signal
                # A transport can ignore cancellation (e.g. a thread-backed
                # stream). Its late deltas must not reach the new model's UI.
                if not active:
                    return
                if value:
                    now = time.monotonic()
                    last_activity = now
                    # High-rate token streams do not need one waiter per token.
                    if now - last_signal >= 1.0 and activity.empty():
                        last_signal = now
                        activity.put_nowait(None)
                if callback is not None:
                    await callback(value)
            return _tracked

        for name in ("on_content_delta", "on_thinking_delta", "on_tool_call_delta"):
            call_kwargs[name] = _watch(call_kwargs.get(name))

        async def _invoke() -> LLMResponse:
            try:
                return await call(provider, call_kwargs)
            finally:
                # Completion must wake the watchdog even if no delta was sent.
                if active and activity.empty():
                    activity.put_nowait(None)

        task = asyncio.create_task(_invoke())
        try:
            while not task.done():
                remaining = max(0.0, idle_s - (time.monotonic() - last_activity))
                try:
                    await wait_stream_idle(activity.get(), remaining)
                except asyncio.TimeoutError:
                    if task.done() or time.monotonic() - last_activity < idle_s:
                        continue
                    logger.warning(
                        "Model '{}' made no progress for {:.0f}s; trying the next relay",
                        model, idle_s,
                    )
                    # "stream stalled" gives the chat the dedicated sentence
                    # (the provider stopped answering), never the generic one.
                    return LLMResponse(
                        content=f"Error calling LLM: stream stalled ({model} made no progress for {idle_s:g}s)",
                        finish_reason="error",
                        error_kind="timeout",
                        error_detail=f"{model} sent no content, reasoning or tool call for {idle_s:g}s",
                    )
            return await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Model '{}' call raised before failover: {}", model, exc)
            return self._unexpected_error_response(exc)
        finally:
            active = False
            if not task.done():
                # Do not await a provider that ignores cancellation. Its late
                # exception is still logged, never treated as a second answer.
                task.cancel()
                task.add_done_callback(self._observe_abandoned_call)

    async def _try_with_fallback(
        self,
        call: Callable[[LLMProvider, dict[str, Any]], Awaitable[LLMResponse]],
        kwargs: dict[str, Any],
        has_streamed: list[bool] | None,
        on_stream_recover: Callable[[], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        primary_model = kwargs.get("model") or self._primary.get_default_model()
        primary_was_attempted = False
        primary_error = "unknown error"
        last_response: LLMResponse | None = None
        # The models of this chain at a usage ceiling, with when each comes back.
        ceilings: dict[str, float] = {}
        primary_aside = _aside(primary_model)
        if primary_aside is not None and primary_aside[1] == _CEILING:
            ceilings[primary_model] = primary_aside[0]
        # Every other model of the chain left aside: the chosen one is asked
        # anyway. A ceiling may reset early, and one refused call costs less
        # than a step refused without asking anyone.
        others_aside = all(_aside(fallback.model) is not None for fallback in self._fallback_presets)

        if (self._primary_available() and primary_aside is None) or others_aside:
            primary_was_attempted = True
            ceilings.pop(primary_model, None)
            response = await self._call_primary_stickily(
                call, kwargs, primary_model, has_streamed
            )
            if response.finish_reason != "error":
                self._primary_failures = 0
                self._primary_tripped_at = None
                self._primary_cooldown_s = _PRIMARY_COOLDOWN_S
                # Back: every chain may ask it again.
                _REFUSED_UNTIL.pop(primary_model, None)
                if self._relayed:
                    # The chosen model answers again: the working line drops the relay.
                    self._relayed = False
                    await notify_relay("")
                return response
            primary_error = (response.content or primary_error)[:120]

            if not self._should_fallback(response):
                logger.warning(
                    "Primary model '{}' returned non-fallbackable error: {}",
                    primary_model,
                    (response.content or "")[:120],
                )
                return response

            if has_streamed is not None and has_streamed[0]:
                logger.warning(
                    "Primary model '{}' failed after content was emitted; "
                    "starting a new stream segment and failing over",
                    primary_model,
                )
                has_streamed[0] = False
                if on_stream_recover:
                    await on_stream_recover()
                else:
                    kwargs["on_content_delta"] = None

            last_response = response
            self._primary_failures += 1
            cooldown = circuit_cooldown_s(response)
            if cooldown is not None:
                self._open_primary_circuit(
                    primary_model,
                    cooldown,
                    "quota, budget or credit used up"
                    if is_exhausted_account(response)
                    else "provider asked to come back later"
                    if getattr(response, "error_retry_after_s", None)
                    or getattr(response, "retry_after", None)
                    else "no access to this model",
                )
                self._leave_aside(
                    primary_model, response, cooldown, ceilings, share_refusal=False
                )
            elif self._primary_failures >= _PRIMARY_FAILURE_THRESHOLD:
                self._open_primary_circuit(
                    primary_model,
                    _PRIMARY_COOLDOWN_S,
                    f"{self._primary_failures} consecutive failures",
                )
        else:
            logger.debug("Primary model '{}' left aside; skipping", primary_model)

        primary_skipped = not primary_was_attempted
        for idx, fallback in enumerate(self._fallback_presets):
            fallback_model = fallback.model
            aside = _aside(fallback_model)
            if aside is not None:
                # Refused by its provider a moment ago (no access, unknown
                # model) or at a usage ceiling: asking again would only add a
                # failed call.
                if aside[1] == _CEILING:
                    ceilings[fallback_model] = aside[0]
                logger.debug("Fallback '{}' skipped: left aside ({})", fallback_model, aside[1])
                continue
            if has_streamed is not None and has_streamed[0]:
                can_hop = last_response is not None and self._should_fallback(
                    last_response
                )
                if can_hop:
                    logger.warning(
                        "Fallback model '{}' failed after content was emitted; "
                        "starting a new stream segment and trying next fallback",
                        self._fallback_presets[idx - 1].model if idx > 0 else primary_model,
                    )
                    has_streamed[0] = False
                    if on_stream_recover:
                        await on_stream_recover()
                    else:
                        kwargs["on_content_delta"] = None
                else:
                    break
            if idx == 0 and primary_skipped:
                logger.info(
                    "Primary model '{}' circuit open, trying fallback '{}'",
                    primary_model, fallback_model,
                )
            elif idx == 0:
                logger.info(
                    "Primary model '{}' failed: {}; trying fallback '{}'",
                    primary_model, primary_error, fallback_model,
                )
            else:
                logger.info(
                    "Fallback '{}' also failed, trying next fallback '{}'",
                    self._fallback_presets[idx - 1].model, fallback_model,
                )
            try:
                fallback_provider = self._provider_factory(fallback)
            except Exception as exc:
                logger.warning(
                    "Failed to create provider for fallback '{}': {}", fallback_model, exc
                )
                continue

            # Named in front of Working while it takes the step.
            await notify_relay(self._label(fallback_model))
            original_values = {
                name: kwargs.get(name, _MISSING)
                for name in ("model", "max_tokens", "temperature", "reasoning_effort")
            }
            kwargs["model"] = fallback_model
            kwargs["max_tokens"] = fallback.max_tokens
            kwargs["temperature"] = fallback.temperature
            # The effort in kwargs is this turn's routing decision (Agent
            # steps run at "none"). A fallback preset without its own value
            # inherits it rather than dropping it, otherwise the substitute
            # model reasons at its default while the primary was told not to.
            # An effort the user picked in the chat stays, whatever the preset.
            from navin.providers.relay_ladder import effort_chosen_in_chat

            if fallback.reasoning_effort is not None and not effort_chosen_in_chat():
                kwargs["reasoning_effort"] = fallback.reasoning_effort
            try:
                fallback_response = await self._call_model(
                    call, fallback_provider, kwargs, fallback_model,
                    streaming=has_streamed is not None,
                )
            finally:
                for name, value in original_values.items():
                    if value is _MISSING:
                        kwargs.pop(name, None)
                    else:
                        kwargs[name] = value

            if fallback_response.finish_reason != "error":
                logger.info(
                    "Fallback '{}' succeeded after primary '{}' failed",
                    fallback_model, primary_model,
                )
                self._relayed = True
                await self._announce_switch(primary_model, fallback_model)
                return fallback_response

            last_response = fallback_response
            refusal_s = circuit_cooldown_s(fallback_response)
            if refusal_s is not None:
                self._leave_aside(
                    fallback_model, fallback_response, refusal_s, ceilings, share_refusal=True
                )
            logger.warning(
                "Fallback '{}' also failed: {}",
                fallback_model,
                (fallback_response.content or "")[:120],
            )

        logger.warning(
            "All {} fallback model(s) failed",
            len(self._fallback_presets),
        )
        chain = [primary_model, *(fallback.model for fallback in self._fallback_presets)]
        if (
            ceilings
            and all(model in ceilings for model in chain)
            and not (last_response is not None and LLMProvider.is_arrearage_response(last_response))
        ):
            # Every model of the relay is at its ceiling: the turn stops on one
            # answer saying when the first comes back, and is not replayed.
            # An exhausted plan or key keeps its own answer (the quota card).
            return self._ceiling_response(ceilings, last_response)
        # Return the last error response we saw (primary or last fallback).
        if last_response is not None:
            return last_response
        # Primary was tripped and we have no fallbacks - synthesize an error.
        return LLMResponse(
            content=f"Primary model '{primary_model}' circuit open and no fallbacks available",
            finish_reason="error",
        )

    async def _call_primary_stickily(
        self,
        call: Callable[[LLMProvider, dict[str, Any]], Awaitable[LLMResponse]],
        kwargs: dict[str, Any],
        primary_model: str,
        has_streamed: list[bool] | None,
    ) -> LLMResponse:
        """Attempt the chosen model, retrying transient failures before switching.

        The budget is ``sticky_retries`` (two) for failures that clear in
        seconds and zero for definitive refusals, timeouts and provider-named
        waits longer than a sticky retry: there the next model is the only
        useful move. Each wait is capped at ``STICKY_MAX_DELAY_S``. Retries stop
        as soon as content has been streamed, since the turn is already partly
        delivered. A rate limit is not retried on the plan's ladder
        (``relay_on_limit``): the next model has its own quota. The outer
        ``chat_with_retry`` ladder does not multiply this: it gives a chain a
        single extra pass (see ``LLMProvider``).
        """
        import asyncio

        from navin.providers.fallback_policy import (
            STICKY_MAX_DELAY_S,
            retry_delay,
            sticky_retry_budget,
        )

        response = await self._call_model(
            call, self._primary, kwargs, primary_model,
            streaming=has_streamed is not None,
        )
        attempt = 0
        while response.finish_reason == "error":
            if has_streamed is not None and has_streamed[0]:
                return response
            budget = sticky_retry_budget(response, self._sticky_retries)
            if self._relay_on_limit and response.error_status_code == 429:
                budget = 0
            if attempt >= budget:
                return response
            attempt += 1
            delay = retry_delay(attempt, response, max_delay=STICKY_MAX_DELAY_S)
            logger.info(
                "Primary model '{}' unavailable ({}); retry {}/{} in {:.1f}s before "
                "switching to the next model",
                primary_model,
                (response.content or "")[:80],
                attempt,
                budget,
                delay,
            )
            if delay > 0:
                await asyncio.sleep(delay)
            response = await self._call_model(
                call, self._primary, kwargs, primary_model,
                streaming=has_streamed is not None,
            )
        return response

    @staticmethod
    def _should_fallback(response: LLMResponse) -> bool:
        from navin.providers.fallback_policy import should_switch_model

        return should_switch_model(response)
