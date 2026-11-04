# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI stays cheap while it works and while it waits.

Full garbage collections over a long session froze the event loop for a
second at random; folded tool rows carried six output widgets they never
showed; status lines that only tick asked for a full-screen reflow.
"""

import asyncio
import gc

import pytest
from textual.widgets import Static
from textual.widgets._markdown import MarkdownBlock

from navin.tui import frames
from navin.tui.frames import IdleCollector, terminal_gc_policy
from navin.tui.runtime import (
    UiAssistantMessage,
    UiProgress,
    UiStreamDelta,
    UiStreamEnd,
    UiToolEvent,
    UiTurnEnd,
    UiTurnStarted,
)
from navin.tui.widgets import ToolCall, WorkingLine, same_height_text
from tests.test_tui_queue import make_app

MB = 2**20


@pytest.fixture(autouse=True)
def _fresh_gc_bookkeeping(monkeypatch):
    """Each test starts with no freeze, no torn-down chat and no memory reading."""
    monkeypatch.setattr(frames, "_freezes", 0)
    monkeypatch.setattr(frames, "_teardowns", 0)
    monkeypatch.setattr(frames, "_memory_at_freeze", None)
    monkeypatch.setattr(frames, "_freeze_requested", False)


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _Memory:
    """Resident memory as the collector reads it, set by the test."""

    def __init__(self, monkeypatch, start: int = 100 * MB) -> None:
        self.bytes = start
        monkeypatch.setattr(frames, "resident_bytes", lambda: self.bytes)


class _Timer:
    """``time.perf_counter`` for one test: these readings, then the last one."""

    def __init__(self, *readings: float) -> None:
        self._readings = list(readings)

    def __call__(self) -> float:
        return self._readings.pop(0) if len(self._readings) > 1 else self._readings[0]


def _with_pending(monkeypatch, pending: int) -> None:
    monkeypatch.setattr(frames.gc, "get_count", lambda: (0, 0, pending))


def _collections(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(frames.gc, "collect", lambda generation=2: calls.append(generation) or 0)
    return calls


def _frozen(monkeypatch) -> list[str]:
    frozen: list[str] = []
    monkeypatch.setattr(frames.gc, "freeze", lambda: frozen.append("freeze"))
    return frozen


def _something_to_give_back(monkeypatch, memory: _Memory, growth: int = frames.DEEP_GC_GROWTH_BYTES) -> None:
    """A freeze since the last deep pass, and resident memory grown past it."""
    monkeypatch.setattr(frames, "_freezes", frames._freezes + 1)
    memory.bytes += growth


def test_the_policy_leaves_full_collections_to_the_idle_collector():
    before = gc.get_threshold()
    with terminal_gc_policy():
        assert gc.get_threshold()[2] >= 1_000_000
        assert gc.get_threshold()[0] >= 4000
    assert gc.get_threshold() == before


def test_a_due_collection_waits_for_a_quiet_moment(monkeypatch):
    clock = _Clock()
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    with terminal_gc_policy():
        _with_pending(monkeypatch, frames.FULL_GC_DUE - 1)
        assert not collector.tick() and not calls
        _with_pending(monkeypatch, frames.FULL_GC_DUE)
        collector.note_input()
        assert not collector.tick(), "the user is typing"
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        collector.note_event()
        assert not collector.tick(), "the stream is painting"
        clock.now += frames.FULL_GC_QUIET_EVENTS_S
        assert collector.tick()
    assert calls == [frames.ORDINARY_GC_GENERATION] == [2], "a quiet pass collects every unfrozen generation"


def test_a_long_overdue_collection_runs_even_while_busy(monkeypatch):
    clock = _Clock()
    _Memory(monkeypatch)
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    frozen = _frozen(monkeypatch)
    with terminal_gc_policy():
        _with_pending(monkeypatch, frames.FULL_GC_FORCED)
        collector.note_input()
        collector.note_event()
        assert collector.tick()
    assert calls == [frames.FORCED_GC_GENERATION] == [1], "under the user's fingers: the young generations only"
    assert frozen == ["freeze"], "the first long turn freezes its survivors"


def test_no_pass_while_work_runs_unless_long_overdue(monkeypatch):
    """The 2 s gaps of a turn are the wait for the model: a pass there stalled
    the stream it was waiting for (0.8 to 2.6 s each on a long session)."""
    clock = _Clock()
    busy = {"on": True}
    memory = _Memory(monkeypatch)
    collector = IdleCollector(clock, busy=lambda: busy["on"])
    calls = _collections(monkeypatch)
    frozen = _frozen(monkeypatch)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: None)
    monkeypatch.setattr(frames, "_memory_at_freeze", memory.bytes)
    with terminal_gc_policy():
        _with_pending(monkeypatch, frames.FULL_GC_DUE)
        clock.now += frames.DEEP_GC_QUIET_INPUT_S + frames.DEEP_GC_QUIET_EVENTS_S
        assert not collector.tick(), "no pass in a turn's quiet gap"
        _with_pending(monkeypatch, frames.FULL_GC_FORCED)
        assert collector.tick(), "memory stays bounded on an endless turn"
        assert frozen == [], "memory did not grow: the next quiet pass collects all of it"
        memory.bytes += frames.FREEZE_AFTER_GROWTH_BYTES
        _with_pending(monkeypatch, frames.FULL_GC_FORCED * 2)
        assert collector.tick() and frozen == ["freeze"], "grown: the long turn's survivors are frozen"
        memory.bytes += frames.DEEP_GC_GROWTH_BYTES
        assert not collector.tick(), "nor the whole-heap walk while work runs"
        assert collector.deep_collections == 0
        busy["on"] = False
        assert collector.tick() and collector.deep_collections == 1
    assert calls == [1, 1, 2]


def test_an_idle_cli_never_walks_the_whole_heap_on_a_timer(monkeypatch):
    """27 deep passes in one day of the freeze log, 3.3 s each, most of them on
    an idle CLI that had nothing to give back. With no freeze since the last
    one the quiet passes reach everything: even a growing process is spared."""
    clock = _Clock()
    memory = _Memory(monkeypatch)
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    thawed: list[str] = []
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: thawed.append("unfreeze"))
    with terminal_gc_policy():
        _with_pending(monkeypatch, 0)
        for _ in range(12):
            clock.now += 20 * 60.0
            memory.bytes += frames.DEEP_GC_GROWTH_BYTES
            assert not collector.tick()
    assert calls == [] and thawed == []


def test_the_ordinary_pass_counts_from_its_own_last_run(monkeypatch):
    """A gen-1 pass does not reset Python's count: without a base the pass
    would run again at every tick once due."""
    clock = _Clock()
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    with terminal_gc_policy():
        _with_pending(monkeypatch, frames.FULL_GC_DUE)
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert collector.tick()
        assert not collector.tick(), "due counts from the last ordinary pass"
        _with_pending(monkeypatch, frames.FULL_GC_DUE * 2)
        assert collector.tick()
        _with_pending(monkeypatch, 3)  # a deep pass elsewhere reset the count
        assert not collector.tick()
    assert calls == [2, 2]


def test_a_terminal_in_the_background_counts_as_away(monkeypatch):
    clock = _Clock()
    focused = {"on": True}
    memory = _Memory(monkeypatch)
    collector = IdleCollector(clock, focused=lambda: focused["on"])
    calls = _collections(monkeypatch)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: None)
    with terminal_gc_policy():
        _with_pending(monkeypatch, 0)
        _something_to_give_back(monkeypatch, memory)
        collector.note_input()
        clock.now += frames.DEEP_GC_QUIET_EVENTS_S
        focused["on"] = False
        assert not collector.tick(), "typed 10 s ago, then a look at another window"
        focused["on"] = True
        clock.now += frames.DEEP_GC_UNFOCUSED_INPUT_S
        assert not collector.tick(), "the terminal has the focus: the user may be reading"
        focused["on"] = False
        assert collector.tick() and collector.deep_collections == 1
    assert calls == [2]


def test_a_broken_focus_hook_surfaces_instead_of_guessing(monkeypatch):
    """The focus hook reads Textual's App.app_focus: if it ever raises, that is
    a bug to see, not a reason to walk the whole heap under the user's eyes."""
    clock = _Clock()
    memory = _Memory(monkeypatch)

    def broken() -> bool:
        raise AttributeError("app_focus")

    collector = IdleCollector(clock, focused=broken)
    calls = _collections(monkeypatch)
    with terminal_gc_policy():
        _with_pending(monkeypatch, 0)
        _something_to_give_back(monkeypatch, memory)
        collector.note_input()
        clock.now += frames.DEEP_GC_UNFOCUSED_INPUT_S
        with pytest.raises(AttributeError, match="app_focus"):
            collector.tick()
    assert calls == []


def test_a_broken_busy_hook_holds_the_quiet_pass_and_is_logged_once(monkeypatch):
    """Raised from the spinner tick, the error would close the CLI; taken as
    "idle", it ran passes in the middle of turns. It holds the quiet pass (the
    forced one still bounds memory) and is written to the log once."""
    from loguru import logger

    clock = _Clock()
    _Memory(monkeypatch)

    def broken() -> bool:
        raise RuntimeError("runtime not ready")

    collector = IdleCollector(clock, busy=broken)
    calls = _collections(monkeypatch)
    _frozen(monkeypatch)
    seen: list[str] = []
    sink = logger.add(lambda message: seen.append(str(message)), level="WARNING")
    try:
        with terminal_gc_policy():
            _with_pending(monkeypatch, frames.FULL_GC_DUE)
            clock.now += frames.FULL_GC_QUIET_INPUT_S
            for _ in range(3):
                assert not collector.tick()
            _with_pending(monkeypatch, frames.FULL_GC_FORCED)
            assert collector.tick()
    finally:
        logger.remove(sink)
    assert calls == [frames.FORCED_GC_GENERATION]
    assert sum("busy check failed" in line for line in seen) == 1


def test_without_the_policy_python_keeps_its_own_full_collections(monkeypatch):
    collector = IdleCollector(_Clock())
    calls = _collections(monkeypatch)
    _with_pending(monkeypatch, frames.FULL_GC_FORCED)
    assert not collector.tick()
    assert not calls


def test_folded_rows_mount_their_output_only_when_opened(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await app.submit_text("go")
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiTurnStarted("go"))
            for index in range(3):
                call = f"g{index}"
                await app._on_runtime_event(UiToolEvent(call, "grep", "start", {"pattern": f"p{index}", "path": "."}))
                await app._on_runtime_event(
                    UiToolEvent(call, "grep", "end", {"pattern": f"p{index}", "path": "."}, result="a.py:1: hit\n" * 4)
                )
            await pilot.pause()
            rows = list(app.query(ToolCall))
            assert rows
            folded = [row for row in rows if not row._open]
            assert folded
            for row in folded:
                assert not row.query(".tool-output")
                assert len(row.children) == 2  # head and summary
            row = folded[0]
            row.toggle()
            await pilot.pause()
            assert row.query(".tool-output"), "opening mounts the output area"
            row.toggle()
            await pilot.pause()
            assert not row.query_one(".tool-output").display

            # A command shows its output live, then folds like any row.
            await app._on_runtime_event(UiToolEvent("x", "exec", "start", {"command": "pytest -q"}))
            for line in range(3):
                await app._on_runtime_event(UiToolEvent("x", "exec", "output", output=f"case_{line} ok\n"))
            await pilot.pause(0.3)
            run = next(row for row in app.query(ToolCall) if row.call_id == "x")
            assert run.query_one(".tool-output").display
            assert "case_2" in str(run.query_one(".tool-body", Static).content)
    asyncio.run(run())


def test_a_ticking_status_line_repaints_without_a_reflow(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            line = app.query_one("#working", WorkingLine)
            app._refresh_working_line = lambda: None  # no turn: the tick would clear it
            line.set_line("Working... 1s")
            await pilot.pause()
            assert same_height_text(line, str(line.content), "x Working... 2s")
            assert not same_height_text(line, str(line.content), "x " + "w" * 200)
            assert not same_height_text(line, "one", "one\ntwo")
            requested: list[bool] = []
            original = line.refresh

            def refresh(*args, layout=False, **kwargs):
                requested.append(layout)
                return original(*args, layout=layout, **kwargs)

            line.refresh = refresh
            line.set_line("Working... 2s", frame=1)
            assert requested and not any(requested)
    asyncio.run(run())


def test_a_quiet_pass_freezes_what_it_kept_only_once_walking_it_got_slow(monkeypatch):
    """Freezing after every pass froze the chat's widgets and painted lines
    while they were shown, and a frozen cycle is never collected: they stayed
    after they left the chat (CLIs of 2.1 to 11.6 GB on 2026-11-02). A quiet
    pass freezes only once it got slow, so the next ones are short again."""
    clock = _Clock()
    _Memory(monkeypatch)
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    frozen = _frozen(monkeypatch)
    with terminal_gc_policy():
        _with_pending(monkeypatch, frames.FULL_GC_DUE)
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        monkeypatch.setattr(frames.time, "perf_counter", _Timer(10.0, 10.01))
        assert collector.tick()
        assert frozen == [], "a short pass leaves the heap collectable"
        _with_pending(monkeypatch, frames.FULL_GC_DUE * 2)
        monkeypatch.setattr(frames.time, "perf_counter", _Timer(20.0, 20.0 + frames.FREEZE_AFTER_PASS_S * 2))
        assert collector.tick()
        assert frozen == ["freeze"], "a slow pass freezes what it kept"
    assert calls == [2, 2]


def test_the_deep_pass_waits_for_the_user_to_be_away(monkeypatch):
    clock = _Clock()
    memory = _Memory(monkeypatch)
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    frozen = _frozen(monkeypatch)
    thawed: list[str] = []
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: thawed.append("unfreeze"))
    with terminal_gc_policy():
        _with_pending(monkeypatch, 0)
        _something_to_give_back(monkeypatch, memory)
        collector.note_input()
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert not collector.tick(), "the user was at the keyboard a few seconds ago"
        clock.now += frames.DEEP_GC_QUIET_INPUT_S
        assert collector.tick()
        assert thawed == ["unfreeze"] and calls == [2] and frozen == ["freeze"]
        assert collector.memory_after_deep == memory.bytes, "the next one counts its growth from here"
        clock.now += frames.DEEP_GC_QUIET_INPUT_S
        assert not collector.tick(), "nothing frozen and nothing grown since: no second walk"


def test_memory_that_keeps_growing_is_reclaimed_once_the_user_steps_away(monkeypatch):
    """The deep pass walks the whole heap (2 to 6 s measured): on a 2 s typing
    pause it froze the screen under the user's next keys."""
    clock = _Clock()
    memory = _Memory(monkeypatch)
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: None)
    monkeypatch.setattr(frames, "_memory_at_freeze", memory.bytes)
    with terminal_gc_policy():
        # A long turn under the user's eyes: the forced pass freezes its survivors.
        memory.bytes += frames.DEEP_GC_GROWTH_BYTES
        _with_pending(monkeypatch, frames.FULL_GC_FORCED)
        collector.note_input()
        assert collector.tick() and collector.deep_collections == 0
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert not collector.tick(), "a typing pause is no time for a whole-heap walk"
        clock.now += frames.DEEP_GC_QUIET_INPUT_S
        assert collector.tick(), "reclaimed as soon as the user is away"
    assert collector.deep_collections == 1 and calls == [1, 2]


def test_a_runaway_heap_still_waits_for_the_user_to_be_away(monkeypatch):
    """Measured 2026-10-28 00:08 in the CLI freeze log: the forced deep pass
    (growth past 4x) ran on a 2 s typing pause of a 2 GB session and froze the
    screen for 16 s (10 s of gen-2 collection, 6 s of counting). However large
    the growth, the whole-heap walk waits for the user to be away; they never
    pay it under their fingers."""
    clock = _Clock()
    memory = _Memory(monkeypatch)
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: None)
    with terminal_gc_policy():
        _with_pending(monkeypatch, 0)
        _something_to_give_back(monkeypatch, memory, growth=frames.DEEP_GC_GROWTH_BYTES * 40)
        for _ in range(5):
            collector.note_input()
            clock.now += frames.FULL_GC_QUIET_INPUT_S
            assert not collector.tick(), "a typing pause never pays the whole-heap walk"
        clock.now += frames.DEEP_GC_QUIET_INPUT_S
        assert collector.tick(), "reclaimed once the user is away"
    assert collector.deep_collections == 1 and calls == [2]


def test_a_torn_down_chat_brings_the_deep_pass_closer(monkeypatch):
    """The widgets of a chat frozen while it was shown are garbage once the
    user switches away: the deep pass then needs half the growth."""
    clock = _Clock()
    memory = _Memory(monkeypatch)
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: None)
    with terminal_gc_policy():
        _with_pending(monkeypatch, 0)
        _something_to_give_back(monkeypatch, memory, growth=frames.DEEP_GC_GROWTH_BYTES // 2)
        clock.now += frames.DEEP_GC_QUIET_INPUT_S
        assert not collector.tick(), "half the growth is not enough on its own"
        frames.thaw_heap()
        assert collector.tick() and collector.deep_collections == 1
    assert calls == [2]


def test_a_painted_chat_is_frozen_at_the_next_quiet_moment_not_on_the_spot(monkeypatch):
    """The history paint of a session switch collected every unfrozen
    generation right as the chat appeared: 1.1 to 2.0 s under the user's first
    scroll or key, 6 times in one CLI (cli-freeze.log, 2026-11-03)."""
    clock = _Clock()
    _Memory(monkeypatch)
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    frozen = _frozen(monkeypatch)
    with terminal_gc_policy():
        _with_pending(monkeypatch, 0)
        collector.note_input()
        frames.request_heap_freeze()
        assert calls == [] and frozen == [], "the paint itself collects nothing"
        assert not collector.tick(), "the user is still at the keyboard"
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert collector.tick()
        assert calls == [2] and frozen == ["freeze"], "the torn-down chat is given back, the new one kept"
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert not collector.tick(), "one pass per request"
    assert calls == [2]


def test_after_a_slow_deep_pass_the_next_one_waits_for_a_longer_absence(monkeypatch):
    """A deep pass of 75 s on 2026-11-01 22:44: the user who came back to a
    frozen screen is not made to wait again after the next short break."""
    clock = _Clock()
    memory = _Memory(monkeypatch)
    collector = IdleCollector(clock)
    _collections(monkeypatch)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: None)
    with terminal_gc_policy():
        _with_pending(monkeypatch, 0)
        _something_to_give_back(monkeypatch, memory)
        clock.now += frames.DEEP_GC_QUIET_INPUT_S
        monkeypatch.setattr(frames.time, "perf_counter", _Timer(0.0, frames.DEEP_GC_SLOW_S))
        assert collector.tick() and collector.last_deep_s >= frames.DEEP_GC_SLOW_S
        _something_to_give_back(monkeypatch, memory)
        collector.note_input()
        clock.now += frames.DEEP_GC_QUIET_INPUT_S
        assert not collector.tick(), "ten minutes away is not enough after a slow pass"
        clock.now += frames.DEEP_GC_QUIET_INPUT_S * (frames.DEEP_GC_SLOW_ABSENCE - 1)
        assert collector.tick() and collector.deep_collections == 2


def test_no_pass_counts_or_lists_the_heap(monkeypatch):
    """Counting the frozen set (``gc.get_freeze_count``) or listing objects
    (``gc.get_objects``) walks the whole heap: up to 3.2 s on each session
    switch (49 times), 21.7 s right after a deep pass and 3.3 s in an ordinary
    pass in the CLI freeze log of 2026-11-01/02. Every decision reads the
    resident memory instead, in O(1)."""

    def walk(*_args: object) -> None:
        raise AssertionError("walked the heap")

    monkeypatch.setattr(frames.gc, "get_freeze_count", walk)
    monkeypatch.setattr(frames.gc, "get_objects", walk)
    clock = _Clock()
    memory = _Memory(monkeypatch)
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    frozen = _frozen(monkeypatch)
    thawed: list[str] = []
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: thawed.append("unfreeze"))
    with terminal_gc_policy():
        # A session switch: the old chat torn down, the new one painted and frozen.
        frames.thaw_heap()
        assert thawed == [], "a switch never unfreezes: that gave nothing back"
        frames.freeze_heap()
        assert calls == [2] and frozen == ["freeze"], "the torn-down chat is collected, not frozen again"
        # A quiet pass, a pass forced by a long turn, then the deep pass.
        _with_pending(monkeypatch, frames.FULL_GC_DUE)
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert collector.tick()
        memory.bytes += frames.FREEZE_AFTER_GROWTH_BYTES
        collector.note_input()
        _with_pending(monkeypatch, frames.FULL_GC_DUE + frames.FULL_GC_FORCED)
        assert collector.tick()
        memory.bytes += frames.DEEP_GC_GROWTH_BYTES // 2
        clock.now += frames.DEEP_GC_QUIET_INPUT_S
        assert collector.tick() and collector.deep_collections == 1
    assert calls == [2, 2, 1, 2] and thawed == ["unfreeze"] and len(frozen) == 3


def test_a_slow_pass_is_logged_with_its_kind(monkeypatch):
    from loguru import logger

    clock = _Clock()
    _Memory(monkeypatch)
    collector = IdleCollector(clock)
    monkeypatch.setattr(frames.time, "perf_counter", _Timer(0.0, 1.2))
    monkeypatch.setattr(frames.gc, "collect", lambda generation=2: 0)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    seen: list[str] = []
    sink = logger.add(lambda message: seen.append(str(message)), level="WARNING")
    try:
        collector._collect()
    finally:
        logger.remove(sink)
    assert collector.last_pass_s == 1.2
    assert any("ordinary garbage collection took 1.2s (resident memory 100 MB)" in line for line in seen)


def test_the_idle_tick_never_walks_the_frozen_heap(monkeypatch):
    """``gc.get_freeze_count()`` walks every frozen object (tens of ms on a long
    chat). Read 8 times a second it kept a whole core busy per CLI and starved
    the engine sharing its event loop; it is read only after a freeze or thaw."""
    clock = _Clock()
    walks: list[int] = []
    monkeypatch.setattr(frames.gc, "get_freeze_count", lambda: walks.append(1) or 1_000)
    collector = IdleCollector(clock)
    _collections(monkeypatch)
    walks.clear()
    with terminal_gc_policy():
        _with_pending(monkeypatch, 0)
        for _ in range(100):
            clock.now += 0.12
            collector.tick()
    assert walks == []


def test_an_idle_session_checks_its_layout_once_a_second_not_eight_times(tmp_path):
    """No turn, no rows, no live: the 8 Hz tick skips the tree walks.

    An inactive TUI on a long chat burned half a core in `_place_composer` and
    `_refresh_working_line`, and on a shared machine that is what made the
    desktop read "engine slow". The checks still run every IDLE_TICK_EVERY
    ticks, and every tick again as soon as anything moves.
    """
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            placed: list[int] = []
            app._place_composer = lambda: placed.append(1)
            assert any(stars.is_attached for stars in app._stars), "the prompt's stars stay on"
            assert not app.runtime.status.turn_active
            app._painted_busy = app.runtime.status.turn_active
            app._idle_ticks = 0
            for _ in range(app.IDLE_TICK_EVERY * 2):
                app._tick_spinner()
            assert len(placed) == 2, "idle: one layout check per IDLE_TICK_EVERY ticks"

            # A running row makes every tick count again.
            placed.clear()
            await app._on_runtime_event(UiTurnStarted("go"))
            await app._on_runtime_event(UiToolEvent("x", "exec", "start", {"command": "pytest -q"}))
            await pilot.pause()
            placed.clear()
            app._painted_busy = app.runtime.status.turn_active
            for _ in range(4):
                app._tick_spinner()
            assert len(placed) == 4, "busy: the layout checks run on every tick"
    asyncio.run(run())


async def _long_turn(app, index: int) -> None:
    """One turn as the agent streams it: text, tool rows with output, an answer."""
    await app._on_runtime_event(UiTurnStarted(f"question {index}"))
    for chunk in range(12):
        await app._on_runtime_event(UiStreamDelta(f"Partie {chunk} de la reponse {index}, avec du **markdown** et `code`. "))
    await app._on_runtime_event(UiStreamEnd())
    for call in range(4):
        call_id = f"t{index}-{call}"
        if call % 2:
            name, args = "exec", {"command": f"pytest -q tests/test_{call}.py"}
        else:
            name, args = "grep", {"pattern": f"p{call}", "path": "."}
        await app._on_runtime_event(UiToolEvent(call_id, name, "start", args))
        if name == "exec":
            for line in range(8):
                await app._on_runtime_event(UiToolEvent(call_id, name, "output", output=f"case_{line} ok\n"))
        await app._on_runtime_event(UiToolEvent(call_id, name, "end", args, result="a.py:1: hit\n" * 40))
        await app._on_runtime_event(UiProgress(f"Etape {call} terminee."))
    answer = "\n".join([f"## Resultat {index}", "", *[f"- point {n}: `navin/tui/app.py` ligne {n}" for n in range(20)]])
    await app._on_runtime_event(UiAssistantMessage(answer))
    await app._on_runtime_event(UiTurnEnd(latency_ms=1200))


def _frozen_garbage_after_long_turns(workspace, turns: int) -> dict:
    """The long-session scenario below, measured in the calling process.

    Returns the Markdown blocks and the number of objects that only a deep
    pass could free once the turns are over.
    """
    # Freezing on a slow pass is covered above: here no timing decides it.
    frames.FREEZE_AFTER_PASS_S = 3600.0
    measured: dict = {}

    async def run():
        app = make_app(workspace)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._idle_gc.tick = lambda: False  # the test runs the passes
            with terminal_gc_policy():
                gc.collect()
                frames.freeze_heap()  # the empty chat, as the history paint freezes it
                for index in range(turns):
                    await _long_turn(app, index)
                    await pilot.pause()
                    await pilot.pause()
                    app._idle_gc._collect()  # the quiet pass the idle tick runs
                gc.unfreeze()
                gc.set_debug(gc.DEBUG_SAVEALL)
                try:
                    gc.collect()
                    left = list(gc.garbage)
                finally:
                    gc.set_debug(0)
                    gc.garbage.clear()
            measured["markdown"] = [type(item).__qualname__ for item in left if isinstance(item, MarkdownBlock)]
            measured["left"] = len(left)

    asyncio.run(run())
    return measured


def test_a_long_session_leaves_no_garbage_only_a_deep_pass_could_free(tmp_path):
    """Freezing after every pass froze the chat's widgets and painted lines
    while they were shown, and a frozen cycle is never collected: in a probe of
    the real app half of the frozen set was garbage after 20 turns (26 000
    objects a turn), and the CLIs of 2026-11-02 reached 2.1 to 11.6 GB. The
    quiet pass now collects every unfrozen generation.

    Measured in a fresh interpreter, like the CLI process it stands for: in a
    full test run the render caches of earlier tests' apps die inside the
    measured turns and were counted as this chat's (8 000 to 121 000 objects,
    944 in a process of its own)."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    turns = 3
    repo = Path(__file__).resolve().parents[1]
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from navin.config import loader\n"
        "loader.set_config_path(Path(sys.argv[1]) / 'config.json')\n"
        "from tests.test_tui_idle_cost import _frozen_garbage_after_long_turns\n"
        "print(json.dumps(_frozen_garbage_after_long_turns(Path(sys.argv[1]), int(sys.argv[2]))))\n"
    )
    env = {**os.environ, "NAVIN_REASONING_NEGOTIATION_FILE": str(tmp_path / "reasoning_negotiation.json")}
    proc = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(turns)],
        cwd=repo, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    measured = json.loads(proc.stdout.strip().splitlines()[-1])
    assert measured["markdown"] == [], "an answer's Markdown was frozen with the chat, then dropped"
    assert measured["left"] < 1_000 * turns, f"{measured['left']} objects only a deep pass could free"
