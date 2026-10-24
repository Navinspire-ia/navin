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
import gc
import os
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
# A full garbage collection at least this long gets the caller's reaction.
SLOW_FULL_GC_S = 0.5
# At most one reaction per interval, whatever the collector does meanwhile.
SLOW_FULL_GC_REACTION_INTERVAL_S = 60.0
# Frames of an event loop waiting for I/O: selectors (POSIX), proactor (Windows).
_SELECTOR_WAITS = frozenset({"select", "poll", "_poll"})
_SELECTOR_FILES = frozenset({"selectors.py", "windows_events.py"})

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

    A report names a garbage collection in progress, and a loop found idle in
    its selector is not reported. ``after_slow_full_gc`` runs on the loop once
    a full collection took ``slow_full_gc_s`` or more (the CLI freezes the
    survivors there so the next full collection does not walk them again).
    """

    def __init__(
        self,
        log: Any,
        *,
        warn_after_s: float = LOOP_LAG_WARN_S,
        poll_s: float = LOOP_LAG_POLL_S,
        min_interval_s: float = LOOP_LAG_MIN_INTERVAL_S,
        after_slow_full_gc: Callable[[], None] | None = None,
        slow_full_gc_s: float = SLOW_FULL_GC_S,
    ) -> None:
        self._log = log
        self._warn_after_s = max(0.05, float(warn_after_s))
        self._poll_s = max(0.01, float(poll_s))
        self._min_interval_s = max(0.0, float(min_interval_s))
        self._beat = time.monotonic()
        self._loop_ident: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None
        self._after_slow_full_gc = after_slow_full_gc
        self._last_gc_reaction = float("-inf")
        self._gc = GcClock(slow_full_s=slow_full_gc_s, on_slow_full=self._slow_full_gc_seen)
        self.reports = 0
        # Gaps seen while the loop sat idle in its selector: not reported.
        self.idle_gaps = 0

    def start(self) -> None:
        if self._task is not None:
            return
        self._loop_ident = threading.get_ident()
        self._loop = asyncio.get_running_loop()
        self._beat = time.monotonic()
        self._stop.clear()
        if self._gc not in gc.callbacks:
            gc.callbacks.append(self._gc)
        self._task = asyncio.create_task(self._heartbeat(), name="navin-loop-lag-beat")
        self._thread = threading.Thread(
            target=self._watch, name="navin-loop-lag", daemon=True
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        if self._gc in gc.callbacks:
            gc.callbacks.remove(self._gc)
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

    def _slow_full_gc_seen(self, _seconds: float) -> None:
        """Called inside the collector: hand the reaction to the loop."""
        loop = self._loop
        if self._after_slow_full_gc is None or loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._react_to_slow_full_gc)
        except RuntimeError:
            pass  # loop closing

    def _react_to_slow_full_gc(self) -> None:
        now = time.monotonic()
        if self._after_slow_full_gc is None or now - self._last_gc_reaction < SLOW_FULL_GC_REACTION_INTERVAL_S:
            return
        self._last_gc_reaction = now
        try:
            self._after_slow_full_gc()
        except Exception as exc:  # a diagnostics helper must never break the loop
            self._log.warning("reaction to a slow full garbage collection failed: {}", exc)

    def _watch(self) -> None:
        last_report = float("-inf")
        while not self._stop.wait(self._poll_s):
            now = time.monotonic()
            stale = now - self._beat
            if stale < self._warn_after_s or now - last_report < self._min_interval_s:
                continue
            frame = sys._current_frames().get(self._loop_ident or -1)
            if idle_in_selector(frame):
                # Nothing ran on the loop: the process itself was not
                # scheduled (host under load, WSL paused, laptop waking up).
                self.idle_gaps += 1
                continue
            last_report = now
            self.reports += 1
            where = (
                "".join(traceback.format_stack(frame)[-8:]).rstrip()
                if frame is not None
                else "(main thread frame unavailable)"
            )
            gc_note = self._gc.describe(now)
            self._log.warning(
                "event loop blocked for {:.1f}s so far; main thread is in:\n{}{}",
                stale,
                where,
                f"\n  {gc_note}" if gc_note else "",
            )


def idle_in_selector(frame: Any) -> bool:
    """Whether the loop thread is waiting in its selector, i.e. idle.

    182 of 1,618 CLI freeze reports from 17 to 23 October showed the main
    thread in ``selectors.select``: the heartbeat was late because the whole
    process was not scheduled, and the stack named no code to fix.
    """
    if frame is None:
        return False
    code = frame.f_code
    return code.co_name in _SELECTOR_WAITS and os.path.basename(code.co_filename) in _SELECTOR_FILES


class GcClock:
    """``gc.callbacks`` entry: which collection runs, and since when.

    In the CLI freeze log, 316 reports stopped in ``_weakrefset._remove``
    under Textual layout or render code, up to 13 s: the collector freeing
    widgets, not the layout itself. Naming the collection in the report makes
    that cause readable instead of pointing at whatever line allocated last.
    """

    def __init__(
        self,
        *,
        slow_full_s: float = SLOW_FULL_GC_S,
        on_slow_full: Callable[[float], None] | None = None,
    ) -> None:
        self.started: float | None = None
        self.generation: int | None = None
        self._slow_full_s = max(0.0, float(slow_full_s))
        self._on_slow_full = on_slow_full

    def __call__(self, phase: str, info: dict[str, Any]) -> None:
        if phase == "start":
            self.generation = info.get("generation")
            self.started = time.monotonic()
            return
        started, self.started = self.started, None
        if started is None or info.get("generation") != 2 or self._on_slow_full is None:
            return
        took = time.monotonic() - started
        if took >= self._slow_full_s:
            self._on_slow_full(took)

    def describe(self, now: float | None = None) -> str:
        started, generation = self.started, self.generation
        if started is None:
            return ""
        now = time.monotonic() if now is None else now
        return f"(garbage collection of generation {generation} running for {now - started:.1f}s)"
