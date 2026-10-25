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

    def test_a_loop_idle_in_its_selector_is_not_a_freeze(self) -> None:
        import selectors
        import socket
        import sys

        from navin.webui.stall_watch import idle_in_selector

        def frame_of(target) -> tuple[object, threading.Thread]:
            ready = threading.Event()
            thread = threading.Thread(target=target, args=(ready,), daemon=True)
            thread.start()
            ready.wait(1)
            time.sleep(0.05)
            return sys._current_frames()[thread.ident], thread

        def waits_for_io(ready: threading.Event) -> None:
            reader, writer = socket.socketpair()
            with selectors.DefaultSelector() as selector:
                selector.register(reader, selectors.EVENT_READ)
                ready.set()
                selector.select(0.5)
            reader.close()
            writer.close()

        def busy(ready: threading.Event) -> None:
            ready.set()
            end = time.monotonic() + 0.5
            while time.monotonic() < end:
                sum(range(1000))

        frame, thread = frame_of(waits_for_io)
        assert idle_in_selector(frame)
        thread.join()
        frame, thread = frame_of(busy)
        assert not idle_in_selector(frame)
        thread.join()
        assert not idle_in_selector(None)

    def test_a_running_collection_is_named_and_a_slow_full_one_reported(self) -> None:
        from navin.webui.stall_watch import GcClock

        seen: list[float] = []
        clock = GcClock(slow_full_s=1.0, on_slow_full=seen.append)
        assert clock.describe() == ""
        clock("start", {"generation": 2})
        clock.started -= 2.0
        assert "garbage collection of generation 2 running for 2.0s" in clock.describe()
        clock("stop", {"generation": 2, "collected": 0, "uncollectable": 0})
        assert clock.describe() == ""
        assert len(seen) == 1 and seen[0] >= 2.0
        # A fast full collection or a young one asks for nothing.
        clock("start", {"generation": 2})
        clock("stop", {"generation": 2})
        clock("start", {"generation": 0})
        clock.started -= 5.0
        clock("stop", {"generation": 0})
        assert len(seen) == 1

    @pytest.mark.asyncio
    async def test_a_slow_full_collection_runs_the_reaction_on_the_loop_once(self) -> None:
        import gc

        calls: list[int] = []
        monitor = LoopLagMonitor(
            _Log(), warn_after_s=5, poll_s=0.02,
            after_slow_full_gc=lambda: calls.append(threading.get_ident()), slow_full_gc_s=0.0,
        )
        monitor.start()
        try:
            gc.collect()
            gc.collect()
            await asyncio.sleep(0.05)
        finally:
            await monitor.stop()
        # On the loop thread, and once per interval however often it fires.
        assert calls == [threading.get_ident()]
        assert monitor._gc not in gc.callbacks


class TestCliFreezeWatch:
    def test_cli_freeze_is_written_to_the_freeze_log(self, tmp_path):
        from navin.tui.runtime import _FileLog

        path = tmp_path / "cli-freeze.log"

        async def run() -> None:
            with open(path, "a", encoding="utf-8", buffering=1) as handle:
                monitor = LoopLagMonitor(_FileLog(handle), warn_after_s=0.1, poll_s=0.02, min_interval_s=0)
                monitor.start()
                await asyncio.sleep(0.05)
                time.sleep(0.4)  # the kind of call that froze the CLI
                await asyncio.sleep(0.05)
                await monitor.stop()

        asyncio.run(run())
        text = path.read_text(encoding="utf-8")
        assert "event loop blocked for" in text
        assert "test_cli_freeze_is_written_to_the_freeze_log" in text

    def test_the_cli_never_freezes_live_widgets_after_a_slow_collection(self, tmp_path, monkeypatch):
        # gc.freeze() after a slow full collection kept the widgets alive at
        # that moment out of the collector for good: once windowed out of the
        # chat they leaked (2.2 M objects after 30 probe turns).
        import faulthandler
        from types import SimpleNamespace

        from navin.tui.runtime import TuiRuntime

        monkeypatch.setattr("navin.config.paths.get_logs_dir", lambda: tmp_path)
        monkeypatch.setattr(faulthandler, "enable", lambda *args, **kwargs: None)
        host = SimpleNamespace()

        async def run() -> None:
            TuiRuntime._start_freeze_watch(host)
            try:
                assert host._loop_lag._after_slow_full_gc is None
            finally:
                await host._loop_lag.stop()
                host._freeze_file.close()

        asyncio.run(run())
        assert (tmp_path / "cli-freeze.log").exists()

    def test_dream_commit_runs_off_the_event_loop(self):
        import inspect

        from navin.cli import commands
        from navin.command import builtin

        assert "await asyncio.to_thread(_commit_dream_changes, store)" in inspect.getsource(commands)
        assert "await asyncio.to_thread(store.git.auto_commit, commit_msg)" in inspect.getsource(builtin)

    def test_open_url_routes_do_not_block_the_loop(self):
        import inspect

        from navin.webui import ws_http

        source = inspect.getsource(ws_http)
        assert "opened = open_external_url(" not in source
        assert source.count("await asyncio.to_thread(open_external_url,") == 3


def test_cron_timer_store_io_runs_off_the_event_loop(tmp_path) -> None:
    """A slow store save (WSL /mnt/c) froze the gateway for 36.9 s: tick I/O must use a thread."""
    import asyncio
    import threading

    from navin.cron.service import CronService

    service = CronService(tmp_path / "jobs.json")
    loop_thread = threading.get_ident()
    seen: list[int] = []
    service._load_store = lambda: seen.append(threading.get_ident()) or service._store  # type: ignore[method-assign]
    service._save_store = lambda: seen.append(threading.get_ident())  # type: ignore[method-assign]
    service._arm_timer = lambda: None  # type: ignore[method-assign]
    from navin.cron.types import CronStore

    service._store = CronStore()
    asyncio.run(service._on_timer())
    assert len(seen) == 2
    assert all(ident != loop_thread for ident in seen)


def test_startup_heap_is_frozen_once_and_stacks_are_frequent() -> None:
    import gc

    from navin.webui import stall_watch as sw

    assert sw.LOOP_LAG_MIN_INTERVAL_S <= 5.0
    saved = sw._STARTUP_HEAP_FROZEN
    sw._STARTUP_HEAP_FROZEN = False
    try:
        assert sw.freeze_startup_heap() is True
        assert gc.get_freeze_count() > 0
        assert sw.freeze_startup_heap() is False, "only once per process"
    finally:
        gc.unfreeze()
        sw._STARTUP_HEAP_FROZEN = saved


def test_settings_route_builds_its_payload_off_the_event_loop() -> None:
    import asyncio
    import threading
    from types import SimpleNamespace
    from unittest import mock

    from navin.webui import settings_routes as sr

    owner = next(
        obj for obj in vars(sr).values()
        if isinstance(obj, type) and "_handle_settings" in vars(obj)
    )
    threads: list[str] = []

    def fake_payload(**kwargs):
        threads.append(threading.current_thread().name)
        return {"ok": True}

    fake = SimpleNamespace(
        _authorized=lambda request: True,
        _query=lambda request: {},
        _json_response=lambda payload: payload,
        _with_restart_state=lambda payload: payload,
        _runtime_surface=None,
        _runtime_capabilities=None,
    )

    async def scenario():
        return await owner._handle_settings(fake, object()), threading.current_thread().name

    with mock.patch.object(sr, "settings_payload", side_effect=fake_payload):
        result, loop_thread = asyncio.run(scenario())
    assert result == {"ok": True}
    assert threads and loop_thread not in threads
