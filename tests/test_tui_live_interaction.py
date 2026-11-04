# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Exercise navigation and prompt controls while work and history are active."""

import asyncio
import os
import signal
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from textual import on
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget

from navin.tui import resilience
from navin.tui.prefs import TuiPrefs
from navin.tui.runtime import UiFileEdit, UiStreamDelta, UiToolEvent
from navin.tui.screens import PickerScreen, PickItem
from navin.tui.virtual import RecordGap
from navin.tui.widgets import AssistantMessage, QueuedPromptRow, ToolCall, UserMessage, WorkingLine
from tests.test_tui_queue import make_app
from tests.test_tui_working import _InteractionApp


def test_status_click_and_repeated_copy_or_modal_escape_do_not_cancel(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("keep working")
            await app.runtime.bus.consume_inbound()
            await pilot.pause()
            await pilot.click(app.query_one(WorkingLine))
            app.composer.focus()
            app.composer.set_text("draft")
            # First press clears, second only arms quit; neither cancels work.
            await pilot.press("ctrl+c", "ctrl+c")
            assert app.composer.text == ""
            assert app.runtime.bus.inbound_size == 0
            assert not app._quitting
            app._quit_armed_at = 0.0
            await app.push_screen(PickerScreen("Mode", [PickItem("chat", "Chat")]))
            await pilot.press("escape", "escape")
            assert len(app.screen_stack) == 1
            assert app.runtime.bus.inbound_size == 0
            app._navigation_closed_at -= 1
            await pilot.press("escape", "escape")
            assert app.runtime.bus.inbound_size == 1
            assert (await app.runtime.bus.consume_inbound()).content == "/stop"
    asyncio.run(run())


def test_send_selected_queue_item_now_keeps_task_draft_and_other_rows(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("first")
            await app.runtime.bus.consume_inbound()
            block = await app._ensure_assistant()
            started = app.runtime._turn_started_at
            app.prefs.mode = "agent"
            await app.submit_text("later")
            await pilot.pause()
            first_row = app.query(QueuedPromptRow).first()
            first_row.query_one(".queue-send").focus()
            await app.submit_text("use this now")
            await pilot.pause()
            assert app.query(QueuedPromptRow).first() is first_row
            assert app.focused is first_row.query_one(".queue-send")
            app.composer.set_text("unsent draft")
            await pilot.click(app.query(QueuedPromptRow).last().query_one(".queue-send"))
            sent = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 1)
            assert sent.content == "use this now"
            assert app.runtime.bus.inbound_size == 0
            assert app.runtime._turn_started_at == started
            assert app._current is None
            assert block in app._parked_bubbles
            await app._on_runtime_event(UiStreamDelta("I will use that instruction."))
            assert app._current is not block
            order = list(app.transcript.children)
            assert order.index(app.query(UserMessage).last()) < order.index(app._current)
            assert app.composer.text == "unsent draft"
            assert app.query(QueuedPromptRow).first() is first_row
            assert len(app.query(QueuedPromptRow)) == 1
            app.composer.set_text("another instruction")
            await pilot.press("ctrl+enter")
            sent = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 1)
            assert sent.content == "another instruction"
            assert app.runtime._turn_started_at == started
            assert [r.raw_text for r in app.query(UserMessage)] == ["first", "use this now", "another instruction"]
    asyncio.run(run())


def test_send_now_keeps_running_tool_output_below_followup(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("run the checks")
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiToolEvent("run", "exec", "start", {"command": "checks"}))
            await app._on_runtime_event(UiToolEvent("run", "exec", "output", output="first line\npar"))
            original = app._current
            for index in range(2):
                await app.submit_text(f"instruction {index}")
                await pilot.pause()
                await pilot.click(app.query_one(".queue-send"))
                await app.runtime.bus.consume_inbound()
                echo = app.query(UserMessage).last()
                await app._on_runtime_event(UiToolEvent(
                    "run", "exec", "output", output="tial\n" if index == 0 else "still working\n",
                ))
                await pilot.pause()
                order = list(app.transcript.children)
                assert order.index(echo) < order.index(app._current)
                assert app._current is not original
                assert len(app.query(ToolCall)) == 1
                card = app.query_one(ToolCall)
                assert card.arguments == {"command": "checks"}
                assert card.output_lines[:2] == ["first line", "partial"]
                assert card.region.y >= echo.region.bottom
            await app._on_runtime_event(UiToolEvent("run", "exec", "end", result="Exit code: 0"))
            assert app.query_one(ToolCall).phase == "end"
            assert "still working" in app.query_one(ToolCall).copy_text()
            assert [row.raw_text for row in app.query(UserMessage)] == [
                "run the checks", "instruction 0", "instruction 1",
            ]
    asyncio.run(run())


def test_send_now_moves_pending_file_edit_and_keeps_completed_activity(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("edit the files")
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiFileEdit("done.py", call_id="done", added=1))
            await app._on_runtime_event(UiFileEdit("next.py", call_id="edit", phase="start"))
            original = app._current
            await app.submit_text("also handle the error", send_now=True)
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiFileEdit(
                "next.py", call_id="edit", added=2, diff="+one\n+two", phase="end",
            ))
            await app._on_runtime_event(UiToolEvent("edit", "edit_file", "end", result="updated"))
            await pilot.pause()
            assert original.has_tool("done")
            assert not original.has_tool("edit")
            assert app._current.has_tool("edit")
            assert len(app.query(ToolCall)) == 2
            card = app._current.query_one(ToolCall)
            assert card.file_path == "next.py"
            assert card.diff_text == "+one\n+two"
            assert card.added == 2
            assert card.phase == "end"
            order = list(app.transcript.children)
            assert order.index(app.query(UserMessage).last()) < order.index(app._current)
    asyncio.run(run())


def test_failed_send_now_retains_queue_draft_and_active_turn(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(90, 30)) as pilot:
            await app.submit_text("first")
            await app.runtime.bus.consume_inbound()
            await app.submit_text("retry me")
            app.composer.set_text("draft")
            await pilot.pause()
            with patch.object(app.runtime, "send", new=AsyncMock(side_effect=RuntimeError("offline"))):
                await pilot.click(app.query_one(".queue-send"))
                await pilot.pause()
            assert app.runtime.turn_active
            assert app._awaiting_reply
            assert app.composer.text == "draft"
            assert [r.raw_text for r in app.query(UserMessage)] == ["first"]
            assert app._queued_prompts[app.runtime.session_key][0].text == "retry me"
    asyncio.run(run())


def _loaded_blocks(transcript) -> int:
    """Blocks in the chat, widgets or records paged out far from the viewport.

    A slow CI runner gives the virtualizer time to turn far blocks into a
    RecordGap between two history pages: they are still loaded.
    """
    return sum(len(child.records) if isinstance(child, RecordGap) else 1 for child in transcript.children)


def test_session_opens_at_latest_page_and_scroll_loads_older_without_losing_place(tmp_path):
    async def run():
        app = make_app(tmp_path)
        history = []
        for i in range(100):
            history.extend([
                {"role": "user", "content": f"question {i}"},
                {"role": "assistant", "content": f"answer {i}\n\nDetails of answer {i}."},
            ])
        app.runtime.history_snapshot = lambda: list(history)
        async with app.run_test(size=(100, 32)) as pilot:
            app.composer.set_text("my draft")
            frames = []
            original_display = app._display

            def record_display(screen, renderable):
                if not app._batch_count and app.transcript.visible and app.transcript.children:
                    frames.append((len(app.transcript.children), app.transcript.is_vertical_scroll_end))
                return original_display(screen, renderable)

            with patch.object(app, "_display", record_display):
                await app._render_history()
                await pilot.pause()
            assert frames
            # The latest exchanges paint first and older rows are inserted
            # above them: every frame stays pinned to the end, the page only
            # grows, and it ends complete.
            assert all(at_end for _count, at_end in frames), frames
            counts = [count for count, _at_end in frames]
            assert counts == sorted(counts), frames
            assert counts[-1] == 12, frames
            transcript = app.transcript
            assert len(transcript.children) == 12
            assert app.query(UserMessage).first().raw_text == "question 94"
            assert app.query(AssistantMessage).last().text.startswith("answer 99")
            assert transcript.is_vertical_scroll_end
            assert transcript.has_older
            await pilot.resize_terminal(48, 30)
            await pilot.pause()
            assert transcript.is_vertical_scroll_end
            await pilot.resize_terminal(100, 32)
            await pilot.pause()
            assert transcript.is_vertical_scroll_end
            last = transcript.children[-1]
            first_y = transcript.children[0].virtual_region.y
            # Down at the end never asks for history.
            transcript.nudge(1000)
            await pilot.pause()
            assert len(transcript.children) == 12
            transcript.scroll_to(y=0, animate=False, immediate=True)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert _loaded_blocks(transcript) == 24
            anchor = list(transcript.children)[12]
            assert anchor.region.y == transcript.content_region.y + first_y
            assert transcript.children[-1] is last
            assert app.composer.text == "my draft"
            assert app.focused is app.composer
            assert not transcript.auto_follow
            assert not transcript.loading_history
            assert app.query(UserMessage).first().raw_text == "question 88"
            transcript.scroll_to(y=0, animate=False, immediate=True)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert _loaded_blocks(transcript) == 36
            assert app.query(UserMessage).first().raw_text == "question 82"
    asyncio.run(run())


def test_older_history_loads_a_screen_ahead_and_the_chat_stays_on_screen(tmp_path):
    from textual import events

    async def run():
        app = make_app(tmp_path)
        history = []
        for i in range(60):
            history.extend([
                {"role": "user", "content": f"question {i}"},
                {"role": "assistant", "content": f"answer {i}\n\nDetails of answer {i}."},
            ])
        app.runtime.history_snapshot = lambda: list(history)
        async with app.run_test(size=(100, 32)) as pilot:
            await app._render_history()
            await pilot.pause()
            transcript = app.transcript
            assert transcript.has_older
            # Still a few rows below the top: the older page is already asked for.
            transcript.scroll_to(y=min(transcript.max_scroll_y, 8), animate=False, immediate=True)
            await pilot.pause()
            hidden_frames = []
            original_display = app._display

            def record_display(screen, renderable):
                if not app._batch_count and transcript.styles.visibility == "hidden":
                    hidden_frames.append(renderable)
                return original_display(screen, renderable)

            with patch.object(app, "_display", record_display):
                x, y = transcript.region.x + 5, transcript.region.y + 5
                transcript.post_message(events.MouseScrollUp(
                    transcript, x, y, 0, 0, 0, False, False, False, screen_x=x, screen_y=y,
                ))
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
            assert _loaded_blocks(transcript) == 24
            assert not hidden_frames, "the chat never blanks while older messages load"
            assert not transcript.loading_history
    asyncio.run(run())


def test_input_and_picker_work_while_code_index_is_waiting(tmp_path):
    from navin.agent.tools.code_index import CodeIndexTool

    async def run():
        app = make_app(tmp_path)
        tool = CodeIndexTool(workspace=tmp_path)
        started, release = threading.Event(), threading.Event()

        def prepare(*_):
            started.set()
            release.wait(5)
            return SimpleNamespace(stats=None)

        async with app.run_test(size=(100, 32)) as pilot:
            with patch.object(tool, "_prepare_index", prepare), patch.object(tool, "_query_index", return_value="ready"):
                indexing = asyncio.create_task(tool.execute(action="overview"))
                try:
                    assert await asyncio.to_thread(started.wait, 1)
                    await pilot.press(*"fluide")
                    await app.push_screen(PickerScreen("Mode", [PickItem("chat", "Chat"), PickItem("agent", "Agent")]))
                    await pilot.press("down", "enter")
                    assert len(app.screen_stack) == 1
                    assert app.composer.text == "fluide"
                    assert not indexing.done(), "indexing blocked the interface until completion"
                finally:
                    release.set()
                    assert await indexing == "ready"
    asyncio.run(run())


def test_output_bursts_preserve_partial_lines_snapshots_and_copy(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)):
            row = ToolCall("run", "exec", {"command": "checks"})
            await app.transcript.mount(row)
            for part in ["par", "tial\n", "second", " line\n"]:
                row.apply(phase="output", output=part)
            assert row.output_lines == ["partial", "second line"]
            row.apply(phase="output", output="snapshot\n", output_mode="snapshot")
            row.apply(phase="output", output="tail")
            assert row.output_lines == ["snapshot", "tail"]
            assert "tail" in row.copy_text()
            row.apply(phase="output", output="\n" + "line\n" * 6000)
            row.apply(phase="output", output="last")
            row.apply(phase="end")
            assert len(row.output_lines) == 5000
            assert row.output_lines[-1] == "last"
    asyncio.run(run())


def test_double_ctrl_c_on_an_empty_prompt_quits_and_a_single_press_does_not(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            app.composer.focus()
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert not app._quitting
            app._quit_armed_at -= app.QUIT_PRESS_WINDOW_S + 1
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert not app._quitting, "presses far apart only re-arm"
            with patch.object(app.runtime, "close", AsyncMock()):
                await pilot.press("ctrl+c")
                await pilot.pause()
                assert app._quitting
    asyncio.run(run())


@pytest.fixture
def black_box(tmp_path, monkeypatch):
    log = tmp_path / "black-box.log"
    monkeypatch.setattr(resilience, "log_path", lambda: log)
    return log


class FlakyBox(Widget):
    """A widget whose first ping fails: its message loop must outlive it."""

    class Ping(Message):
        pass

    def __init__(self) -> None:
        super().__init__()
        self.pings = 0

    @on(Ping)
    def _ping(self, _message: Ping) -> None:
        self.pings += 1
        if self.pings == 1:
            raise LookupError("first ping fails")


class _BrokenActionApp(_InteractionApp):
    BINDINGS = [Binding("f9", "boom", priority=True)]

    def action_boom(self) -> None:
        raise KeyError("app action failed")


def _make(app_class, tmp_path):
    prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
    prefs.save = lambda: None
    app = app_class(SimpleNamespace(workspace_path=Path(tmp_path)), prefs=prefs)
    app.theme = "navin"
    return app


def test_an_interface_error_leaves_the_cli_open_shown_and_in_the_black_box(tmp_path, black_box):
    """Textual closed the app, and every turn and subagent with it, on the
    first error a worker, timer or handler let through (2026-11-03)."""
    async def run():
        app = _make(_BrokenActionApp, tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            assert app._navin_ui_ready

            async def broken_switch():
                raise RuntimeError("session switch broke")

            app.run_worker(broken_switch(), group="picker")  # exit_on_error, as _spawn
            await pilot.pause()
            await pilot.pause()

            def broken_tick():
                raise ZeroDivisionError("timer broke")

            app.set_timer(0.01, broken_tick)
            await pilot.pause(0.05)
            await pilot.press("f9")  # an app handler: its loop is the app's
            await pilot.pause()
            flaky = FlakyBox()
            await app.transcript.mount(flaky)
            flaky.post_message(FlakyBox.Ping())
            flaky.post_message(FlakyBox.Ping())
            await pilot.pause()
            await pilot.pause()

            assert app.is_running and not app._exit and app.return_code is None
            assert flaky.pings == 2, "the widget kept answering after its error"
            shown = [note.message for note in app._notifications if "Internal error" in note.message]
            assert len(shown) == 4 and all("The CLI stays open" in text for text in shown)
            assert isinstance(app._exception, RuntimeError), "tests still see the first error"
            app._exception = None  # expected here
            await app.submit_text("still here")
            assert (await app.runtime.bus.consume_inbound()).content == "still here"
        text = black_box.read_text(encoding="utf-8")
        assert f"[pid {os.getpid()} project " in text
        for line in (
            "ui error: RuntimeError: session switch broke; the CLI kept running",
            "ui error: ZeroDivisionError: timer broke; the CLI kept running",
            "ui error: KeyError: 'app action failed' in _BrokenActionApp; the CLI kept running",
            "ui error: LookupError: first ping fails in FlakyBox; the CLI kept running",
        ):
            assert line in text
        assert "in broken_switch" in text and "in action_boom" in text, "each with its stack"
    asyncio.run(run())


def test_a_repeated_interface_error_is_counted_not_repeated(tmp_path, black_box):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            try:
                raise ValueError("every tick")
            except ValueError as error:
                failure = error
            for _ in range(12):
                app._report_ui_error(failure)
            await pilot.pause()  # a toast is a message: shown once processed
            assert len([note for note in app._notifications if "Internal error" in note.message]) == 1
            app._exception = None
        lines = black_box.read_text(encoding="utf-8").splitlines()
        assert sum("ui error: ValueError: every tick; the CLI kept running" in line for line in lines) == 1
        assert sum("ValueError: every tick (repeated 10 times)" in line for line in lines) == 1
        assert not any("repeated 11" in line or "repeated 12" in line for line in lines)
    asyncio.run(run())


def test_a_cli_that_cannot_start_stays_fatal_and_says_why(tmp_path, black_box):
    class BrokenStart(_InteractionApp):
        async def on_mount(self, event):
            raise RuntimeError("engine never started")

    async def run():
        app = _make(BrokenStart, tmp_path)
        with pytest.raises(RuntimeError, match="engine never started"):
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
        assert app._exit_reason.startswith("fatal error: RuntimeError: engine never started")
    asyncio.run(run())
    text = black_box.read_text(encoding="utf-8")
    assert "fatal error: the CLI closes on it" in text and "engine never started" in text


def test_quitting_with_work_running_here_asks_first_with_cancel_first(tmp_path, black_box):
    """A double Ctrl+C meant as a copy, a Ctrl+Q or a typed exit stopped the
    turn and the subagents on screen without a word."""
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            app.runtime.work_here = lambda: 2
            screens: list[PickerScreen] = []
            answers = ["no", "yes"]

            async def answer(screen):
                screens.append(screen)
                return answers.pop(0)

            app.push_screen_wait = answer
            app.exit = Mock()
            app.runtime.close = AsyncMock()
            await app.submit_text("exit")  # typed: the same door as Ctrl+Q
            await pilot.pause()
            await pilot.pause()
            assert len(screens) == 1
            assert [item.id for item in screens[0]._items] == ["no", "yes"], "Enter keeps the work"
            assert screens[0]._title == "Work is still running"
            assert "2 tasks running here stop if you quit now." in screens[0]._hint
            app.exit.assert_not_called()
            assert not app._quitting
            await pilot.press("ctrl+q")
            for _ in range(10):
                if app.exit.called:
                    break
                await pilot.pause()
            app.exit.assert_called_once()
            assert app._exit_reason == "quit (ctrl+q)"
            if app._runtime_close_task is not None:
                await app._runtime_close_task
    asyncio.run(run())
    assert "quit: ctrl+q; work stopped: 2 here, 0 in other projects" in black_box.read_text(encoding="utf-8")


def test_a_stop_signal_is_recorded_and_quits_through_the_normal_path(tmp_path, black_box):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            app.runtime.work_here = lambda: 1
            app.push_screen_wait = AsyncMock(side_effect=AssertionError("a closed terminal answers nothing"))
            app.exit = Mock()
            app.runtime.close = AsyncMock()
            app._on_stop_signal("SIGHUP")
            for _ in range(10):
                if app.exit.called:
                    break
                await pilot.pause()
            app.exit.assert_called_once()
            if app._runtime_close_task is not None:
                await app._runtime_close_task
            app.runtime.close.assert_awaited()  # sessions flushed, as on Ctrl+Q
    asyncio.run(run())
    text = black_box.read_text(encoding="utf-8")
    assert "signal: SIGHUP: the terminal was closed; work stopped: 1 here, 0 in other projects" in text
    assert "quit: signal SIGHUP;" in text


def test_a_stop_signal_ends_the_cli_even_when_its_clean_quit_never_comes(tmp_path, black_box, monkeypatch):
    """Handled by the loop, SIGTERM waited for the loop to be back at its
    selector: `kill` did nothing to a CLI whose loop was stuck, and a second
    SIGTERM did nothing either."""
    forced: list[int] = []
    restores: list[object] = []

    def fake_exit(signum, restore=None):
        forced.append(signum)
        restores.append(restore)

    monkeypatch.setattr(resilience, "force_exit", fake_exit)

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            loop = asyncio.get_running_loop()
            app.exit = Mock()  # the clean quit never closes this CLI
            app.runtime.close = AsyncMock()
            app._stop_watchdog = resilience.StopWatchdog(0.05, app._project_label, restore=app._restore_terminal_now)
            app._stop_signal_received(loop, "SIGTERM", signal.SIGTERM)
            assert forced == [], "first a clean quit, asked of the loop"
            for _ in range(10):
                if app.exit.called:
                    break
                await pilot.pause()
            app.exit.assert_called_once()
            app._stop_watchdog.thread.join(timeout=5)
            assert forced == [signal.SIGTERM], "then the watchdog ends it"
            app._stop_signal_received(loop, "SIGTERM", signal.SIGTERM)
            assert forced == [signal.SIGTERM, signal.SIGTERM], "a second signal ends it at once"
            closed = asyncio.new_event_loop()
            closed.close()
            app._stop_signal = None
            app._stop_signal_received(closed, "SIGHUP", signal.SIGHUP)
            assert forced[-1] == signal.SIGHUP, "after the loop: at once"
            assert restores == [app._restore_terminal_now] * 3, "every forced exit gives the terminal back"
            if app._runtime_close_task is not None:
                await app._runtime_close_task
    asyncio.run(run())
    text = black_box.read_text(encoding="utf-8")
    assert "signal: SIGTERM: navin-cli did not close within 0.05 s, forced exit" in text
    assert "signal: SIGTERM again: exit now" in text
    assert "signal: SIGHUP while closing: exit now" in text
