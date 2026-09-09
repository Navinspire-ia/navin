# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A stalled route must name the thread it is waiting on, while it waits.

"slow webui http route ... duration_ms=70946" was the whole story the log had
for a minute during which every panel showed the same timeout. These tests pin
the two probes that turn that into a cause: a per-route stack snapshot past the
client's timeout, and a loop-lag watcher that sees a blocked event loop from a
helper thread.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from navin.webui.stall_watch import LoopLagMonitor, StallReporter, thread_snapshot


class _Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message.format(*args))


def _hold_here(gate: threading.Event, ready: threading.Event) -> None:
    ready.set()
    gate.wait(timeout=5)


class TestThreadSnapshot:
    def test_a_thread_waiting_in_our_code_is_listed_innermost_line_first(self) -> None:
        gate = threading.Event()
        ready = threading.Event()
        worker = threading.Thread(
            target=_hold_here, args=(gate, ready), name="stuck-worker", daemon=True
        )
        worker.start()
        try:
            assert ready.wait(timeout=2)
            body = thread_snapshot(interesting=lambda module: module == __name__)
        finally:
            gate.set()
            worker.join(timeout=2)
        assert "stuck-worker" in body
        lines = body.splitlines()
        # The call the thread is actually blocked on comes first (the Event
        # wait), and the line of ours that made it is right behind, not main().
        assert "wait" in lines[1]
        assert any("_hold_here" in line for line in lines[1:4])

    def test_threads_outside_our_code_are_left_out(self) -> None:
        body = thread_snapshot(interesting=lambda module: module == "nope.never")
        assert body == "(no thread is inside navin code)"

    def test_the_asking_thread_can_be_skipped(self) -> None:
        body = thread_snapshot(
            interesting=lambda module: module == __name__,
            skip_ident=threading.get_ident(),
        )
        assert "(no thread is inside navin code)" == body


class TestStallReporter:
    @pytest.mark.asyncio
    async def test_a_route_past_the_deadline_gets_one_snapshot(self) -> None:
        log = _Log()
        reporter = StallReporter(
            log, dump_after_s=0.02, min_interval_s=60, snapshot=lambda: "STACKS"
        )

        async def slow() -> str:
            await asyncio.sleep(0.08)
            return "ok"

        assert await reporter.watch("/api/webui/skills", slow()) == "ok"
        assert len(log.warnings) == 1
        assert "/api/webui/skills" in log.warnings[0]
        assert "STACKS" in log.warnings[0]

    @pytest.mark.asyncio
    async def test_a_fast_route_is_not_reported(self) -> None:
        log = _Log()
        reporter = StallReporter(log, dump_after_s=0.2, snapshot=lambda: "STACKS")

        async def fast() -> int:
            return 1

        assert await reporter.watch("/api/x", fast()) == 1
        await asyncio.sleep(0.25)
        assert log.warnings == []

    @pytest.mark.asyncio
    async def test_a_burst_of_stalled_routes_shares_one_snapshot(self) -> None:
        log = _Log()
        reporter = StallReporter(
            log, dump_after_s=0.02, min_interval_s=60, snapshot=lambda: "STACKS"
        )

        async def slow() -> None:
            await asyncio.sleep(0.06)

        await asyncio.gather(*(reporter.watch(f"/api/r{i}", slow()) for i in range(5)))
        assert len(log.warnings) == 1, "same stall, five identical dumps"

    @pytest.mark.asyncio
    async def test_the_route_result_and_errors_pass_through(self) -> None:
        reporter = StallReporter(_Log(), dump_after_s=10)

        async def failing() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await reporter.watch("/api/x", failing())

    @pytest.mark.asyncio
    async def test_a_snapshot_failure_never_breaks_the_route(self) -> None:
        log = _Log()

        def broken() -> str:
            raise ValueError("no frames")

        reporter = StallReporter(log, dump_after_s=0.01, snapshot=broken)

        async def slow() -> str:
            await asyncio.sleep(0.05)
            return "served"

        assert await reporter.watch("/api/x", slow()) == "served"
        assert "snapshot failed" in log.warnings[0]


class TestGatewayWiring:
    def test_every_webui_route_runs_under_the_reporter(self) -> None:
        from pathlib import Path

        import navin.webui.ws_http as ws_http

        source = Path(ws_http.__file__).read_text(encoding="utf-8")
        assert "self._stalls = StallReporter(log)" in source
        assert "await self._stalls.watch(" in source

    def test_only_the_computing_caller_holds_a_heavy_permit(self) -> None:
        """Waiters on a coalesced result must not pin the shared semaphore.

        Four windows waiting on one skills scan used to hold all four permits,
        so the sessions list queued behind a result it did not need.
        """
        from pathlib import Path

        import navin.webui.ws_http as ws_http

        source = Path(ws_http.__file__).read_text(encoding="utf-8")
        assert source.count("lambda: self._gated(") >= 2
        assert "async with self._heavy_routes:\n            payload = await self.route_cache.get(" not in source

    def test_the_channel_watches_its_own_loop(self) -> None:
        from pathlib import Path

        import navin.channels.websocket as channel

        source = Path(channel.__file__).read_text(encoding="utf-8")
        assert "self._loop_lag = LoopLagMonitor(self.logger)" in source
        assert "await lag.stop()" in source


class TestLoopLagMonitor:
    @pytest.mark.asyncio
    async def test_a_blocked_loop_is_seen_from_the_side_and_measured_after(self) -> None:
        log = _Log()
        monitor = LoopLagMonitor(log, warn_after_s=0.1, poll_s=0.02, min_interval_s=0)
        monitor.start()
        try:
            await asyncio.sleep(0.05)  # let the heartbeat settle
            time.sleep(0.4)  # synchronous work on the loop: the bug we hunt
            await asyncio.sleep(0.05)
        finally:
            await monitor.stop()
        side = [w for w in log.warnings if "so far; main thread is in" in w]
        after = [w for w in log.warnings if "event loop was blocked for" in w]
        assert side, "the helper thread never noticed the block"
        assert "time.sleep" in side[0] or "test_stall_watch" in side[0]
        assert after, "the resumed loop did not report how long it was gone"

    @pytest.mark.asyncio
    async def test_a_responsive_loop_stays_quiet(self) -> None:
        log = _Log()
        monitor = LoopLagMonitor(log, warn_after_s=0.2, poll_s=0.02)
        monitor.start()
        try:
            await asyncio.sleep(0.15)
        finally:
            await monitor.stop()
        assert log.warnings == []
