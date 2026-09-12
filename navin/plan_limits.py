# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Enforce plan limits returned by the navin.live license API.

The site ships ``limits.stepsPerTask`` and ``limits.concurrentAgents`` with
every successful ``/api/license/validate``. This module stores them on the
local config and clamps agent-loop runtime settings so a Free / Plus /
Team seat never exceeds its paid entitlement.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from navin.agent.resources import governed_agent_count

# The Free entitlement, mirroring ``plans.ts``. Used when a subscription is
# refused (expired, past_due, revoked) and the live loop has to fall back
# without a fresh answer from the site.
FREE_CONCURRENT_AGENTS = 200
FREE_STEPS_PER_TASK = 30

# Licensed steps_per_task is a ceiling, not a target: 80 steps of 50k-token
# requests is an hour nobody asked for. The cap has to stay above what real
# work costs, though - a /forge that edits a few files and verifies twice is
# already ~20 steps, and cutting it mid-repair is slower than letting it
# finish, because the user just asks again. Runaway loops are stopped by the
# no-progress detector, not by this number.
DEFAULT_TURN_ITERATION_CAP = 24

# Probing cores and memory means reading a handful of small files. Cheap, but
# this runs on every limit lookup, including from the license sync thread, so
# the answer is held briefly. Short enough that a machine which just freed
# memory is not stuck with the old number for long.
_CAPACITY_TTL_S = 10.0
_capacity_lock = threading.Lock()
_capacity_cache: tuple[float, int, int | None] = (0.0, 0, None)
_last_logged_limit = 0


def measured_capacity(*, now: float | None = None) -> tuple[int, int | None]:
    """Usable cores and available bytes, re-probed at most every few seconds."""
    global _capacity_cache
    from navin.agent.resources import usable_cores, usable_memory_bytes

    stamp = now if now is not None else time.monotonic()
    with _capacity_lock:
        measured_at, cores, available = _capacity_cache
        if cores and (stamp - measured_at) < _CAPACITY_TTL_S:
            return cores, available
    cores = usable_cores()
    available = usable_memory_bytes()
    with _capacity_lock:
        _capacity_cache = (stamp, cores, available)
    return cores, available


def reset_capacity_cache() -> None:
    """Drop the memoised probe. For tests and for an explicit re-measure."""
    global _capacity_cache
    with _capacity_lock:
        _capacity_cache = (0.0, 0, None)


def govern_concurrent_agents(config: Any, ceiling: int) -> int:
    """Cap ``ceiling`` by what the machine can actually hold.

    A cap, never a grant: the governor can only lower the number a plan and the
    config already agreed on. Disabled, unmeasurable or misconfigured, it
    returns the ceiling untouched, which is the behaviour from before it
    existed.
    """
    ceiling = max(1, int(ceiling))
    resources = getattr(config, "resources", None)
    if resources is None or not getattr(resources, "enabled", True):
        return ceiling
    try:
        cores, available = measured_capacity()
        allowed = governed_agent_count(
            cores=cores,
            available_bytes=available,
            reserve_ratio=float(getattr(resources, "max_utilisation", 0.70)),
            agents_per_core=int(getattr(resources, "agents_per_core", 32)),
            memory_per_agent_mb=int(getattr(resources, "memory_per_agent_mb", 96)),
            minimum=int(getattr(resources, "min_concurrent_agents", 5)),
            ceiling=ceiling,
        )
        _log_governed_limit(allowed, ceiling, cores, available)
        return allowed
    except Exception:  # noqa: BLE001 - sizing must never break a spawn
        return ceiling


def _log_governed_limit(
    allowed: int, ceiling: int, cores: int, available: int | None
) -> None:
    """Say once why the limit is what it is.

    A limit lowered silently looks like a bug from the outside ("I configured
    200, I get 59"). Logged on change only, since this is re-evaluated on every
    lookup and would otherwise repeat forever.
    """
    global _last_logged_limit
    if allowed >= ceiling or allowed == _last_logged_limit:
        return
    _last_logged_limit = allowed
    try:
        from loguru import logger

        from navin.agent.resources import describe_capacity

        logger.info(
            "Concurrent agents governed to {} (configured {}): {}",
            allowed,
            ceiling,
            describe_capacity(cores=cores, available_bytes=available),
        )
    except Exception:  # noqa: BLE001 - a log line may not break sizing either
        pass


def parse_limits(payload: dict[str, Any] | None) -> dict[str, int]:
    """Extract positive integer limits from a validate/activate response."""
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("limits")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for src, dest in (
        ("stepsPerTask", "steps_per_task"),
        ("steps_per_task", "steps_per_task"),
        ("concurrentAgents", "concurrent_agents"),
        ("concurrent_agents", "concurrent_agents"),
        ("outputTokensPerCall", "output_tokens_per_call"),
        ("output_tokens_per_call", "output_tokens_per_call"),
    ):
        if src not in raw:
            continue
        try:
            value = int(raw[src])
        except (TypeError, ValueError):
            continue
        if value > 0:
            out[dest] = value
    return out


def store_limits_on_config(config: Any, limits: dict[str, int]) -> bool:
    """Persist plan limits on ``config.license``. Returns True if changed."""
    lic = getattr(config, "license", None)
    if lic is None:
        return False
    changed = False
    steps = limits.get("steps_per_task", 0)
    agents = limits.get("concurrent_agents", 0)
    output = limits.get("output_tokens_per_call", 0)
    if steps and getattr(lic, "steps_per_task", 0) != steps:
        lic.steps_per_task = steps
        changed = True
    if agents and getattr(lic, "concurrent_agents", 0) != agents:
        lic.concurrent_agents = agents
        changed = True
    if output and getattr(lic, "output_tokens_per_call", 0) != output:
        lic.output_tokens_per_call = output
        changed = True
    return changed


def apply_limits_from_response(config: Any, body: dict[str, Any]) -> bool:
    """Parse + store limits from a license API body."""
    return store_limits_on_config(config, parse_limits(body))


def clamp_steps_per_task(configured: int, plan_limit: int | None) -> int:
    """Cap tool iterations by the plan entitlement when present."""
    configured = max(1, int(configured))
    if not plan_limit or plan_limit <= 0:
        return configured
    return min(configured, int(plan_limit))


def clamp_concurrent_agents(configured: int, plan_limit: int | None) -> int:
    """Cap concurrent agents / parent turns by the plan entitlement."""
    configured = max(1, int(configured))
    if not plan_limit or plan_limit <= 0:
        return configured
    return min(configured, int(plan_limit))


def effective_steps_per_task(config: Any, configured: int | None = None) -> int:
    defaults = getattr(getattr(config, "agents", None), "defaults", None)
    base = (
        configured
        if configured is not None
        else int(getattr(defaults, "max_tool_iterations", 200) or 200)
    )
    plan_limit = int(getattr(getattr(config, "license", None), "steps_per_task", 0) or 0)
    return clamp_steps_per_task(base, plan_limit)


def effective_turn_iterations(configured: int, *, goal_active: bool) -> int:
    """Size one saved tool-loop slice within the plan entitlement.

    Sustained goals (create_goal / long missions) keep the full entitlement.
    Other interactive tasks resume at each boundary; this is not a limit on
    the total work needed to finish the request.
    """
    configured = max(1, int(configured))
    if goal_active:
        return configured
    return min(configured, DEFAULT_TURN_ITERATION_CAP)


def effective_concurrent_agents(config: Any, configured: int | None = None) -> int:
    """Configured value, capped by the plan, then by the machine itself.

    Both callers go through here (``AgentLoop.from_config`` at startup and
    ``apply_to_agent_loop`` on every license sync), so the governor applies
    everywhere without touching the schema default.
    """
    defaults = getattr(getattr(config, "agents", None), "defaults", None)
    base = (
        configured
        if configured is not None
        else int(getattr(defaults, "max_concurrent_subagents", 200) or 200)
    )
    plan_limit = int(getattr(getattr(config, "license", None), "concurrent_agents", 0) or 0)
    return govern_concurrent_agents(config, clamp_concurrent_agents(base, plan_limit))


def apply_to_agent_loop(loop: Any, config: Any) -> None:
    """Clamp a live AgentLoop (and its SubagentManager) to plan limits.

    The baseline is always re-read from the config, never from the live loop.
    Clamping used to take the loop's current value as the input and write the
    result back, so each sync could only ratchet the limits down: a seat that
    had been clamped to Free and then upgraded to Ultra stayed at the Free
    numbers until the process restarted.
    """
    steps = effective_steps_per_task(config)
    agents = effective_concurrent_agents(config)
    loop.max_iterations = steps
    if hasattr(loop, "subagents") and loop.subagents is not None:
        loop.subagents.max_iterations = steps
        loop.subagents.max_concurrent_subagents = agents
        # This runs on the license-sync thread every few minutes, which makes it
        # the periodic re-measure of the machine as well. A limit that just went
        # back up has to wake the queues, otherwise they wait for a subagent to
        # finish before noticing the room they were queued for.
        drain = getattr(loop.subagents, "request_drain", None)
        if callable(drain):
            try:
                drain()
            except Exception:  # noqa: BLE001 - a sync must never die on this
                pass
    # Parent-turn concurrency gate mirrors concurrentAgents.
    import asyncio
    import os

    env_cap = os.environ.get("NAVIN_MAX_CONCURRENT_REQUESTS")
    if env_cap is not None:
        try:
            env_n = int(env_cap)
        except ValueError:
            env_n = agents
        if env_n <= 0:
            loop._concurrency_gate = None
            return
        agents = min(agents, env_n)
    loop._concurrency_gate = asyncio.Semaphore(agents)
