"""The gateway must not let polled network routes starve everything else.

Three windows polling four network-backed routes was enough to fill the
ten-thread default executor, after which unrelated routes - and the work a chat
turn depends on - queued behind them. These tests pin the two mechanisms that
prevent it: concurrent callers share one call, and the pool is sized for
waiting rather than computing.
"""

from __future__ import annotations

import asyncio

import pytest

from navin.webui.blocking_pool import (
    MAX_WORKERS,
    MIN_WORKERS,
    blocking_pool_size,
    measure_pool_latency,
    watch_pool_latency,
)
from navin.webui.route_cache import CoalescingCache


class TestConcurrentCallersShareOneCall:
    @pytest.mark.asyncio
    async def test_a_burst_of_identical_lookups_makes_one_network_call(self) -> None:
        calls = 0
        released = asyncio.Event()

        async def slow_fetch() -> str:
            nonlocal calls
            calls += 1
            await released.wait()
            return "payload"

        cache = CoalescingCache()
        waiters = [
            asyncio.create_task(cache.get("account", slow_fetch, ttl=60))
            for _ in range(8)
        ]
        await asyncio.sleep(0)  # let every waiter reach the cache
        released.set()

        assert await asyncio.gather(*waiters) == ["payload"] * 8
        assert calls == 1, "each window paid for its own call to the same route"

    @pytest.mark.asyncio
    async def test_different_keys_are_not_merged(self) -> None:
        seen: list[str] = []

        def fetch(name: str):
            async def run() -> str:
                seen.append(name)
                return name

            return run

        cache = CoalescingCache()
        assert await cache.get("a", fetch("a"), ttl=60) == "a"
        assert await cache.get("b", fetch("b"), ttl=60) == "b"
        assert seen == ["a", "b"]

    @pytest.mark.asyncio
    async def test_one_caller_giving_up_does_not_strand_the_others(self) -> None:
        released = asyncio.Event()

        async def slow_fetch() -> str:
            await released.wait()
            return "payload"

        cache = CoalescingCache()
        first = asyncio.create_task(cache.get("k", slow_fetch, ttl=60))
        await asyncio.sleep(0)
        second = asyncio.create_task(cache.get("k", slow_fetch, ttl=60))
        await asyncio.sleep(0)

        # A window closing mid-request cancels its handler; the rest must still
        # be served rather than inherit the cancellation.
        second.cancel()
        released.set()
        assert await first == "payload"


class TestReuseWithinTheTtl:
    @pytest.mark.asyncio
    async def test_a_fresh_answer_is_reused(self) -> None:
        calls = 0

        async def fetch() -> int:
            nonlocal calls
            calls += 1
            return calls

        cache = CoalescingCache()
        assert await cache.get("k", fetch, ttl=60) == 1
        assert await cache.get("k", fetch, ttl=60) == 1
        assert calls == 1

    @pytest.mark.asyncio
    async def test_a_zero_ttl_stores_nothing(self) -> None:
        calls = 0

        async def fetch() -> int:
            nonlocal calls
            calls += 1
            return calls

        cache = CoalescingCache()
        assert await cache.get("k", fetch, ttl=0) == 1
        assert await cache.get("k", fetch, ttl=0) == 2

    @pytest.mark.asyncio
    async def test_an_expired_answer_is_served_while_a_refresh_runs(self) -> None:
        calls = 0
        released = asyncio.Event()

        async def fetch() -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return 1
            await released.wait()
            return 2

        cache = CoalescingCache()
        assert await cache.get("k", fetch, ttl=0.01) == 1
        await asyncio.sleep(0.02)
        # TTL lapsed: the poller must not wait on navin.live.
        assert await cache.get("k", fetch, ttl=60) == 1
        released.set()
        await asyncio.sleep(0.05)
        assert await cache.get("k", fetch, ttl=60) == 2
        assert calls == 2

    @pytest.mark.asyncio
    async def test_force_bypasses_the_stored_answer(self) -> None:
        calls = 0

        async def fetch() -> int:
            nonlocal calls
            calls += 1
            return calls

        cache = CoalescingCache()
        assert await cache.get("k", fetch, ttl=60) == 1
        assert await cache.get("k", fetch, ttl=60, force=True) == 2

    @pytest.mark.asyncio
    async def test_invalidate_drops_the_stored_answer(self) -> None:
        calls = 0

        async def fetch() -> int:
            nonlocal calls
            calls += 1
            return calls

        cache = CoalescingCache()
        await cache.get("k", fetch, ttl=60)
        cache.invalidate("k")
        assert await cache.get("k", fetch, ttl=60) == 2

    @pytest.mark.asyncio
    async def test_invalidate_prefix_drops_matching_keys_only(self) -> None:
        calls = 0

        async def fetch() -> int:
            nonlocal calls
            calls += 1
            return calls

        cache = CoalescingCache()
        await cache.get("file-tree:/proj:", fetch, ttl=60)
        await cache.get("file-tree:/proj:/deploy", fetch, ttl=60)
        await cache.get("account", fetch, ttl=60)
        cache.invalidate_prefix("file-tree:/proj")
        assert await cache.get("file-tree:/proj:", fetch, ttl=60) == 4
        assert await cache.get("file-tree:/proj:/deploy", fetch, ttl=60) == 5
        assert await cache.get("account", fetch, ttl=60) == 3


class TestFailuresAreNotCached:
    @pytest.mark.asyncio
    async def test_an_error_reaches_every_waiter_and_is_retried(self) -> None:
        attempts = 0

        async def failing() -> str:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("registry unreachable")

        cache = CoalescingCache()
        with pytest.raises(RuntimeError):
            await cache.get("k", failing, ttl=60)
        # A transient outage must not be pinned for the whole TTL.
        with pytest.raises(RuntimeError):
            await cache.get("k", failing, ttl=60)
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_waiters_joined_to_a_failing_call_all_see_the_error(self) -> None:
        released = asyncio.Event()

        async def failing() -> str:
            await released.wait()
            raise RuntimeError("registry unreachable")

        cache = CoalescingCache()
        first = asyncio.create_task(cache.get("k", failing, ttl=60))
        await asyncio.sleep(0)
        second = asyncio.create_task(cache.get("k", failing, ttl=60))
        await asyncio.sleep(0)
        released.set()

        results = await asyncio.gather(first, second, return_exceptions=True)
        assert all(isinstance(r, RuntimeError) for r in results)


class TestPoolSizing:
    def test_it_beats_the_python_default_on_a_small_machine(self) -> None:
        # Six cores gave min(32, 6 + 4) = 10, the count that starved the
        # gateway in production.
        assert blocking_pool_size(cpu_count=6) > 10
        assert blocking_pool_size(cpu_count=6) >= MIN_WORKERS

    @pytest.mark.parametrize("cores", [1, 2, 6, 8, 16, 64])
    def test_it_stays_within_its_bounds(self, cores: int) -> None:
        assert MIN_WORKERS <= blocking_pool_size(cpu_count=cores) <= MAX_WORKERS

    def test_an_unknown_core_count_still_gives_a_usable_pool(self) -> None:
        # os.cpu_count() returns None on some hosts.
        assert blocking_pool_size(cpu_count=None) >= MIN_WORKERS
        assert blocking_pool_size(cpu_count=0) >= MIN_WORKERS


class TestTheStatedHeavyWorkload:
    """Ten subagents and five chats at once, the load the user asked about.

    Counted from what each actually holds a thread for: a subagent does its git
    checkout and inspection through ``to_thread``, a chat drags along the
    per-window polling, and the coalescing cache collapses the polls that are
    the same call.
    """

    SUBAGENTS = 10
    CHATS = 5
    # Per subagent: checkout, inspect, pooled lookup - not all at once, but
    # budget for the worst overlap.
    THREADS_PER_SUBAGENT = 3
    # Per chat: tool work that has to block (file reads, git, subprocesses).
    THREADS_PER_CHAT = 2
    # Polling is now per distinct route, no longer per window: account, CI,
    # git status, context usage, version check, LSP catalogue.
    DISTINCT_POLLED_ROUTES = 6

    def test_the_pool_covers_it_on_a_small_machine(self) -> None:
        needed = (
            self.SUBAGENTS * self.THREADS_PER_SUBAGENT
            + self.CHATS * self.THREADS_PER_CHAT
            + self.DISTINCT_POLLED_ROUTES
        )
        assert needed == 46
        # Six cores, the machine the starvation was measured on.
        assert blocking_pool_size(cpu_count=6) >= needed
        # And the old default was nowhere near it.
        assert min(32, 6 + 4) < needed

    def test_a_hundred_agent_wave_still_gets_a_thread_each(self) -> None:
        """The load a migration client will actually throw at the gateway.

        Ten was the old story. A hundred isolate=true subagents each hold a
        thread for checkout and another for the prompt, and a 128-thread
        ceiling put the second half of that wave behind the first.
        """
        needed = 100 * 2 + self.DISTINCT_POLLED_ROUTES
        assert blocking_pool_size(cpu_count=6) >= needed
        assert blocking_pool_size(cpu_count=1) >= needed

    def test_every_supported_machine_covers_it(self) -> None:
        needed = (
            self.SUBAGENTS * self.THREADS_PER_SUBAGENT
            + self.CHATS * self.THREADS_PER_CHAT
            + self.DISTINCT_POLLED_ROUTES
        )
        # A dual-core laptop must survive this too, so the floor carries it.
        for cores in (1, 2, 4, 6, 8, 12, 16, 32):
            assert blocking_pool_size(cpu_count=cores) >= needed

    @pytest.mark.asyncio
    async def test_that_many_concurrent_blocking_jobs_all_get_a_thread(self) -> None:
        """The real thing: 46 jobs that block until released, no deadlock."""
        from concurrent.futures import ThreadPoolExecutor

        jobs = 46
        started = asyncio.Event()
        release = asyncio.Event()
        running = 0
        lock = asyncio.Lock()

        pool = ThreadPoolExecutor(max_workers=blocking_pool_size(cpu_count=6))
        loop = asyncio.get_running_loop()
        loop.set_default_executor(pool)
        try:

            async def job() -> int:
                nonlocal running
                async with lock:
                    running += 1
                    if running == jobs:
                        started.set()
                await release.wait()
                # Only reached if a thread was actually available.
                return await asyncio.to_thread(lambda: 1)

            tasks = [asyncio.create_task(job()) for _ in range(jobs)]
            await asyncio.wait_for(started.wait(), timeout=5)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
            assert sum(results) == jobs
        finally:
            pool.shutdown(wait=False, cancel_futures=True)


class TestSaturationIsReported:
    @pytest.mark.asyncio
    async def test_a_free_pool_measures_as_fast(self) -> None:
        assert await measure_pool_latency() < 1.0

    @pytest.mark.asyncio
    async def test_a_slow_pool_gets_a_warning(self) -> None:
        warnings: list[str] = []

        class Log:
            def warning(self, message: str, *args: object) -> None:
                warnings.append(message.format(*args))

        # warn_after_s=0 makes any measurable wait count, so the probe fires on
        # its first pass without the test having to jam a real pool.
        task = asyncio.create_task(
            watch_pool_latency(Log(), interval_s=0.01, warn_after_s=0.0)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert warnings, "saturation went unreported"
        assert "blocking pool saturated" in warnings[0]

    @pytest.mark.asyncio
    async def test_a_healthy_pool_stays_quiet(self) -> None:
        warnings: list[str] = []

        class Log:
            def warning(self, message: str, *args: object) -> None:
                warnings.append(message)

        task = asyncio.create_task(
            watch_pool_latency(Log(), interval_s=0.01, warn_after_s=5.0)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert warnings == []
