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
import functools
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
# One stack per freeze episode: at 30 s, 227 s of freezes in a day had none.
LOOP_LAG_MIN_INTERVAL_S = 5.0
# A gap at least this long that the side thread never reported was a paused
# process (computer asleep, WSL or the host not scheduling it), not a block:
# 2 520 s of "blocked" in one day of the desktop log were laptop sleeps.
LOOP_PAUSE_MIN_S = 30.0
# A full garbage collection at least this long gets the caller's reaction.
SLOW_FULL_GC_S = 0.5
# At most one reaction per interval, whatever the collector does meanwhile.
SLOW_FULL_GC_REACTION_INTERVAL_S = 60.0
# Frames of an event loop waiting for I/O: selectors (POSIX), proactor (Windows).
_SELECTOR_WAITS = frozenset({"select", "poll", "_poll"})
_SELECTOR_FILES = frozenset({"selectors.py", "windows_events.py"})

_MAX_FRAMES_PER_THREAD = 6
_MAX_THREADS = 24
# Innermost frames of the blocked main thread in a freeze report. Eight left
# many CLI stacks with no navin frame at all (only Textual and asyncio).
_MAIN_THREAD_FRAMES = 20

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
    skip_idents: tuple[int | None, ...] = (),
) -> str:
    """Condensed stacks of the threads doing (or waiting on) our work.

    Pool threads parked in ``queue.get`` and the selector loop itself have no
    frame in our code and are left out: the point is the handful of threads
    inside navin, and the innermost line each one is on. Innermost frame is
    listed first so the blocking call is the first thing read.
    """
    frames = sys._current_frames()
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    skipped = {skip_ident, *skip_idents}
    lines: list[str] = []
    shown = 0
    for ident, frame in frames.items():
        if ident in skipped:
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
        context: Callable[[], str] | None = None,
        interesting: Interesting = _navin_module,
    ) -> None:
        self._log = log
        # Which other threads a report lists (ours by default).
        self._interesting = interesting
        # Several CLIs append to one cli-freeze.log: each entry names its
        # process, and the caller may add more (the CLI adds its project).
        self._context = context
        # Last main-thread stack the side thread sampled during a block, with
        # the heartbeat stamp it belongs to: the report written once the loop
        # resumes shows it even when no "so far" entry was written.
        self._last_sample: tuple[float, str] | None = None
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
        reported = self.reports
        while True:
            now = time.monotonic()
            previous = self._beat
            gap = now - previous
            self._beat = now
            if gap >= self._warn_after_s + self._poll_s:
                held = gap - self._poll_s
                sample = self._last_sample
                if paused_not_blocked(held, reported_meanwhile=self.reports != reported):
                    self._log.info(
                        "{}engine was paused for {:.1f}s with nothing running on its loop "
                        "(computer asleep, or the process was not scheduled)",
                        self._prefix(),
                        held,
                    )
                else:
                    where = (
                        f"; main thread was last seen in:\n{sample[1]}"
                        if sample is not None and sample[0] == previous
                        else "; no main thread stack was sampled during it"
                    )
                    self._log.warning(
                        "{}event loop was blocked for {:.1f}s; every request waited that long{}",
                        self._prefix(),
                        held,
                        where,
                    )
            reported = self.reports
            await asyncio.sleep(self._poll_s)

    def _prefix(self) -> str:
        extra = self._context() if self._context is not None else ""
        return f"[pid {os.getpid()}{' ' + extra if extra else ''}] "

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
            beat = self._beat
            now = time.monotonic()
            stale = now - beat
            if stale < self._warn_after_s:
                continue
            frame = sys._current_frames().get(self._loop_ident or -1)
            if idle_in_selector(frame):
                # Nothing ran on the loop: the process itself was not
                # scheduled (host under load, WSL paused, laptop waking up).
                self.idle_gaps += 1
                continue
            # Sampled on every poll of a block, logged at most once an
            # interval: the 8.7 s block of 2026-11-01 20:58 fell inside the
            # interval of an earlier report and its final entry had no stack.
            where = (
                "".join(traceback.format_stack(frame)[-_MAIN_THREAD_FRAMES:]).rstrip()
                if frame is not None
                else "(main thread frame unavailable)"
            )
            gc_note = self._gc.describe()
            if gc_note:
                where = f"{where}\n  {gc_note}"
            # A main thread stopped inside a short C call (a socket accept, a
            # recv) waits for the interpreter lock another thread holds: 325
            # Windows freezes of 1 to 50 s showed no navin frame on the main
            # thread (2026-10-31). The other threads inside our code name it.
            others = thread_snapshot(
                interesting=self._interesting,
                skip_idents=(self._loop_ident, threading.get_ident()),
            )
            where = f"{where}\n  other threads inside navin:\n{others}"
            self._last_sample = (beat, where)
            if now - last_report < self._min_interval_s:
                continue
            last_report = now
            self.reports += 1
            self._log.warning(
                "{}event loop blocked for {:.1f}s so far; main thread is in:\n{}",
                self._prefix(),
                stale,
                where,
            )


def paused_not_blocked(held_s: float, *, reported_meanwhile: bool) -> bool:
    """A long gap with no side report: the whole process was paused.

    Any real block of that length is seen by the side thread, which reports
    the stack after one second. When it never did, it was frozen too (sleep,
    hibernation) or saw the loop idle in its selector (host not scheduling
    the process): no Navin code held the loop.
    """
    return held_s >= LOOP_PAUSE_MIN_S and not reported_meanwhile


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
        # ``started`` first, the clock after: a collection that began after
        # the caller read its clock printed "running for -0.1s" (2026-11-01).
        started, generation = self.started, self.generation
        if started is None:
            return ""
        elapsed = (time.monotonic() if now is None else now) - started
        if elapsed < 0:
            elapsed = max(0.0, time.monotonic() - started)
        return f"(garbage collection of generation {generation} running for {elapsed:.1f}s)"


_STARTUP_HEAP_FROZEN = False


def freeze_startup_heap() -> bool:
    """Move everything alive at engine start out of later full collections.

    Modules, config and registries live for the whole process, yet every full
    collection walked them again and paused the loop 4-8 s with many agents
    running. Only done once, at startup: freezing later would also pin
    short-lived objects alive at that moment. True when this call froze it.
    """
    global _STARTUP_HEAP_FROZEN
    if _STARTUP_HEAP_FROZEN:
        return False
    _STARTUP_HEAP_FROZEN = True
    gc.collect()
    gc.freeze()
    return True


# Between two idle passes at least this long. A pass is a full collection
# over the unfrozen objects only: cheap once the previous pass froze the rest.
IDLE_HEAP_PASS_MIN_INTERVAL_S = 60.0
# Frozen objects may hold cycles that died since (a finished turn's tasks and
# tracebacks, and any cyclic garbage that reached generation 2 before an
# ordinary pass froze it). Once the process resident memory grew this much
# past the last baseline, the next idle pass thaws everything first, collects
# it all and freezes again: rare, and idle only. The baseline is set by the
# first pass (which puts away whatever the engine built up before its first
# idle minute) and by every deep pass. Resident memory, not the frozen count:
# ``gc.get_freeze_count()`` walks the whole permanent generation, and reading
# it after every pass stopped the desktop loop about once a minute (171 of
# 208 "event loop blocked" stacks of 2026-11-01 ended on that line).
IDLE_HEAP_DEEP_GROWTH_BYTES = 128 * 1024 * 1024
# Two deep passes at least this far apart, whatever the memory does (a native
# cache growing outside the Python heap must not turn every idle minute into
# a full walk of the heap).
IDLE_HEAP_DEEP_MIN_INTERVAL_S = 600.0
# When this process cannot read its own resident memory, a deep pass this
# often instead, so frozen garbage is still given back.
IDLE_HEAP_DEEP_FALLBACK_INTERVAL_S = 3600.0
# Generation the ordinary pass collects. A full collection walked every object
# built since the last pass: 1.5 to 2.4 s with the loop stopped, 8 times in the
# gateway log of 2026-10-30/31, while the desktop UI kept asking. Python's own
# automatic collections still run in the engine; garbage frozen by a young pass
# is given back by the deep pass once the resident memory grew.
IDLE_HEAP_ORDINARY_GENERATION = 1
# A pass at least this long is logged with its kind, for the next audit.
SLOW_HEAP_PASS_S = 0.5


@functools.lru_cache(maxsize=1)
def _this_process(pid: int) -> Any:
    """``psutil.Process`` of *pid*, built once (keyed on the pid for a fork)."""
    try:
        import psutil
    except ImportError:  # a frozen build that did not ship its native module
        return None
    return psutil.Process(pid)


def _resident_without_psutil() -> int | None:
    """Resident memory read straight from the system, each read O(1)."""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/statm", encoding="ascii") as handle:
                pages = int(handle.read().split()[1])
        except (OSError, ValueError, IndexError):
            return None
        return pages * os.sysconf("SC_PAGE_SIZE")
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize)
    try:
        import resource
    except ImportError:
        return None
    # macOS and the BSDs: the peak, which still only grows with the heap.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024


def resident_memory_bytes() -> int | None:
    """This process's resident memory, without walking any object.

    psutil reads ``/proc/self/statm`` on Linux and ``GetProcessMemoryInfo``
    on Windows: a constant cost, whatever the size of the heap. None when
    the system gives no reading.
    """
    process = _this_process(os.getpid())
    if process is None:
        return _resident_without_psutil()
    import psutil

    try:
        return int(process.memory_info().rss)
    except (psutil.Error, OSError):
        return _resident_without_psutil()


class IdleHeapPass:
    """Put the engine's long-lived heap away while nothing runs.

    ``freeze_startup_heap`` covers what exists at start. A day of sessions,
    indexes and tool results then grows the unfrozen long-lived set, and every
    automatic full collection walks all of it again: 1.1 s pauses in the
    desktop log of 2026-10-27, in the middle of turns. The agent loop ticks
    this once a minute while its inbound queue is quiet; with no turn and no
    subagent running, the pass collects and freezes the survivors, so the
    collections Python starts on its own during the next turns only walk the
    objects created since. Cyclic garbage pinned by a freeze is given back by
    the deep pass once the resident memory grew by ``IDLE_HEAP_DEEP_GROWTH_BYTES``.

    An ordinary pass reads nothing whose cost grows with the heap: a young
    collection, ``gc.freeze()`` (a list splice) and one resident memory
    reading. Only the deep pass walks the frozen objects, by collecting them.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        *,
        min_interval_s: float = IDLE_HEAP_PASS_MIN_INTERVAL_S,
        deep_growth_bytes: int = IDLE_HEAP_DEEP_GROWTH_BYTES,
        deep_min_interval_s: float = IDLE_HEAP_DEEP_MIN_INTERVAL_S,
        deep_fallback_interval_s: float = IDLE_HEAP_DEEP_FALLBACK_INTERVAL_S,
        resident: Callable[[], int | None] = resident_memory_bytes,
    ) -> None:
        self._clock = clock
        self._min_interval_s = min_interval_s
        self._deep_growth_bytes = deep_growth_bytes
        self._deep_min_interval_s = deep_min_interval_s
        self._deep_fallback_interval_s = deep_fallback_interval_s
        self._resident = resident
        self._last_pass_at = float("-inf")
        # When the baseline was set: the first pass, then each deep pass.
        self._baseline_at: float | None = None
        # Resident memory the growth is measured from: after the first pass,
        # then after each deep pass. Not the reading at construction: the
        # first pass collects and freezes the whole live heap, which is size,
        # not growth. None while no pass ran, or when the system gives no
        # reading (the deep pass then comes on the fallback interval).
        self.resident_after_deep: int | None = None
        # Resident memory read at the start of the last pass.
        self.resident: int | None = None
        self.passes = 0
        self.deep_passes = 0
        self.last_pass_s = 0.0

    def _deep_due(self, now: float, resident: int | None) -> bool:
        baseline_at = self._baseline_at
        if baseline_at is None or now - baseline_at < self._deep_min_interval_s:
            return False
        base = self.resident_after_deep
        if resident is None or base is None:
            return now - baseline_at >= self._deep_fallback_interval_s
        return resident - base >= self._deep_growth_bytes

    def tick(self, *, idle: bool) -> bool:
        """Run a pass when the engine is idle and the interval has passed."""
        if not idle or not gc.isenabled():
            return False
        now = self._clock()
        if now - self._last_pass_at < self._min_interval_s:
            return False
        self._last_pass_at = now
        first = self._baseline_at is None
        self.resident = self._resident()
        deep = self._deep_due(now, self.resident)
        started = time.perf_counter()
        if deep:
            gc.unfreeze()
        # The first pass puts away everything built before the first idle
        # minute: a full collection, once, so no startup garbage is pinned.
        gc.collect(2 if deep or first else IDLE_HEAP_ORDINARY_GENERATION)
        gc.freeze()
        self.last_pass_s = time.perf_counter() - started
        self.passes += 1
        if deep:
            self.deep_passes += 1
        if deep or first:
            self._baseline_at = now
            self.resident_after_deep = self._resident()
        if self.last_pass_s >= SLOW_HEAP_PASS_S:
            from loguru import logger

            logger.info(
                "engine {} heap pass took {:.1f}s while idle ({} MB resident before it)",
                "deep" if deep else "ordinary", self.last_pass_s,
                "?" if self.resident is None else self.resident // (1024 * 1024),
            )
        return True
