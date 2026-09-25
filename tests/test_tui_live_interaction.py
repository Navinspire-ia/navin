# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Exercise navigation and prompt controls while work and history are active."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from navin.tui.runtime import UiFileEdit, UiStreamDelta, UiToolEvent
from navin.tui.screens import PickerScreen, PickItem
from navin.tui.widgets import AssistantMessage, QueuedPromptRow, ToolCall, UserMessage, WorkingLine
from tests.test_tui_queue import make_app


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
            assert all(count == 12 and at_end for count, at_end in frames), frames
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
            assert len(transcript.children) == 24
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
            assert len(transcript.children) == 36
            assert app.query(UserMessage).first().raw_text == "question 82"
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
