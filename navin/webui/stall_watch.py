# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Say *where* the gateway is stuck, while it is stuck.

``slow webui http route`` records that ``/api/webui/skills`` took 70 s. It is
written when the work is over, so it cannot say why: a 0.6 s directory scan
that took 70 s was waiting on something - a lock, the GIL, a thread that never
came - and the log had no name for it. Every tab then showed the same
"timed out after 20000ms", which read as a broken IDE rather than one stalled
resource.

Two probes fill that gap, both cheap enough to stay on:

- :class:`StallReporter` arms a timer per WebUI route. A route still running
  past ``dump_after_s`` gets one condensed snapshot of every busy thread's
  stack in the log (rate limited), then finishes normally.
- :class:`LoopLagMonitor` watches the event loop from a helper thread. When a
  heartbeat coroutine stops beating, the main thread's stack is logged from
  outside - the one place a synchronous block on the loop can be seen from.

Neither changes what any route does; they only make the next stall legible.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")

# A WebUI route past this is what the client calls a timeout.
STALL_DUMP_AFTER_S = 10.0
# One snapshot per minute is plenty to name a culprit; a burst of stalled
# routes all share the same cause.
STALL_DUMP_MIN_INTERVAL_S = 60.0
# How late the heartbeat may run before the loop counts as blocked.
LOOP_LAG_WARN_S = 1.0
LOOP_LAG_POLL_S = 0.25
LOOP_LAG_MIN_INTERVAL_S = 30.0

_MAX_FRAMES_PER_THREAD = 6
_MAX_THREADS = 24

Interesting = Callable[[str], bool]


def _navin_module(name: str) -> bool:
    return name == "navin" or name.startswith("navin.")


def _module_of(frame: Any) -> str:
    return str(frame.f_globals.get("__name__", "") or "")


def thread_snapshot(
    *,
    interesting: Interesting = _navin_module,
    skip_ident: int | None = None,
    max_frames: int = _MAX_FRAMES_PER_THREAD,
    max_threads: int = _MAX_THREADS,
) -> str:
    """Condensed stacks of the threads doing (or waiting on) our work.

    Pool threads parked in ``queue.get`` and the selector loop itself have no
    frame in our code and are left out: the point is the handful of threads
    inside navin, and the innermost line each one is on. Innermost frame is
    listed first so the blocking call is the first thing read.
    """
    frames = sys._current_frames()
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    lines: list[str] = []
    shown = 0
    for ident, frame in frames.items():
        if ident == skip_ident:
            continue
        chain: list[tuple[str, int, str, str]] = []
        current = frame
        while current is not None:
            chain.append(
                (
                    _module_of(current),
                    current.f_lineno,
                    current.f_code.co_name,
                    current.f_code.co_filename,
                )
            )
            current = current.f_back
        if not any(interesting(module) for module, _, _, _ in chain):
            continue
        if shown >= max_threads:
            lines.append(f"- ... {len(frames) - shown} more threads")
            break
        shown += 1
        lines.append(f"- {names.get(ident, 'thread')} ({ident})")
        for module, lineno, func, filename in chain[:max_frames]:
            where = module or filename
            lines.append(f"    {where}:{lineno} in {func}")
    if not lines:
        return "(no thread is inside navin code)"
    return "\n".join(lines)


class StallReporter:
    """Log a thread snapshot when an awaited route runs too long."""

    def __init__(
        self,
        log: Any,
        *,
        dump_after_s: float = STALL_DUMP_AFTER_S,
        min_interval_s: float = STALL_DUMP_MIN_INTERVAL_S,
        snapshot: Callable[[], str] | None = None,
    ) -> None:
        self._log = log
        self._dump_after_s = max(0.0, float(dump_after_s))
        self._min_interval_s = max(0.0, float(min_interval_s))
        self._snapshot = snapshot
        self._last_dump = float("-inf")
        self.dumps = 0

    async def watch(self, label: str, work: Awaitable[T]) -> T:
        """Await *work*; if it is still running after the deadline, report."""
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        handle = loop.call_later(self._dump_after_s, self._report, label, started)
        try:
            return await work
        finally:
            handle.cancel()

    def _report(self, label: str, started: float) -> None:
        now = time.monotonic()
        if now - self._last_dump < self._min_interval_s:
            return
        self._last_dump = now
        self.dumps += 1
        try:
            body = self._snapshot() if self._snapshot else thread_snapshot(
                skip_ident=threading.get_ident()
            )
        except Exception as exc:  # noqa: BLE001 - a probe must never break a route
            body = f"(snapshot failed: {exc!r})"
        self._log.warning(
            "webui route {} still running after {:.0f}s; threads inside navin:\n{}",
            label,
            now - started,
            body,
        )


class LoopLagMonitor:
    """Notice a blocked event loop and log what the main thread was doing.

    A coroutine stamps ``_beat`` every ``poll_s``; a daemon thread checks the
    stamp. The thread can read the main thread's frame while the loop is still
    inside the blocking call, which nothing running *on* the loop could do.
    Once the loop resumes, the coroutine logs how long the gap was.
    """

    def __init__(
        self,
        log: Any,
        *,
        warn_after_s: float = LOOP_LAG_WARN_S,
        poll_s: float = LOOP_LAG_POLL_S,
        min_interval_s: float = LOOP_LAG_MIN_INTERVAL_S,
    ) -> None:
        self._log = log
        self._warn_after_s = max(0.05, float(warn_after_s))
        self._poll_s = max(0.01, float(poll_s))
        self._min_interval_s = max(0.0, float(min_interval_s))
        self._beat = time.monotonic()
        self._loop_ident: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None
        self.reports = 0

    def start(self) -> None:
        if self._task is not None:
            return
        self._loop_ident = threading.get_ident()
        self._beat = time.monotonic()
        self._stop.clear()
        self._task = asyncio.create_task(self._heartbeat(), name="navin-loop-lag-beat")
        self._thread = threading.Thread(
            target=self._watch, name="navin-loop-lag", daemon=True
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)

    async def _heartbeat(self) -> None:
        while True:
            now = time.monotonic()
            gap = now - self._beat
            self._beat = now
            if gap >= self._warn_after_s + self._poll_s:
                self._log.warning(
                    "event loop was blocked for {:.1f}s; every request waited that long",
                    gap - self._poll_s,
                )
            await asyncio.sleep(self._poll_s)

    def _watch(self) -> None:
        last_report = float("-inf")
        while not self._stop.wait(self._poll_s):
            now = time.monotonic()
            stale = now - self._beat
            if stale < self._warn_after_s or now - last_report < self._min_interval_s:
                continue
            last_report = now
            self.reports += 1
            frame = sys._current_frames().get(self._loop_ident or -1)
            where = (
                "".join(traceback.format_stack(frame)[-8:]).rstrip()
                if frame is not None
                else "(main thread frame unavailable)"
            )
            self._log.warning(
                "event loop blocked for {:.1f}s so far; main thread is in:\n{}",
                stale,
                where,
            )
