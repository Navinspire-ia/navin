"""CLI blocking work: a roomy default executor and ordered prefs writes."""

from __future__ import annotations

import asyncio
import json
import threading
import time

from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.runtime import TuiRuntime


class _RecordingPrefs(TuiPrefs):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[dict] = []
        self.threads: set[str] = set()
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def save(self, snapshot: str | None = None) -> None:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.threads.add(threading.current_thread().name)
        time.sleep(0.02)
        self.writes.append(json.loads(snapshot if snapshot is not None else self.snapshot()))
        with self._lock:
            self.active -= 1


class _Host:
    _save_prefs_in_background = NavinApp._save_prefs_in_background
    _drain_prefs_snapshots = NavinApp._drain_prefs_snapshots
    _write_prefs_snapshot = NavinApp._write_prefs_snapshot

    def __init__(self) -> None:
        self.prefs = _RecordingPrefs()


def test_prefs_saves_are_serialized_and_the_latest_wins():
    host = _Host()

    async def scenario() -> None:
        writer = None
        for index in range(20):
            host.prefs.remember(f"prompt {index}")
            host.prefs.theme = f"theme-{index}"
            writer = host._save_prefs_in_background()
        assert writer is not None
        await writer

    asyncio.run(scenario())
    prefs = host.prefs
    assert prefs.max_active == 1
    # A burst coalesces: the first write and the newest, not twenty.
    assert len(prefs.writes) <= 3
    assert prefs.writes[-1]["theme"] == "theme-19"
    assert prefs.writes[-1]["history"][-1] == "prompt 19"
    assert threading.main_thread().name not in prefs.threads


def test_prefs_snapshot_is_taken_on_the_caller_thread():
    host = _Host()

    async def scenario() -> None:
        host.prefs.remember("before")
        writer = host._save_prefs_in_background()
        # Mutating after scheduling must not leak into the queued snapshot.
        host.prefs.history.append("after")
        await writer

    asyncio.run(scenario())
    assert host.prefs.writes[-1]["history"] == ["before"]


def test_prefs_save_without_a_loop_writes_directly():
    host = _Host()
    assert host._save_prefs_in_background() is None
    assert len(host.prefs.writes) == 1


def test_runtime_installs_a_large_default_executor():
    from navin.webui.blocking_pool import MIN_WORKERS

    class _Stub:
        _install_blocking_pool = TuiRuntime._install_blocking_pool

    async def scenario() -> int:
        stub = _Stub()
        stub._install_blocking_pool()
        pool = stub._blocking_pool
        stub._install_blocking_pool()
        assert stub._blocking_pool is pool
        loop = asyncio.get_running_loop()
        assert loop._default_executor is pool
        name = await asyncio.to_thread(lambda: threading.current_thread().name)
        assert name.startswith("navin-cli-blocking")
        return pool._max_workers

    assert asyncio.run(scenario()) >= MIN_WORKERS
