"""Provider wrapper that transparently fails over to fallback models on error."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from navin.providers.base import LLMProvider, LLMResponse
from navin.providers.fallback_policy import circuit_cooldown_s

# Circuit breaker tuned to match OpenAICompatProvider's Responses API breaker.
_PRIMARY_FAILURE_THRESHOLD = 3
_PRIMARY_COOLDOWN_S = 60
_MISSING = object()

#: Extra attempts on the chosen model before the switch. The operator's rule:
#: a model that blocks is asked again twice, then the next model of the list
#: takes the step. Only failures that clear in seconds use the budget; a
#: definitive refusal or a timeout switches at once (fallback_policy).
PRIMARY_STICKY_RETRIES = 2


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
    ):
        self._primary = primary
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

        if self._primary_available():
            primary_was_attempted = True
            response = await self._call_primary_stickily(
                call, kwargs, primary_model, has_streamed
            )
            if response.finish_reason != "error":
                self._primary_failures = 0
                self._primary_tripped_at = None
                self._primary_cooldown_s = _PRIMARY_COOLDOWN_S
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

            self._primary_failures += 1
            cooldown = circuit_cooldown_s(response)
            if cooldown is not None:
                self._open_primary_circuit(
                    primary_model,
                    cooldown,
                    "provider asked to come back later"
                    if getattr(response, "error_retry_after_s", None)
                    or getattr(response, "retry_after", None)
                    else "account out of credit",
                )
            elif self._primary_failures >= _PRIMARY_FAILURE_THRESHOLD:
                self._open_primary_circuit(
                    primary_model,
                    _PRIMARY_COOLDOWN_S,
                    f"{self._primary_failures} consecutive failures",
                )
        else:
            logger.debug("Primary model '{}' circuit open; skipping", primary_model)

        last_response: LLMResponse | None = None
        primary_skipped = not primary_was_attempted
        for idx, fallback in enumerate(self._fallback_presets):
            fallback_model = fallback.model
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
            if fallback.reasoning_effort is not None:
                kwargs["reasoning_effort"] = fallback.reasoning_effort
            try:
                fallback_response = await call(fallback_provider, kwargs)
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
                await self._announce_switch(primary_model, fallback_model)
                return fallback_response

            last_response = fallback_response
            logger.warning(
                "Fallback '{}' also failed: {}",
                fallback_model,
                (fallback_response.content or "")[:120],
            )

        logger.warning(
            "All {} fallback model(s) failed",
            len(self._fallback_presets),
        )
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
        delivered. The outer ``chat_with_retry`` ladder does not multiply this:
        it gives a chain a single extra pass (see ``LLMProvider``).
        """
        import asyncio

        from navin.providers.fallback_policy import (
            STICKY_MAX_DELAY_S,
            retry_delay,
            sticky_retry_budget,
        )

        response = await call(self._primary, kwargs)
        attempt = 0
        while response.finish_reason == "error":
            if has_streamed is not None and has_streamed[0]:
                return response
            budget = sticky_retry_budget(response, self._sticky_retries)
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
            response = await call(self._primary, kwargs)
        return response

    @staticmethod
    def _should_fallback(response: LLMResponse) -> bool:
        from navin.providers.fallback_policy import should_switch_model

        return should_switch_model(response)
