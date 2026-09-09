# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Where a turn actually spends its wall clock.

A slow turn has three candidate culprits and no way to tell them apart from
the outside: the model call, the tools, or the work the loop does between
them (context governance re-encodes history every iteration). Guessing has
cost real time here, so the loop measures instead.

One INFO line per turn, plus a DEBUG line per iteration. Cost is a handful
of ``perf_counter`` reads, so this stays on in production: a turn nobody
measured is a turn nobody can speed up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from loguru import logger

# Phases of a single iteration. "other" is everything the loop itself does:
# hook callbacks, checkpoint emission, message bookkeeping.
PHASE_CONTEXT = "context"
PHASE_MODEL = "model"
PHASE_TOOLS = "tools"


def _ms(seconds: float) -> int:
    return int(seconds * 1000)


@dataclass
class TurnTiming:
    """Accumulates per-phase durations for one agent turn."""

    session_key: str | None = None
    model: str | None = None
    started: float = field(default_factory=time.perf_counter)
    iterations: int = 0
    phases: dict[str, float] = field(
        default_factory=lambda: {PHASE_CONTEXT: 0.0, PHASE_MODEL: 0.0, PHASE_TOOLS: 0.0}
    )
    # Per-tool totals, so one pathological tool cannot hide inside "tools".
    tools: dict[str, float] = field(default_factory=dict)
    tool_calls: dict[str, int] = field(default_factory=dict)
    slowest_model_call: float = 0.0
    # Prefill is what a multi-step turn pays over and over. Without a cache
    # hit, step 20 re-reads the whole conversation at full price, which is
    # the difference between a two-minute turn and an hour.
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    # Reasoning tokens are generated at normal speed and never shown, so they
    # are pure silence to the user. A step routed to "none" that still
    # reports them is a leak: the routing decision did not reach the wire.
    reasoning_tokens: int = 0
    leaked_reasoning_tokens: int = 0
    leaked_steps: int = 0

    def add(self, phase: str, seconds: float) -> None:
        self.phases[phase] = self.phases.get(phase, 0.0) + seconds
        if phase == PHASE_MODEL and seconds > self.slowest_model_call:
            self.slowest_model_call = seconds

    def add_tool(self, name: str, seconds: float) -> None:
        self.tools[name] = self.tools.get(name, 0.0) + seconds
        self.tool_calls[name] = self.tool_calls.get(name, 0) + 1

    def add_usage(
        self,
        usage: dict[str, int] | None,
        requested_effort: str | None = None,
    ) -> None:
        if not usage:
            return
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.cached_tokens += int(usage.get("cached_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        reasoning = int(usage.get("reasoning_tokens") or 0)
        self.reasoning_tokens += reasoning
        if reasoning > 0 and (requested_effort or "").lower() == "none":
            self.leaked_reasoning_tokens += reasoning
            self.leaked_steps += 1

    @property
    def reasoning_leaked(self) -> bool:
        return self.leaked_reasoning_tokens > 0

    @property
    def cache_hit_ratio(self) -> float | None:
        """Share of prefill served from cache, or None when unreported."""
        if self.prompt_tokens <= 0:
            return None
        return self.cached_tokens / self.prompt_tokens

    def end_iteration(self) -> None:
        self.iterations += 1

    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def summary(self) -> str:
        total = self.elapsed()
        model = self.phases.get(PHASE_MODEL, 0.0)
        tools = self.phases.get(PHASE_TOOLS, 0.0)
        context = self.phases.get(PHASE_CONTEXT, 0.0)
        other = max(0.0, total - model - tools - context)
        parts = [
            f"total={_ms(total)}ms",
            f"model={_ms(model)}ms",
            f"tools={_ms(tools)}ms",
            f"context={_ms(context)}ms",
            f"other={_ms(other)}ms",
            f"steps={self.iterations}",
        ]
        if self.iterations:
            parts.append(f"model_avg={_ms(model / self.iterations)}ms")
        if self.slowest_model_call:
            parts.append(f"model_max={_ms(self.slowest_model_call)}ms")
        if self.prompt_tokens:
            parts.append(f"prefill={self.prompt_tokens}tok")
            ratio = self.cache_hit_ratio
            if ratio is not None:
                parts.append(f"cached={self.cached_tokens}tok ({ratio:.0%})")
        if self.completion_tokens:
            parts.append(f"generated={self.completion_tokens}tok")
            if model > 0:
                parts.append(f"gen_rate={self.completion_tokens / model:.0f}tok/s")
        if self.reasoning_tokens:
            parts.append(f"reasoning={self.reasoning_tokens}tok")
        if self.reasoning_leaked:
            parts.append(
                f"REASONING LEAK={self.leaked_reasoning_tokens}tok over "
                f"{self.leaked_steps} step(s) routed to none"
            )
        top = sorted(self.tools.items(), key=lambda kv: kv[1], reverse=True)[:3]
        if top:
            detail = " ".join(
                f"{name}={_ms(spent)}ms/{self.tool_calls.get(name, 0)}"
                for name, spent in top
            )
            parts.append(f"| slowest tools: {detail}")
        return " ".join(parts)

    def log(self) -> None:
        """One line per turn. Never raises: a ruler must not break the turn."""
        try:
            who = f"[{self.session_key or 'default'}/{self.model or '?'}]"
            if self.reasoning_leaked:
                # The one symptom worth a warning: the model thought on steps
                # where navin asked it not to, so a provider default won.
                logger.warning("{} turn timing: {}", who, self.summary())
            else:
                logger.info("{} turn timing: {}", who, self.summary())
        except Exception:  # pragma: no cover - defensive
            logger.debug("turn timing log failed", exc_info=True)


class measure:
    """Context manager charging its wall time to one phase of the turn."""

    __slots__ = ("_timing", "_phase", "_started")

    def __init__(self, timing: TurnTiming | None, phase_name: str) -> None:
        self._timing = timing
        self._phase = phase_name
        self._started = 0.0

    def __enter__(self) -> "measure":
        self._started = time.perf_counter()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._timing is not None:
            self._timing.add(self._phase, time.perf_counter() - self._started)
