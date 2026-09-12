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
            stop = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 1)
            self.assertEqual(stop.content, "/stop")
            await app.runtime._dispatch(OutboundMessage("cli", "direct", "Stopped."))
            await pilot.pause()
            self.assertFalse(app.query_one(WorkingLine).display)
            self.assertIn(app.runtime.session_key, app._queue_paused)
            await pilot.click(app.query_one(QueuedPromptRow).query_one("Button"))
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

        app = self.app
        attempts = 0

        async def engine():
            nonlocal attempts
            attempts += 1
            message = await app.runtime.bus.consume_inbound()
            if attempts == 1:
                raise RuntimeError("lost connection")
            await app.runtime.bus.publish_outbound(OutboundMessage("cli", "direct", f"Received: {message.content}"))
            await asyncio.Event().wait()

        async with app.run_test(size=(72, 25)) as pilot:
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
            await pilot.pause()
            await app.submit_text("first")
            await pilot.pause()
            self.assertFalse(app.runtime.turn_active)
            self.assertTrue(any("lost connection" in str(note.content) for note in app.query(SystemNote)))
            await app.submit_text("second")
            await pilot.pause()
            self.assertEqual(attempts, 2)
            self.assertEqual(app._current.text, "Received: second")
            self.assertFalse(app.query_one(WorkingLine).display)


if __name__ == "__main__":
    unittest.main()
