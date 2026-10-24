# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One app-wide paint clock for streamed text and live tool output.

Every streaming answer, thinking row and running command used to own a
timer. Fifty parallel streams meant fifty timer tasks waking on their own
schedule, each parsing and painting between the user's key presses.

Widgets now mark themselves dirty here. A single clock (about 30 frames a
second) drains every due item in one pass: synchronous paints share one
``batch_update``, asynchronous ones (Markdown mounts) run one after the
other. Keyboard input always wins: a frame waits while a key is queued, a
pass yields between items as soon as one arrives, and a pass that runs
past its budget leaves the rest for the next frame. Marking an item dirty
twice keeps one entry, so a slow frame coalesces work instead of queueing
it.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

from textual import events

FRAME_SECONDS = 1 / 30
# Work per frame before the rest waits for the next one.
FRAME_BUDGET_SECONDS = 0.008
# A waiting key defers a frame by this much, at most this many times in a row.
KEY_DEFER_SECONDS = 0.004
MAX_KEY_DEFERS = 12
# Costly paints marked ``yield_to_input`` (live command logs) pause while
# the user types, for at most MAX_INPUT_DEFER_SECONDS at a time.
INPUT_QUIET_SECONDS = 0.15
MAX_INPUT_DEFER_SECONDS = 0.4

Callback = Callable[[], Any]


def _queued_key(pump: Any) -> bool:
    holder = pump.__dict__.get("_message_queue") if hasattr(pump, "__dict__") else None
    # Textual's own queue keeps a ``values`` deque; asyncio's a ``_queue``.
    queue = getattr(holder, "values", None) or getattr(holder, "_queue", None)
    if not queue:
        return False
    return any(isinstance(message, (events.Key, events.Paste)) for message in queue)


def key_pending(app: Any) -> bool:
    """Whether a key press waits in the app's or the focused widget's queue."""
    try:
        focused = app.focused
    except Exception:  # noqa: BLE001 - no screen yet
        focused = None
    return _queued_key(app) or (focused is not None and _queued_key(focused))


class FrameScheduler:
    """Coalesce paints from every live widget into one frame clock."""

    def __init__(self, app: Any, *, frame_seconds: float = FRAME_SECONDS,
                 budget_seconds: float = FRAME_BUDGET_SECONDS) -> None:
        self.app = app
        self.frame_seconds = frame_seconds
        self.budget_seconds = budget_seconds
        # key -> (due time, callback). Insertion order is drain order.
        self._items: dict[Hashable, tuple[float, Callback]] = {}
        self._timer: Any = None
        self._timer_due = 0.0
        self._running = False
        self._key_defers = 0
        self._yielding: dict[Hashable, float] = {}
        self.last_input = 0.0
        self.frames = 0
        self.overruns = 0
        self.deferred_for_keys = 0

    # -- public API ---------------------------------------------------------

    def note_input(self) -> None:
        """A key was handled: costly ``yield_to_input`` paints wait a little."""
        self.last_input = time.monotonic()

    def request(self, key: Hashable, callback: Callback, *, delay: float | None = None,
                yield_to_input: bool = False) -> None:
        """Paint ``callback`` on a coming frame; repeated requests coalesce.

        ``delay`` is the earliest time from now the item may run (a live
        command log repaints ten times a second, not thirty).
        """
        now = time.monotonic()
        due = now + (self.frame_seconds if delay is None else max(0.0, delay))
        if yield_to_input:
            self._yielding.setdefault(key, due)
        current = self._items.get(key)
        if current is not None:
            # Keep the earlier slot: a stream must not starve by re-requesting.
            self._items[key] = (min(current[0], due), callback)
            return
        self._items[key] = (due, callback)
        if not self._running:
            self._arm(due)

    def cancel(self, key: Hashable) -> None:
        self._items.pop(key, None)
        self._yielding.pop(key, None)

    def pending(self, key: Hashable) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    async def drain(self) -> None:
        """Run every queued item now (tests, shutdown)."""
        while self._items:
            await self._run_items(list(self._items), budget=None)

    # -- clock --------------------------------------------------------------

    def _arm(self, due: float) -> None:
        if self._timer is not None and self._timer_due <= due:
            return
        if self._timer is not None:
            self._timer.stop()
        self._timer_due = due
        try:
            # Textual timers divide by their interval: never zero.
            self._timer = self.app.set_timer(max(0.001, due - time.monotonic()), self._frame)
        except Exception:  # noqa: BLE001 - app not running (closing, tests)
            self._timer = None

    async def _frame(self) -> None:
        self._timer = None
        if not self._items:
            return
        app = self.app
        if getattr(app, "_exit", False):
            self._items.clear()
            return
        if key_pending(app) and self._key_defers < MAX_KEY_DEFERS:
            # The key paints first; the frame follows right after it.
            self._key_defers += 1
            self.deferred_for_keys += 1
            self._arm(time.monotonic() + KEY_DEFER_SECONDS)
            return
        self._key_defers = 0
        now = time.monotonic()
        typing = now - self.last_input < INPUT_QUIET_SECONDS
        due = [
            key for key, (when, _callback) in self._items.items()
            if when <= now + 0.001 and not (
                typing and key in self._yielding and now - self._yielding[key] < MAX_INPUT_DEFER_SECONDS
            )
        ]
        started = time.perf_counter()
        self._running = True
        try:
            if due:
                await self._run_items(due, budget=self.budget_seconds)
        finally:
            self._running = False
        self.frames += 1
        elapsed = time.perf_counter() - started
        if elapsed > self.frame_seconds:
            self.overruns += 1
        if self._items:
            # An overrun frame pushes the next one out instead of stacking
            # frames back to back; leftovers keep their place in line.
            soonest = min(when for when, _callback in self._items.values())
            if typing and not due:
                soonest = max(soonest, self.last_input + INPUT_QUIET_SECONDS)
            wait = self.frame_seconds if elapsed > self.frame_seconds else self.frame_seconds - elapsed
            self._arm(max(soonest, time.monotonic() + wait))

    async def _run_items(self, keys: list[Hashable], *, budget: float | None) -> None:
        app = self.app
        started = time.perf_counter()
        pending_async: list[tuple[Hashable, Callback]] = []
        pending_sync: list[Callback] = []
        for key in keys:
            item = self._items.get(key)
            if item is None:
                continue
            if _is_async(item[1]):
                pending_async.append((key, item[1]))
            else:
                del self._items[key]
                self._yielding.pop(key, None)
                pending_sync.append(item[1])
        if pending_sync:
            # Synchronous paints first, all in one batch: one composition.
            with app.batch_update():
                for callback in pending_sync:
                    try:
                        callback()
                    except Exception as error:  # noqa: BLE001 - one widget must not stop the clock
                        _report(app, error)
        for position, (key, callback) in enumerate(pending_async):
            if position:
                # Hand the loop back between items: input, timers and the
                # compositor run between two costly paints, never behind both.
                await asyncio.sleep(0)
                if budget is not None and time.perf_counter() - started > budget:
                    break
            while key_pending(app):
                # Let the queued key run (and paint) before the next item.
                await asyncio.sleep(0)
                if time.perf_counter() - started > 0.05:
                    break
            current = self._items.get(key)
            if current is None or current[1] is not callback:
                continue
            del self._items[key]
            self._yielding.pop(key, None)
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception as error:  # noqa: BLE001
                _report(app, error)


def _is_async(callback: Callback) -> bool:
    function = getattr(callback, "__func__", callback)
    return inspect.iscoroutinefunction(function)


def _report(app: Any, error: Exception) -> None:
    handler = getattr(app, "_handle_exception", None)
    if callable(handler):
        handler(error)
    else:  # pragma: no cover - not a Textual app
        raise error


def frame_scheduler(app: Any) -> FrameScheduler:
    """The app's single frame clock (created on first use)."""
    scheduler = getattr(app, "_navin_frame_scheduler", None)
    if scheduler is None:
        scheduler = FrameScheduler(app)
        app._navin_frame_scheduler = scheduler
    return scheduler


def request_frame(widget: Any, key: Hashable, callback: Callable[[], Awaitable[Any] | Any],
                  *, delay: float | None = None) -> bool:
    """Queue a paint for a mounted widget; False when it has no app."""
    try:
        app = widget.app
    except Exception:  # noqa: BLE001 - detached
        return False
    frame_scheduler(app).request(key, callback, delay=delay)
    return True


class FrameRequest:
    """A queued paint; ``stop()`` drops it, like a Textual ``Timer``."""

    __slots__ = ("key", "scheduler")

    def __init__(self, scheduler: FrameScheduler, key: Hashable) -> None:
        self.scheduler = scheduler
        self.key = key

    def stop(self) -> None:
        self.scheduler.cancel(self.key)


def schedule_paint(widget: Any, name: str, callback: Callable[[], Awaitable[Any] | Any],
                   *, delay: float | None = None, yield_to_input: bool = False) -> FrameRequest | None:
    """Queue ``callback`` for ``widget`` on the app's frame clock."""
    try:
        app = widget.app
    except Exception:  # noqa: BLE001 - detached
        return None
    scheduler = frame_scheduler(app)
    key = (widget, name)
    scheduler.request(key, callback, delay=delay, yield_to_input=yield_to_input)
    return FrameRequest(scheduler, key)


def cancel_frame(widget: Any, key: Hashable) -> None:
    try:
        app = widget.app
    except Exception:  # noqa: BLE001
        return
    scheduler = getattr(app, "_navin_frame_scheduler", None)
    if scheduler is not None:
        scheduler.cancel(key)


def note_input(app: Any) -> None:
    """Tell the app's frame clock a key was just handled."""
    scheduler = getattr(app, "_navin_frame_scheduler", None)
    if scheduler is not None:
        scheduler.note_input()
