# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Training jobs: kicked after a turn, run on a daemon thread, never inside.

The turn hook only ever calls ``kick``: a ``queue.put`` of the workspace.
The runner thread decides whether training is due (enough new lines since
the last run, ``train`` corridor on), runs it at low priority, and moves
on. ``run_due`` does the same synchronously for the CLI and for tests, which
switch the thread off with ``configure(auto_thread=False)``.

With the project flag off, ``kick`` returns before touching the queue.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from navin.world_model.settings import read_settings

# Do not re-run training for the same project sooner than this.
_MIN_INTERVAL_S = 10 * 60


class _Runner:
    def __init__(self) -> None:
        self._queue: queue.Queue[Path | threading.Event | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self._last_run: dict[str, float] = {}
        self.auto_thread = True

    def configure(self, *, auto_thread: bool) -> None:
        self.auto_thread = auto_thread

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def kick(self, workspace: Path) -> None:
        if not self.auto_thread:
            return
        self._ensure_thread()
        self._queue.put(workspace)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        if not self.alive:
            return True
        done = threading.Event()
        self._queue.put(done)
        return done.wait(timeout)

    def _ensure_thread(self) -> None:
        if self.alive:
            return
        with self._lock:
            if self.alive:
                return
            self._thread = threading.Thread(target=self._run, name="navin-world-train", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            os.nice(5)
        except (OSError, AttributeError):
            pass
        while True:
            item = self._queue.get()
            if item is None:
                return
            if isinstance(item, threading.Event):
                item.set()
                continue
            try:
                run_due(item)
            except Exception as exc:  # noqa: BLE001 - the sidecar must never crash the host
                logger.warning("world-model train runner error for {}: {}", item, exc)

    def recently_ran(self, workspace: Path, now: float) -> bool:
        last = self._last_run.get(str(workspace))
        return last is not None and now - last < _MIN_INTERVAL_S

    def mark_ran(self, workspace: Path, now: float) -> None:
        self._last_run[str(workspace)] = now

    def busy_lock(self) -> threading.Lock:
        return self._busy

    def reset(self) -> None:
        self._last_run.clear()


_RUNNER = _Runner()


def configure(*, auto_thread: bool) -> None:
    _RUNNER.configure(auto_thread=auto_thread)


def runner_alive() -> bool:
    return _RUNNER.alive


def wait_idle(timeout: float = 30.0) -> bool:
    return _RUNNER.wait_idle(timeout)


def reset_runner() -> None:
    _RUNNER.reset()


def kick(workspace: Path | str) -> bool:
    """Ask the runner to look at this project after the turn. Cheap, off = False."""
    workspace = Path(workspace)
    if not read_settings(workspace).feature("train"):
        return False
    if _RUNNER.recently_ran(workspace, time.monotonic()):
        return False
    _RUNNER.kick(workspace)
    return True


def run_due(workspace: Path | str, *, force: bool = False) -> dict[str, Any] | None:
    """Train now if due (or ``force``), in this thread. ``None`` when nothing ran."""
    from navin.world_model.train import train, training_due

    workspace = Path(workspace)
    if not read_settings(workspace).feature("train") and not force:
        return None
    with _RUNNER.busy_lock():
        now = time.monotonic()
        if not force and (_RUNNER.recently_ran(workspace, now) or not training_due(workspace)):
            return None
        _RUNNER.mark_ran(workspace, now)
        return train(workspace, actor="auto" if not force else "human", force=force).as_dict()
