# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI stays cheap while it works and while it waits.

Full garbage collections over a long session froze the event loop for a
second at random; folded tool rows carried six output widgets they never
showed; status lines that only tick asked for a full-screen reflow.
"""

import asyncio
import gc

from textual.widgets import Static

from navin.tui import frames
from navin.tui.frames import IdleCollector, terminal_gc_policy
from navin.tui.runtime import UiToolEvent, UiTurnStarted
from navin.tui.widgets import ToolCall, WorkingLine, same_height_text
from tests.test_tui_queue import make_app


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _with_pending(monkeypatch, pending: int) -> None:
    monkeypatch.setattr(frames.gc, "get_count", lambda: (0, 0, pending))


def _collections(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(frames.gc, "collect", lambda generation=2: calls.append(generation) or 0)
    return calls


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
    assert calls == [2]


def test_a_long_overdue_collection_runs_even_while_busy(monkeypatch):
    clock = _Clock()
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    with terminal_gc_policy():
        _with_pending(monkeypatch, frames.FULL_GC_FORCED)
        collector.note_input()
        collector.note_event()
        assert collector.tick()
    assert calls == [2]


def test_no_full_pass_while_a_turn_runs_unless_long_overdue(monkeypatch):
    """The 2 s gaps of a turn are the wait for the model: a pass there stalled
    the stream it was waiting for (0.8 to 2.6 s each on a long session)."""
    clock = _Clock()
    busy = {"on": True}
    monkeypatch.setattr(frames.gc, "get_freeze_count", lambda: 1_000)
    collector = IdleCollector(clock, busy=lambda: busy["on"])
    calls = _collections(monkeypatch)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: None)
    with terminal_gc_policy():
        _with_pending(monkeypatch, frames.FULL_GC_DUE)
        clock.now += frames.DEEP_GC_INTERVAL_S + frames.DEEP_GC_QUIET_INPUT_S
        assert not collector.tick(), "neither the ordinary nor the deep pass"
        _with_pending(monkeypatch, frames.FULL_GC_FORCED)
        assert collector.tick(), "memory stays bounded on an endless turn"
        assert collector.deep_collections == 0
        busy["on"] = False
        assert collector.tick() and collector.deep_collections == 1
    assert calls == [2, 2]


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


def test_survivors_are_frozen_so_the_next_pass_is_short(monkeypatch):
    clock = _Clock()
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    frozen: list[str] = []
    monkeypatch.setattr(frames.gc, "freeze", lambda: frozen.append("freeze"))
    with terminal_gc_policy():
        _with_pending(monkeypatch, frames.FULL_GC_DUE)
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert collector.tick()
    assert calls == [2] and frozen == ["freeze"]


def test_the_deep_pass_waits_for_the_user_to_be_away(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(frames.gc, "get_freeze_count", lambda: 1_000)
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    thawed: list[str] = []
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: thawed.append("unfreeze"))
    with terminal_gc_policy():
        _with_pending(monkeypatch, 0)
        clock.now += frames.DEEP_GC_INTERVAL_S
        collector.note_input()
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert not collector.tick(), "the user was at the keyboard a few seconds ago"
        clock.now += frames.DEEP_GC_QUIET_INPUT_S
        assert collector.tick()
    assert thawed == ["unfreeze"] and calls == [2]


def test_a_frozen_set_that_keeps_growing_is_reclaimed_once_the_user_steps_away(monkeypatch):
    """The deep pass walks the whole heap (2 to 6 s measured): on a 2 s typing
    pause it froze the screen under the user's next keys."""
    clock = _Clock()
    count = {"objects": 0}
    monkeypatch.setattr(frames, "_frozen_count", 0)
    monkeypatch.setattr(frames.gc, "get_freeze_count", lambda: 0)
    monkeypatch.setattr(frames, "_unfrozen_objects", lambda: count["objects"])
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: None)
    with terminal_gc_policy():
        # Each ordinary pass freezes its survivors: here a large batch of them.
        count["objects"] = frames.DEEP_GC_GROWTH
        _with_pending(monkeypatch, frames.FULL_GC_DUE)
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert collector.tick() and collector.deep_collections == 0
        _with_pending(monkeypatch, 0)
        collector.note_input()
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert not collector.tick(), "a typing pause is no time for a whole-heap walk"
        clock.now += frames.DEEP_GC_QUIET_INPUT_S
        assert collector.tick(), "reclaimed as soon as the user is away"
    assert collector.deep_collections == 1 and calls == [2, 2]


def test_a_runaway_frozen_set_is_reclaimed_at_the_next_quiet_moment(monkeypatch):
    clock = _Clock()
    count = {"objects": 0}
    monkeypatch.setattr(frames, "_frozen_count", 0)
    monkeypatch.setattr(frames.gc, "get_freeze_count", lambda: 0)
    monkeypatch.setattr(frames, "_unfrozen_objects", lambda: count["objects"])
    collector = IdleCollector(clock)
    calls = _collections(monkeypatch)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: None)
    with terminal_gc_policy():
        count["objects"] = frames.DEEP_GC_GROWTH_FORCED
        _with_pending(monkeypatch, frames.FULL_GC_DUE)
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert collector.tick() and collector.deep_collections == 0
        _with_pending(monkeypatch, 0)
        collector.note_input()
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert collector.tick(), "bounded memory beats waiting for the user to leave"
    assert collector.deep_collections == 1 and calls == [2, 2]


def test_collections_never_walk_the_frozen_heap_to_count_it(monkeypatch):
    """``gc.get_freeze_count()`` after each pass took 2 to 4.5 s in the CLI log."""
    clock = _Clock()
    walks: list[int] = []
    monkeypatch.setattr(frames, "_frozen_count", 7)
    monkeypatch.setattr(frames.gc, "get_freeze_count", lambda: walks.append(1) or 7)
    collector = IdleCollector(clock)
    _collections(monkeypatch)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames.gc, "unfreeze", lambda: None)
    monkeypatch.setattr(frames, "_unfrozen_objects", lambda: 5)
    walks.clear()
    with terminal_gc_policy():
        _with_pending(monkeypatch, frames.FULL_GC_DUE)
        clock.now += frames.FULL_GC_QUIET_INPUT_S
        assert collector.tick()
        assert frames.frozen_count() == 12, "the survivors are added to the count"
        frames.freeze_heap()
        assert frames.frozen_count() == 17
    assert walks == []


def test_a_slow_pass_is_logged_with_its_kind(monkeypatch):
    from loguru import logger

    clock = _Clock()
    collector = IdleCollector(clock)
    ticks = iter([0.0, 1.2])
    monkeypatch.setattr(frames.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(frames.gc, "collect", lambda generation=2: 0)
    monkeypatch.setattr(frames.gc, "freeze", lambda: None)
    monkeypatch.setattr(frames, "_unfrozen_objects", lambda: 3)
    seen: list[str] = []
    sink = logger.add(lambda message: seen.append(str(message)), level="WARNING")
    try:
        collector._collect()
    finally:
        logger.remove(sink)
    assert collector.last_pass_s == 1.2
    assert any("full garbage collection took 1.2s" in line for line in seen)


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
