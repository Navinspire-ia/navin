# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from textual import events

from navin.bus.events import OutboundMessage
from navin.bus.queue import MessageBus
from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.widgets import (
    CompactionLine,
    PromptQueue,
    QueuedPromptRow,
    Sidebar,
    SystemNote,
    WorkingLine,
    format_elapsed,
    format_working_line,
)


class FormatElapsedTests(unittest.TestCase):
    def test_seconds_minutes_hours(self) -> None:
        self.assertEqual(format_elapsed(12), "12s")
        self.assertEqual(format_elapsed(654), "10m 54s")
        self.assertEqual(format_elapsed(3723), "1h 02m")


class TerminalBackgroundTests(unittest.TestCase):
    def test_background_covers_terminal_padding_and_is_restored(self):
        driver = Mock(is_headless=False, is_inline=False)
        host = SimpleNamespace(_driver=driver, ansi_color=False, no_color=False,
                               current_theme=SimpleNamespace(background="#000000"))
        NavinApp._sync_terminal_background(host, "navin")
        driver.write.assert_called_once_with("\x1b]11;#000000\x1b\\")
        host.current_theme.background = "#F5F5F5"
        NavinApp._sync_terminal_background(host, "navin-light")
        self.assertEqual(driver.write.call_args.args[0], "\x1b]11;#F5F5F5\x1b\\")
        NavinApp._restore_terminal_background(host)
        self.assertEqual(driver.write.call_args.args[0], "\x1b]111\x1b\\")
        driver.flush.assert_called_once()
        self.assertFalse(host._terminal_background_set)

    def test_headless_and_inline_sessions_leave_terminal_colors_alone(self):
        for headless, inline in [(True, False), (False, True)]:
            driver = Mock(is_headless=headless, is_inline=inline)
            host = SimpleNamespace(_driver=driver)
            NavinApp._sync_terminal_background(host, "navin")
            driver.write.assert_not_called()

    def _background_host(self):
        driver = Mock(is_headless=False, is_inline=False)
        host = SimpleNamespace(
            _driver=driver, ansi_color=False, no_color=False, theme="navin",
            current_theme=SimpleNamespace(background="#181A1D"), _terminal_background_set=False,
        )
        host._sync_terminal_background = lambda theme: NavinApp._sync_terminal_background(host, theme)
        host._restore_terminal_background = lambda: NavinApp._restore_terminal_background(host)
        host._reassert_terminal_background = lambda: NavinApp._reassert_terminal_background(host)
        return host, driver

    def test_a_lost_padding_colour_is_written_again_on_focus_and_after_a_suspend(self):
        # Windows Terminal puts the profile colour back in its padding when it
        # reloads its settings: the purple strip beside the CLI. A focus
        # writes the colour again, and only when this run had set it.
        host, driver = self._background_host()
        NavinApp.on_app_focus(host, events.AppFocus())
        driver.write.assert_not_called()
        NavinApp._sync_terminal_background(host, "navin")
        driver.write.reset_mock()
        NavinApp.on_app_focus(host, events.AppFocus())
        driver.write.assert_called_once_with("\x1b]11;#181A1D\x1b\\")
        driver.flush.assert_called()
        # A suspend hands the shell its own colours; a focus meanwhile writes
        # nothing; the resume sets the CLI's again.
        NavinApp._on_terminal_suspend(host, None)
        self.assertEqual(driver.write.call_args.args[0], "\x1b]111\x1b\\")
        driver.write.reset_mock()
        NavinApp.on_app_focus(host, events.AppFocus())
        driver.write.assert_not_called()
        NavinApp._on_terminal_resume(host, None)
        driver.write.assert_called_once_with("\x1b]11;#181A1D\x1b\\")
        self.assertTrue(host._terminal_background_set)

    def test_start_up_writes_the_colour_once_more_and_follows_suspend_and_resume(self):
        # A new Windows Terminal tab loads its profile after the program's
        # first output: one more write shortly after start covers it.
        host = SimpleNamespace(
            set_timer=Mock(), app_suspend_signal=Mock(), app_resume_signal=Mock(),
            _reassert_terminal_background=Mock(), _on_terminal_suspend=Mock(), _on_terminal_resume=Mock(),
        )
        NavinApp._keep_terminal_background(host)
        host.set_timer.assert_called_once_with(
            NavinApp._BACKGROUND_REASSERT_DELAY_S, host._reassert_terminal_background,
        )
        self.assertLessEqual(NavinApp._BACKGROUND_REASSERT_DELAY_S, 3.0)
        host.app_suspend_signal.subscribe.assert_called_once_with(host, host._on_terminal_suspend)
        host.app_resume_signal.subscribe.assert_called_once_with(host, host._on_terminal_resume)


class FormatWorkingLineTests(unittest.TestCase):
    def test_navin_keys_not_cursor_keys(self) -> None:
        line = format_working_line(elapsed_s=654, background=0)
        self.assertEqual(line, "Working (10m 54s • esc to interrupt)")
        self.assertNotIn("/stop", line)
        self.assertNotIn("/ps", line)

    def test_one_background_terminal(self) -> None:
        line = format_working_line(elapsed_s=12, background=1)
        self.assertIn("esc to interrupt", line)
        self.assertIn("1 background terminal running", line)
        self.assertIn("/ps to view", line)
        self.assertNotIn("/stop to close", line)

    def test_several_background_terminals(self) -> None:
        line = format_working_line(elapsed_s=5, background=3)
        self.assertIn("3 background terminals running", line)
        self.assertIn("/ps to view", line)


class WorkingLineWidgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_hidden_until_set(self) -> None:
        from textual.app import App, ComposeResult

        from navin.tui.widgets import WorkingLine

        class Host(App):
            def compose(self) -> ComposeResult:
                yield WorkingLine(id="working")

        app = Host()
        async with app.run_test(size=(80, 12)) as _pilot:
            line = app.query_one("#working", WorkingLine)
            self.assertFalse(line.has_class("-visible"))
            line.set_line(format_working_line(elapsed_s=12, background=1))
            self.assertTrue(line.has_class("-visible"))
            self.assertIn("esc to interrupt", str(line.content))
            self.assertIn("/ps to view", str(line.content))
            line.set_line("")
            self.assertFalse(line.has_class("-visible"))

    async def test_compaction_bar_animates_only_the_unmeasured_part(self) -> None:
        from textual.app import App, ComposeResult

        class Host(App):
            def compose(self) -> ComposeResult:
                yield CompactionLine(id="compaction")
                yield WorkingLine(id="working")

        app = Host()
        async with app.run_test(size=(92, 14)) as _pilot:
            line = app.query_one(CompactionLine)
            working = app.query_one(WorkingLine)
            self.assertFalse(line.display)
            line.update_progress("start", "Preparing the archive", None)
            working.set_line("Working (7s • esc to interrupt)")
            pending = str(line.content)
            self.assertIn("[", pending)
            self.assertIn("measuring", pending)
            self.assertNotIn("%", pending)
            line.tick(frame=5)
            self.assertNotEqual(str(line.content), pending)
            line.update_progress("summarizing", "Summarizing 40 messages", 0, frame=5)
            self.assertIn("0%", str(line.content))
            line.update_progress("measuring", "40 messages archived", 61, frame=5)
            self.assertIn("61%", str(line.content))
            self.assertIn("40 messages archived", str(line.content))
            self.assertTrue(working.display)
            line.update_progress("partial", "No safe boundary", 61)
            self.assertIn("61%", str(line.content))
            self.assertNotIn("100%", str(line.content))
            line.update_progress("done", "Replay archive saved, token estimate unavailable", None)
            self.assertIn("[saved]", str(line.content))
            self.assertNotIn("100%", str(line.content))
            line.reset()
            self.assertFalse(line.display)
            self.assertTrue(working.display)


class _InteractionApp(NavinApp):
    """Real chat controls and bus, without account/network startup."""

    async def on_mount(self, event):
        event.prevent_default()
        self.runtime.bus = MessageBus()
        self._engine_ready = True
        self.query_one(Sidebar).display = False
        self.composer.focus()
        self.set_interval(0.12, self._tick_spinner)

    async def on_unmount(self, event):
        event.prevent_default()
        self.runtime._closed = True
        tasks = [task for task in (self.runtime._loop_task, self.runtime._consumer_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _refresh_side(self):
        self._set_status()

    def _load_account(self, *args, **kwargs):
        pass


class ChatInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        self.app = _InteractionApp(SimpleNamespace(workspace_path=Path(self.directory.name)), prefs=prefs)

    async def test_ctrl_c_copies_selected_input_without_stopping_work(self):
        app = self.app
        async with app.run_test(size=(72, 25)) as pilot:
            await app.submit_text("keep working")
            await app.runtime.bus.consume_inbound()
            app.composer.set_text("copy this")
            app.composer.focus()
            await pilot.press("ctrl+a")
            with patch.object(app, "copy_to_clipboard") as copy:
                await pilot.press("ctrl+c")
                copy.assert_called_once_with("copy this")
            self.assertTrue(app.runtime.turn_active)
            self.assertEqual(app.runtime.bus.inbound_size, 0)
            self.assertEqual(app.composer.text, "copy this")
            # Transcript selections receive the same priority.
            with patch.object(app.screen, "get_selected_text", return_value="selected output"), patch.object(app, "copy_to_clipboard") as copy:
                await pilot.press("ctrl+c")
                copy.assert_called_once_with("selected output")
            self.assertEqual(app.runtime.bus.inbound_size, 0)

    async def test_ctrl_v_full_payload_first_enter_working_and_ctrl_c(self):
        app = self.app
        payload = "première ligne\n" + "données العربية [x]\n" * 600 + "fin"
        async with app.run_test(size=(72, 25)) as pilot:
            app._clipboard = "old" * 12000
            app.composer.set_text("replace this selection")
            await pilot.press("ctrl+a")
            with patch("navin.tui.clipboard.read_clipboard", return_value=payload):
                await pilot.press("ctrl+v")
                await app.workers.wait_for_complete()
            self.assertEqual(app.composer.text, f"[Pasted Content {len(payload)} chars]")
            await pilot.press("enter")
            message = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 1)
            self.assertEqual(message.content, payload)
            app.runtime._turn_started_at = time.monotonic() - 380
            app._tick_spinner()
            await pilot.pause()
            self.assertIn("Working (6m 20s", str(app.query_one(WorkingLine).content))
            self.assertIn("Working", app.export_screenshot())
            block = await app._ensure_assistant()
            await block.set_text("Checking the request...")
            app.prefs.mode = "agent"
            started = app.runtime._turn_started_at
            await app.submit_text("also check this")
            self.assertEqual(app.runtime.bus.inbound_size, 0)
            self.assertEqual(app._queued_prompts[app.runtime.session_key][0].text, "also check this")
            self.assertTrue(app.query_one(PromptQueue).display)
            self.assertEqual(app.runtime._turn_started_at, started)
            self.assertEqual(app.runtime.bus.inbound_size, 0)
            self.assertIs(app.transcript.children[-1], block)
            app.prefs.mode = "chat"
            await pilot.press("ctrl+c")
            self.assertEqual(app.runtime.bus.inbound_size, 0)
            await pilot.press("escape")
            stop = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 1)
            self.assertEqual(stop.content, "/stop")
            await app.runtime._dispatch(OutboundMessage("cli", "direct", "Stopped."))
            await pilot.pause()
            self.assertFalse(app.query_one(WorkingLine).display)
            self.assertIn(app.runtime.session_key, app._queue_paused)
            await pilot.click(app.query_one(QueuedPromptRow).query_one(".queue-remove"))
            self.assertFalse(app.query_one(PromptQueue).display)
            # Terminal-managed Ctrl+V sends Paste, not a key. Its payload wins
            # over the old in-app clipboard and does not consume the next Enter.
            app.transcript.focus()
            app.post_message(events.Paste("nouveau\ntexte"))
            await pilot.pause()
            self.assertEqual(app.composer.text, "nouveau\ntexte")
            await pilot.press("ctrl+c")
            self.assertEqual(app.composer.text, "")
            messages = list(app.transcript.children)
            await pilot.press("ctrl+c")
            self.assertEqual(list(app.transcript.children), messages)
            self.assertTrue(messages)
            app.action_find()
            await pilot.pause()
            with patch("navin.tui.clipboard.read_clipboard", return_value="needle"):
                await pilot.press("ctrl+v")
                await app.workers.wait_for_complete()
                await pilot.pause()
            self.assertEqual(app.query_one("#find-query").value, "needle")
            await pilot.press("escape")
            app.composer.focus()
            await pilot.press("o", "k", "enter")
            self.assertEqual((await app.runtime.bus.consume_inbound()).content, "ok")

    async def test_slow_clipboard_keeps_ctrl_c_responsive_and_cannot_restore_cleared_text(self):
        started, release = threading.Event(), threading.Event()

        def slow_read():
            started.set()
            release.wait(2)
            return "late clipboard text" * 200

        async with self.app.run_test(size=(72, 25)) as pilot:
            with patch("navin.tui.clipboard.read_clipboard", side_effect=slow_read):
                try:
                    await pilot.press("ctrl+v")
                    self.assertTrue(await asyncio.to_thread(started.wait, 1))
                    await pilot.press("ctrl+c")
                    self.assertFalse(release.is_set())
                finally:
                    release.set()
                await self.app.workers.wait_for_complete()
            self.assertEqual(self.app.composer.text, "")

    async def test_recovered_work_is_visible_and_dead_engine_can_answer_next_message(self):
        from navin.bus.runtime_events import (
            RuntimeEventBus,
            RuntimeEventContext,
            SessionTurnStarted,
        )
        from navin.tui.runtime import UiAssistantMessage, UiEngineError, UiTurnEnd

        app = self.app
        attempts = 0
        turn_ended = asyncio.Event()
        error_rendered = asyncio.Event()
        answer_rendered = asyncio.Event()
        second_turn_ended = asyncio.Event()
        render_event = app.runtime._on_event

        async def observe_event(event):
            await render_event(event)
            if isinstance(event, UiTurnEnd):
                turn_ended.set()
                if answer_rendered.is_set():
                    second_turn_ended.set()
            elif isinstance(event, UiEngineError) and "lost connection" in event.text:
                error_rendered.set()
            elif isinstance(event, UiAssistantMessage) and event.text == "Received: second":
                answer_rendered.set()

        async def engine():
            nonlocal attempts
            attempts += 1
            message = await app.runtime.bus.consume_inbound()
            if attempts == 1:
                raise RuntimeError("lost connection")
            await app.runtime.bus.publish_outbound(OutboundMessage("cli", "direct", f"Received: {message.content}"))
            await asyncio.Event().wait()

        async with app.run_test(size=(72, 25)) as pilot:
            app.runtime._on_event = observe_event
            event_bus = RuntimeEventBus()
            app.runtime.agent_loop = SimpleNamespace(run=engine, runtime_events=event_bus)
            app.runtime._refresh_status = lambda: None
            app.runtime._subscribe_runtime_events()
            await event_bus.publish(SessionTurnStarted(RuntimeEventContext("cli", "direct", "cli:direct"), "Recovered task"))
            await pilot.pause()
            self.assertTrue(app.query_one(WorkingLine).display)
            active = asyncio.create_task(asyncio.Event().wait())
            app.runtime.agent_loop._active_tasks = {"cli:direct": [active]}
            try:
                await app.runtime._dispatch(OutboundMessage("cli", "direct", "Side command result"))
                self.assertTrue(app.runtime.turn_active)
            finally:
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)
            app.runtime._finish_turn({})
            # Turn-end rendering and the failed engine's done callback run in
            # separate tasks. Synchronize with their rendered events, not an
            # arbitrary paint pause or a fixed sleep under suite load.
            await asyncio.wait_for(turn_ended.wait(), 5)
            turn_ended.clear()
            await app.submit_text("first")
            await asyncio.wait_for(error_rendered.wait(), 5)
            await asyncio.wait_for(turn_ended.wait(), 5)
            self.assertFalse(app.runtime.turn_active)
            self.assertTrue(any("lost connection" in str(note.content) for note in app.query(SystemNote)))
            turn_ended.clear()
            await app.submit_text("second")
            await asyncio.wait_for(answer_rendered.wait(), 5)
            await asyncio.wait_for(second_turn_ended.wait(), 5)
            self.assertEqual(attempts, 2)
            self.assertEqual(app._current.text, "Received: second")
            self.assertFalse(app.query_one(WorkingLine).display)


if __name__ == "__main__":
    unittest.main()


def test_the_working_line_names_the_model_that_took_over() -> None:
    from navin.tui.widgets import format_working_line

    relayed = format_working_line(elapsed_s=12, activity="Working…", relay="Claude Fable 5.1")

    assert relayed.startswith("Claude Fable 5.1 · Working… (")
    assert format_working_line(elapsed_s=12, activity="Working…").startswith("Working… (")


def test_compaction_progress_is_scoped_to_its_own_session_and_cleans_on_stop(tmp_path) -> None:
    from navin.tui.runtime import UiCompactionProgress, UiTurnEnd

    async def run() -> None:
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        app = _InteractionApp(SimpleNamespace(workspace_path=tmp_path), prefs=prefs)
        async with app.run_test(size=(112, 34)) as pilot:
            await app.submit_text("Please summarize this long chat")
            await app.runtime.bus.consume_inbound()
            assert app.runtime.turn_active
            owner = {"session_key": app.runtime.session_key, "workspace": str(app.runtime.workspace)}
            row = app.query_one(CompactionLine)
            assert not row.display
            await app._render_runtime_event(UiCompactionProgress(
                "start", "Preparing the archive", **owner,
            ))
            await pilot.pause()
            assert row.display and "measuring" in str(row.content)
            assert not any("Compacting context" in str(child.content) for child in app.transcript.children if hasattr(child, "content"))
            app._tick_spinner()
            assert "Compacting context" in str(row.content)
            assert "Working" in str(app.query_one(WorkingLine).content)
            await app._render_runtime_event(UiCompactionProgress(
                "measuring", "41 messages archived", 72, **owner,
            ))
            assert "72%" in str(row.content)
            await app._render_runtime_event(UiCompactionProgress(
                "measuring", "foreign chat", 99,
                session_key="cli:another", workspace=str(app.runtime.workspace),
            ))
            await app._render_runtime_event(UiCompactionProgress(
                "measuring", "foreign project", 99,
                session_key=app.runtime.session_key, workspace="/another/project",
            ))
            assert "72%" in str(row.content) and "foreign" not in str(row.content)
            await app._render_runtime_event(UiCompactionProgress(
                "partial", "No safe boundary", 72, **owner,
            ))
            assert "72%" in str(row.content) and "100%" not in str(row.content)
            app.runtime.status.turn_active = False
            await app._render_runtime_event(UiTurnEnd())
            assert not row.display

    asyncio.run(run())


def test_compaction_row_clears_before_changing_sessions_even_if_the_switch_fails(tmp_path) -> None:
    from navin.tui.runtime import UiCompactionProgress

    async def run() -> None:
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        app = _InteractionApp(SimpleNamespace(workspace_path=tmp_path), prefs=prefs)
        async with app.run_test(size=(112, 34)):
            await app.submit_text("A working session")
            await app.runtime.bus.consume_inbound()
            row = app.query_one(CompactionLine)
            await app._render_runtime_event(UiCompactionProgress(
                "summarizing", "Summarizing the first chat", 34,
                session_key=app.runtime.session_key, workspace=str(app.runtime.workspace),
            ))
            assert row.display and "34%" in str(row.content)
            with patch.object(app.runtime, "switch_session", side_effect=RuntimeError("switch failed")):
                try:
                    await app._show_session("cli:second")
                except RuntimeError as error:
                    assert "switch failed" in str(error)
                else:
                    raise AssertionError("A failed session switch must surface its error")
            assert not row.display
            assert app._compaction_session is None

    asyncio.run(run())


def test_compaction_progress_routes_to_a_dedicated_row_not_the_transcript() -> None:
    from navin.bus.events import OUTBOUND_META_AGENT_UI
    from navin.bus.outbound_events import ProgressEvent, outbound_message_for_event
    from navin.tui.runtime import TuiRuntime, UiCompactionProgress
    from navin.utils.task_progress import build_agent_ui_blob

    rows = []
    runtime = SimpleNamespace(
        agent_loop=SimpleNamespace(channels_config=None),
        status=SimpleNamespace(relay_model=""),
        workspace=Path("/tmp/navin-test"),
        _emit=lambda row: rows.append(row),
    )

    async def capture(row):
        rows.append(row)

    runtime._emit = capture

    def message(data):
        return outbound_message_for_event(
            channel="cli", chat_id="direct", event=ProgressEvent(content=""),
            metadata={OUTBOUND_META_AGENT_UI: build_agent_ui_blob(data)}, content="",
        )

    event = ProgressEvent(content="")
    asyncio.run(TuiRuntime._dispatch_progress(runtime, message({
        "phase": "compacting", "state": "summarizing", "step": "Waiting for the model",
    }), event))
    asyncio.run(TuiRuntime._dispatch_progress(runtime, message({
        "phase": "compacting", "state": "measuring", "step": "Reduced tokens", "percent": 63,
    }), event))
    assert len(rows) == 2
    assert all(isinstance(row, UiCompactionProgress) for row in rows)
    assert rows[0].percent is None and rows[1].percent == 63
    assert rows[0].session_key == "cli:direct"
    assert rows[0].workspace == "/tmp/navin-test"


def test_the_cli_hears_which_model_took_over_and_when_the_chosen_one_is_back() -> None:
    import asyncio
    from types import SimpleNamespace

    from navin.tui.runtime import TuiRuntime

    runtime = SimpleNamespace(agent_loop=SimpleNamespace(channels_config=None), status=SimpleNamespace(relay_model=""))
    event = SimpleNamespace(
        reasoning_end=False, reasoning=False, reasoning_delta=False,
        tool_events=None, file_edit_events=None, tool_hint=False,
    )

    def relay(name: str) -> SimpleNamespace:
        data = {"label": f"{name} · Working…" if name else "Working…", "indeterminate": True, "relay_model": name}
        return SimpleNamespace(content="", metadata={"_agent_ui": {"kind": "task_progress", "data": data}})

    asyncio.run(TuiRuntime._dispatch_progress(runtime, relay("Claude Fable 5.1"), event))
    assert runtime.status.relay_model == "Claude Fable 5.1"
    asyncio.run(TuiRuntime._dispatch_progress(runtime, relay(""), event))
    assert runtime.status.relay_model == ""


def _plan_window_text(screen) -> str:
    """Every step row of the plan's detail window, as plain text."""
    return "\n".join(str(row.content) for row in screen.query("#plan-detail-steps Static"))


async def _until(pilot, check) -> None:
    """Let the app work (threads, mounts) until ``check`` holds, at most 3 s."""
    for _ in range(60):
        if check():
            return
        await pilot.pause(0.05)


def test_the_chat_plan_shows_its_steps_and_the_working_line_names_the_current_one(tmp_path, monkeypatch) -> None:
    """A real board in the project, the app's own event path, no model."""
    from textual.worker import WorkerCancelled

    from navin.board import session_focus
    from navin.board.store import ProjectBoardStore
    from navin.tui.plan_detail import PlanDetailScreen
    from navin.tui.plan_panel import PlanPanel, load_cli_plan
    from navin.tui.runtime import UiToolEvent

    monkeypatch.setattr(session_focus, "_store_path", lambda: tmp_path / "session-focus.json")
    session_focus.clear()

    async def settle(app) -> None:
        for worker in [w for w in app.workers if w.group == "plan-panel"]:
            try:
                await worker.wait()
            except WorkerCancelled:
                continue  # superseded by the refresh after it

    async def run() -> None:
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        app = _InteractionApp(SimpleNamespace(workspace_path=tmp_path), prefs=prefs)
        store = ProjectBoardStore(tmp_path)
        # Wide enough for the step title: a narrow row keeps only "Step 1/2".
        async with app.run_test(size=(150, 30)) as pilot:
            panel = app.query_one("#plan", PlanPanel)
            app._refresh_plan()
            await settle(app)
            assert not panel.display, "no plan, no panel"
            await app.submit_text("Fix the rounding and add the tests")
            await app.runtime.bus.consume_inbound()
            key = app.runtime.session_key
            ids = [store.create_task(title=title, actor="agent", actor_type="agent")["id"]
                   for title in ("Fix the rounding", "Add the tests")]
            store.update_task(ids[0], actor="agent", actor_type="agent", fields={"status": "in_progress"})
            session_focus.remember(key, ids)
            await app._render_runtime_event(UiToolEvent("b1", "board", "end", {"action": "create"}, result="2 tasks created:"))
            await settle(app)
            await pilot.pause()
            text = str(panel.content)
            assert panel.display and "Plan  0/2 terminées" in text
            assert panel.folded and "\n" not in text
            # Clicking the collapsed title opens the detailed checklist.
            await pilot.click(panel)
            text = str(panel.content)
            assert "◉ Fix the rounding" in text and "○ Add the tests" in text
            app._refresh_working_line()
            assert "Étape 1/2: Fix the rounding · " in str(app.query_one(WorkingLine).content)
            store.update_task(ids[0], actor="agent", actor_type="agent", fields={"status": "done"})
            store.update_task(ids[1], actor="agent", actor_type="agent", fields={"status": "in_progress"})
            await app._render_runtime_event(UiToolEvent(
                "b2", "board", "end", {"action": "move", "task_id": ids[0], "status": "done"},
                result=f"Task moved: {ids[0]} [done] (medium) Fix the rounding @agent(agent)",
            ))
            await settle(app)
            await pilot.pause()
            text = str(panel.content)
            assert "1/2 terminées" in text and "✓ Fix the rounding" in text and "◉ Add the tests" in text
            app._refresh_working_line()
            line = str(app.query_one(WorkingLine).content)
            assert "Étape 2/2: Add the tests" in line and "\n" not in line
            # One click folds the plan to one line that still says where it is.
            await pilot.click(panel)
            folded = str(panel.content)
            assert "\n" not in folded and "en cours : Add the tests" in folded
            await pilot.click(panel)
            assert "○" not in str(panel.content) and "✓ Fix the rounding" in str(panel.content)
            # A step handed over for review moves the plan on and is no longer "en cours".
            store.update_task(ids[1], actor="agent", actor_type="agent", fields={"status": "review"})
            await app._render_runtime_event(UiToolEvent(
                "b3", "board", "end", {"action": "move", "task_id": ids[1], "status": "review"},
                result=f"Task moved: {ids[1]} [review] (medium) Add the tests @agent(agent)",
            ))
            await settle(app)
            await pilot.pause()
            text = str(panel.content)
            assert "1/2 terminées · 1 à valider" in text and "◐ Add the tests  à valider" in text, text
            assert panel.step_label() == ""
            app._refresh_working_line()
            assert "Étape" not in str(app.query_one(WorkingLine).content)
            # Past five steps the plan stays on one line and a click opens its window.
            more = [
                store.create_task(title=f"Step {n}", actor="agent", actor_type="agent",
                                  description=f"Everything about step {n}")["id"]
                for n in range(3, 7)
            ]
            store.update_task(more[0], actor="agent", actor_type="agent",
                              fields={"status": "blocked", "evidence": "Needs the Harbor robot account"})
            session_focus.remember(key, more)
            await app._render_runtime_event(UiToolEvent(
                "b4", "board", "end", {"action": "create"}, result="4 tasks created:",
            ))
            await settle(app)
            await pilot.pause()
            text = str(panel.content)
            assert "Plan  1/6 terminées · 1 bloquée · 1 à valider" in text and "\n" not in text, text
            await pilot.click(panel)
            await _until(pilot, lambda: isinstance(app.screen, PlanDetailScreen)
                         and "step 6" in _plan_window_text(app.screen))
            detail = app.screen
            assert isinstance(detail, PlanDetailScreen) and "\n" not in str(panel.content)
            heading = str(detail.query_one("#plan-detail-heading").content)
            assert "1/6 terminées · 1 bloquée · 1 à valider" in heading and "en cours : Step 4" in heading, heading
            steps = _plan_window_text(detail)
            for part in ("✓ 1. Fix the rounding  Terminée", "◐ 2. Add the tests  À valider",
                         "! 3. Step 3  Bloquée", "Motif du blocage : Needs the Harbor robot account",
                         "◉ 4. Step 4  À suivre", "Everything about step 6"):
                assert part in steps, steps
            # A filter keeps the plan's numbering.
            await pilot.click("#plan-detail-blocked")
            await _until(pilot, lambda: "Step 4" not in _plan_window_text(detail))
            steps = _plan_window_text(detail)
            assert "! 3. Step 3  Bloquée" in steps and "Fix the rounding" not in steps, steps
            await pilot.press("escape")
            await _until(pilot, lambda: not isinstance(app.screen, PlanDetailScreen))
            assert not isinstance(app.screen, PlanDetailScreen)
            # The plan is this chat's own.
            assert load_cli_plan(tmp_path, "cli:another-chat") is None

    try:
        asyncio.run(run())
    finally:
        session_focus.clear()


def test_plan_hide_and_show_keep_the_tasks_and_plan_lists_the_plans_apart(tmp_path, monkeypatch) -> None:
    """Hiding is a view choice of the chat; /plan lists the current and earlier plans apart."""
    from rich.cells import cell_len
    from textual.worker import WorkerCancelled

    from navin.board import session_focus
    from navin.board.store import ProjectBoardStore
    from navin.tui.plan_detail import PlanDetailScreen
    from navin.tui.plan_panel import PlanPanel
    from navin.tui.session_overview import SessionOverviewScreen

    monkeypatch.setattr(session_focus, "_store_path", lambda: tmp_path / "session-focus.json")
    session_focus.clear()

    async def settle(app, pilot) -> None:
        for worker in [w for w in app.workers if w.group == "plan-panel"]:
            try:
                await worker.wait()
            except WorkerCancelled:
                continue
        await pilot.pause()

    async def run() -> None:
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        app = _InteractionApp(SimpleNamespace(workspace_path=tmp_path), prefs=prefs)
        store = ProjectBoardStore(tmp_path)
        async with app.run_test(size=(150, 30)) as pilot:
            key = app.runtime.session_key
            old = store.create_task(title="Connect the API", actor="agent", actor_type="agent")["id"]
            new = store.create_task(
                title="Fix the login", actor="agent", actor_type="agent",
                acceptance="La connexion passe avec le SSO",
            )["id"]
            session_focus.remember(key, [old], owner="req-a")
            session_focus.start_plan(key, [new], owner="req-b")
            panel = app.query_one("#plan", PlanPanel)
            app._refresh_plan()
            await settle(app, pilot)
            assert panel.display and "Connect the API" not in str(panel.content)

            assert await app._run_tui_slash("/plan hide")
            await settle(app, pilot)
            assert not panel.display and session_focus.plan_hidden(key)
            assert panel.step_label() == ""
            # Only the view: the task keeps its status, nothing is cancelled.
            assert store.get_task(new)["status"] == "backlog"
            assert await app._run_tui_slash("/plan show")
            await settle(app, pilot)
            assert panel.display and not session_focus.plan_hidden(key)
            # The panel's own "✕ masquer" hides it in one click.
            first = str(panel.content).split("\n")[0]
            await pilot.click("#plan", offset=(1 + cell_len(first[: first.index("✕")]), 0))
            for _ in range(40):
                if session_focus.plan_hidden(key):
                    break
                await pilot.pause(0.05)
            await settle(app, pilot)
            assert session_focus.plan_hidden(key) and not panel.display
            assert store.get_task(new)["status"] == "backlog"
            assert await app._run_tui_slash("/plan show")
            await settle(app, pilot)
            assert panel.display

            assert await app._run_tui_slash("/plan")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SessionOverviewScreen)
            text = str(screen.query_one("#overview-text").content)
            order = [text.index(part) for part in ("Plan en cours", "Fix the login", "Plan précédent 1", "Connect the API")]
            assert order == sorted(order), text
            await pilot.click("#overview-visibility")
            for _ in range(40):
                if session_focus.plan_hidden(key):
                    break
                await pilot.pause(0.05)
            assert session_focus.plan_hidden(key)
            assert "Plan en cours (masqué dans le chat)" in str(screen.query_one("#overview-text").content)

            # Session menu > Plan en cours: the current plan alone in its window,
            # with every detail, read even while it is hidden from the chat.
            await pilot.press("escape")
            await pilot.pause()
            await app._show_current_plan(str(tmp_path), key)
            current = app.screen
            assert isinstance(current, PlanDetailScreen)
            await _until(pilot, lambda: "Fix the login" in _plan_window_text(current))
            heading = str(current.query_one("#plan-detail-heading").content)
            assert "Plan en cours  ·  masqué dans le chat" in heading, heading
            detail = _plan_window_text(current)
            assert "Critères : La connexion passe avec le SSO" in detail, detail
            assert "Connect the API" not in detail
            # Shown again from the window: only the view changes.
            await pilot.click("#plan-detail-visibility")
            await _until(pilot, lambda: not session_focus.plan_hidden(key))
            assert not session_focus.plan_hidden(key)
            assert "masqué" not in str(current.query_one("#plan-detail-heading").content)
            assert store.get_task(new)["status"] == "backlog"

    try:
        asyncio.run(run())
    finally:
        session_focus.clear()


def test_a_step_taken_up_again_is_numbered_by_progress_not_by_its_place() -> None:
    # Seen live: "Plan 21/40 terminées", then "Étape 1/40" on the working
    # line, because the step taken up again after a stop was the first one
    # the chat had touched. The user read it as a restart from scratch.
    from navin.tui.plan_panel import PlanPanel

    panel = PlanPanel()
    items = [{"id": "t-1", "title": "CO P2 : backend", "done": False}]
    items += [{"id": f"t-{n}", "title": f"Step {n}", "done": n <= 22} for n in range(2, 41)]
    panel.plan = {"items": items, "current_id": "t-1", "done_count": 21, "total_count": 40, "complete": False}
    assert panel.step_label() == "Étape 22/40: CO P2 : backend"
    # Never past the last step, even while the last one is still open.
    panel.plan = {**panel.plan, "done_count": 40}
    assert panel.step_label() == "Étape 40/40: CO P2 : backend"
    # The steps handed over for review are behind the run too.
    panel.plan = {**panel.plan, "done_count": 0, "review_count": 24}
    items[0]["status"] = "in_progress"
    assert panel.step_label() == "Étape 25/40: CO P2 : backend"
    # A step handed over, or a blocked one, is not named as the step in progress.
    for state in ({"status": "review"}, {"status": "blocked", "blocked": True}):
        items[0].update(state)
        assert panel.step_label() == "", state


def test_the_working_line_stays_on_one_row_with_a_long_step() -> None:
    from navin.tui.widgets import format_working_line

    line = format_working_line(
        elapsed_s=154, activity="Running pytest test_pricing.py",
        step="Step 1/3: Corriger l'ordre remise/arrondi", width=81,
    )
    assert line == "Step 1/3 · Running pytest test_pricing.py (2m 34s • esc to interrupt)"
    roomy = format_working_line(elapsed_s=5, activity="Working…", step="Step 2/3: Add the tests", width=120)
    assert roomy.startswith("Step 2/3: Add the tests · Working… (5s")


def test_session_history_and_plans_show_dates_states_and_unfinished_work(tmp_path) -> None:
    from unittest.mock import AsyncMock

    from navin.board.store import ProjectBoardStore
    from navin.session.manager import SessionManager

    sessions = SessionManager(tmp_path / "sessions")
    session = sessions.get_or_create("cli:direct")
    session.messages = [
        {"role": "user", "content": "Build the charts", "timestamp": "2026-11-07T10:15:00"},
        {"role": "assistant", "content": "Charts are built."},
        {"role": "user", "content": "Add the tests", "timestamp": "2026-11-07T11:30:00"},
    ]
    sessions.save(session)
    board = ProjectBoardStore(tmp_path)
    board.create_task(title="Charts", actor="test", actor_type="agent", status="done")
    board.create_task(title="Tests", actor="test", actor_type="agent")
    shown: list[str] = []
    host = SimpleNamespace(push_screen=AsyncMock())
    from navin.board import session_focus

    session_focus.remember("cli:direct", [task["id"] for task in board.read_tasks()])
    try:
        with patch("navin.tui.session_overview.SessionOverviewScreen", side_effect=lambda mode, **kwargs: shown.append((mode, kwargs)) or mode):
            asyncio.run(NavinApp._show_session_history(host, str(tmp_path), "cli:direct", sessions))
            asyncio.run(NavinApp._show_session_plans(host, str(tmp_path), "cli:direct"))
        history, plans = shown
        assert history[0] == "history" and plans[0] == "plan"
        assert history[1]["prompts"][0]["date"].startswith("07/11/2026")
        assert [row["text"] for row in history[1]["prompts"]] == ["Build the charts", "Add the tests"]
        assert [row["answered"] for row in history[1]["prompts"]] == ["1", ""]
        assert [row["title"] for row in plans[1]["tasks"]] == ["Charts", "Tests"]
    finally:
        session_focus.forget("cli:direct")
    assert "\u2014" not in history + plans
