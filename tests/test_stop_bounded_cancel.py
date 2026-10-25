"""/stop must not hang the inbound reader on a task that unwinds slowly."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from navin.agent.loop import AgentLoop


class _Subs:
    def __init__(self, delay: float = 0.0, count: int = 0) -> None:
        self.delay, self.count = delay, count

    async def cancel_by_session(self, key: str) -> int:
        await asyncio.sleep(self.delay)
        return self.count


def _loop(tasks, subs) -> SimpleNamespace:
    return SimpleNamespace(_active_tasks={"k": tasks}, subagents=subs, _STOP_GRACE_S=0.2)


def test_cancel_counts_normal_tasks_and_subagents() -> None:
    async def run() -> int:
        t = asyncio.create_task(asyncio.sleep(10))
        await asyncio.sleep(0)
        n = await AgentLoop._cancel_active_tasks(_loop([t], _Subs(count=2)), "k")
        assert t.cancelled()
        return n

    assert asyncio.run(run()) == 3


def test_task_swallowing_cancel_does_not_block_stop() -> None:
    async def stubborn() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(10)  # swallows the first cancel

    async def run() -> float:
        t = asyncio.create_task(stubborn())
        await asyncio.sleep(0)
        start = time.monotonic()
        await AgentLoop._cancel_active_tasks(_loop([t], _Subs()), "k")
        elapsed = time.monotonic() - start
        await asyncio.sleep(0.05)
        assert t.done()  # the second cancel reached it
        return elapsed

    assert asyncio.run(run()) < 1.0


def test_slow_subagent_cancel_is_bounded() -> None:
    async def run() -> tuple[int, float]:
        start = time.monotonic()
        n = await AgentLoop._cancel_active_tasks(_loop([], _Subs(delay=5, count=4)), "k")
        return n, time.monotonic() - start

    n, elapsed = asyncio.run(run())
    assert n == 0 and elapsed < 1.0
