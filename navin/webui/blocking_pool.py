# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""How many threads the gateway keeps for blocking work, and a way to notice
when that number stops being enough.

Everything the gateway pushes off the event loop is waiting on something else:
navin.live for the plan, `gh` for CI, the registry for LSP servers, git for a
checkout or a status. Python's default executor is sized ``min(32, cpu_count +
4)`` - ten threads on six cores - which is a rule for CPU-bound work applied to
work that is almost entirely idle waiting. Ten was low enough that four polled
routes could hold them all, after which every later ``to_thread`` queued,
including the ones a chat turn or a subagent needed.

Sizing here is deliberately generous, for two reasons. A thread parked on a
socket costs a stack and nothing else, and ``ThreadPoolExecutor`` only creates
a thread when there is work for it, so a high ceiling is free while idle: the
pool grows to demand and no further.

A number chosen today can still be wrong tomorrow, so :func:`watch_pool_latency`
keeps checking. It is the difference between a user reporting "it is slow" and
the log saying which resource ran out.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

# Floor: enough for a heavy session - several chats, a batch of subagents, and
# the polling every open window does - without the pool ever being the limit.
MIN_WORKERS = 48
# Ceiling: a full agent wave (200) each holding a thread for checkout / prompt
# build, plus the gateway's own polls. Past this, a full queue means something
# is stuck rather than busy, and more threads would only hide it.
MAX_WORKERS = 256
# Threads per core. High on purpose: this pool waits on the network and on
# child processes, so cores are not the resource being shared.
WORKERS_PER_CORE = 16
# Extra threads beyond the agent ceiling so gateway polls still get a slot
# while a wave is materialising its checkouts.
_AGENT_WAVE_HEADROOM = 32
_DEFAULT_AGENT_CEILING = 200

# How often to check that the pool still starts work promptly, and how long a
# no-op may wait before that silence is worth a line in the log.
POOL_LATENCY_PROBE_INTERVAL_S = 30.0
POOL_LATENCY_WARN_S = 1.0


def blocking_pool_size(
    cpu_count: int | None = None,
    *,
    concurrent_agents: int = _DEFAULT_AGENT_CEILING,
) -> int:
    """Worker count for the gateway's default executor.

    Sized for the larger of "cores times waiters-per-core" and "a full agent
    wave plus headroom". The second term is the one that used to be missing:
    100 isolate=true subagents each call ``to_thread`` for their checkout and
    their prompt, and a 128-thread ceiling put the second half of the wave
    behind the first, which the IDE read as agents that had started and then
    gone silent.
    """
    cores = cpu_count if cpu_count is not None else os.cpu_count()
    if not cores or cores < 1:
        cores = 4
    from_cores = cores * WORKERS_PER_CORE
    from_agents = max(1, int(concurrent_agents)) + _AGENT_WAVE_HEADROOM
    return max(MIN_WORKERS, min(MAX_WORKERS, max(from_cores, from_agents)))


async def measure_pool_latency() -> float:
    """Seconds a trivial job waits before a pool thread picks it up.

    Near zero while threads are free. Once every worker is busy the wait is
    however long the slowest job in front still has to run, which is exactly
    the delay every chat turn and subagent is also paying.
    """
    started = time.perf_counter()
    await asyncio.to_thread(lambda: None)
    return time.perf_counter() - started


async def watch_pool_latency(
    log: Any,
    *,
    interval_s: float = POOL_LATENCY_PROBE_INTERVAL_S,
    warn_after_s: float = POOL_LATENCY_WARN_S,
) -> None:
    """Log a warning whenever the blocking pool stops answering promptly.

    Saturation used to be invisible: it surfaced as unrelated routes all going
    slow at the same second, which took a log-forensics session to read. This
    names the cause as it happens.
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            waited = await measure_pool_latency()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a probe must never kill the gateway
            continue
        if waited >= warn_after_s:
            log.warning(
                "blocking pool saturated: a no-op job waited {:.1f}s for a free "
                "thread (pool size {}). Chat turns and subagents are queueing "
                "behind it.",
                waited,
                blocking_pool_size(),
            )
