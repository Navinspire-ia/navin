# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""UX fixes reported on the CLI: prompt focus, copy of long selections,
terminal file preview, and the tool plumbing behind it."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from navin.agent.tools.context import request_context
from navin.agent.tools.open_file_preview import OpenFilePreviewTool
from navin.bus.outbound_events import (
    FilePreviewOpenRequestedEvent,
    outbound_message_for_event,
)
from navin.bus.queue import MessageBus
from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.runtime import TuiRuntime, UiFilePreview
from navin.tui.widgets import Composer, ComposerShell, Sidebar


class InteractionHost(NavinApp):
    async def on_mount(self, event):
        event.prevent_default()
        self.runtime.bus = MessageBus()
        self._engine_ready = True
        self.query_one(Sidebar).display = False

    async def on_unmount(self, event):
        event.prevent_default()
        self.runtime._closed = True

    def _refresh_side(self):
        self._set_status()

    def _load_account(self, *args, **kwargs):
        pass


def _make_app(directory: Path) -> NavinApp:
    prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
    prefs.save = lambda: None
    return InteractionHost(SimpleNamespace(workspace_path=directory), prefs=prefs)


class PromptFocusTests(unittest.IsolatedAsyncioTestCase):
    async def test_clicking_the_shell_focuses_the_composer(self):
        with tempfile.TemporaryDirectory() as name:
            app = _make_app(Path(name))
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                shell = app.query_one(ComposerShell)
                composer = app.query_one(Composer)
                app.set_focus(None)
                await pilot.pause()

                class FakeClick:
                    widget = shell

                shell.on_click(FakeClick())
                await pilot.pause()
                self.assertIs(app.focused, composer)

    async def test_clicks_on_the_meta_row_do_not_steal_focus(self):
        with tempfile.TemporaryDirectory() as name:
            app = _make_app(Path(name))
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                shell = app.query_one(ComposerShell)
                meta = app.query_one("ComposerMeta")
                app.set_focus(None)
                await pilot.pause()

                class FakeClick:
                    widget = meta

                shell.on_click(FakeClick())
                await pilot.pause()
                self.assertIsNone(app.focused)


class CopySelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_selection_reaches_the_host_clipboard(self):
        with tempfile.TemporaryDirectory() as name:
            app = _make_app(Path(name))
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                long_text = "x" * 9000
                captured: dict = {}
                with patch.object(
                    NavinApp, "copy_to_clipboard", autospec=True,
                    side_effect=lambda self, text, **kw: captured.update(text=text, kw=kw),
                ):
                    with patch.object(
                        NavinApp, "_selected_text", return_value=long_text,
                    ):
                        from textual import events

                        app._copy_on_select(events.TextSelected())
                self.assertEqual(captured.get("text"), long_text)
                # Long selections reach the host clipboard too.
                self.assertTrue(captured.get("kw", {}).get("to_os", True))


class TerminalPreviewToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.report = self.root / "report.md"
        self.report.write_text("# Hello\n\nbody")

    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_tool_emits_preview_for_the_cli_channel(self):
        bus = MessageBus()
        tool = OpenFilePreviewTool(bus=bus, working_dir=str(self.root))
        ctx = SimpleNamespace(channel="cli", chat_id="direct", workspace=str(self.root))

        async def run():
            with request_context(ctx):
                return await tool.execute(path="report.md")

        result = self._run(run())
        self.assertIn("terminal preview", str(result))
        self.assertFalse(bus.outbound.empty())
        msg = bus.outbound.get_nowait()
        self.assertEqual(msg.channel, "cli")

    def test_tool_still_rejects_unknown_channels(self):
        bus = MessageBus()
        tool = OpenFilePreviewTool(bus=bus, working_dir=str(self.root))
        ctx = SimpleNamespace(channel="telegram", chat_id="1", workspace=str(self.root))

        async def run():
            with request_context(ctx):
                return await tool.execute(path="report.md")

        result = self._run(run())
        self.assertIn("Error:", str(result))
        self.assertTrue(bus.outbound.empty())


class SessionTitlesLatencyTests(unittest.IsolatedAsyncioTestCase):
    """ensure_session_titles must stay cheap for the sessions picker."""

    def _runtime(self, rows):
        from navin.tui.runtime import TuiRuntime

        peeks: list[str] = []
        saved: list[str] = []

        class Sessions:
            def list_sessions(self):
                return rows

            def peek(self, key):
                peeks.append(key)
                return SimpleNamespace(
                    messages=[SimpleNamespace(role="user", content="hello world")],
                    metadata={},
                )

            def save(self, session):
                saved.append(session)

        class Loop:
            sessions = Sessions()

        runtime = TuiRuntime.__new__(TuiRuntime)
        runtime.agent_loop = Loop()
        return runtime, peeks, saved

    async def test_untitled_rows_are_capped_and_previewed_rows_skipped(self):
        from navin.tui.runtime import MAX_SESSION_TITLE_FIXUPS

        # Rows with a preview already display something: no full load.
        rows = [
            {"key": f"cli:s{i}", "preview": f"msg {i}"}
            for i in range(MAX_SESSION_TITLE_FIXUPS + 5)
        ]
        # Untitled and without preview: fixable, but capped.
        for i in range(MAX_SESSION_TITLE_FIXUPS + 3):
            rows.append({"key": f"cli:u{i}", "preview": ""})
        runtime, peeks, _ = self._runtime(rows)
        runtime.ensure_session_titles()
        self.assertEqual(len(peeks), MAX_SESSION_TITLE_FIXUPS)
        self.assertNotIn("cli:s0", peeks)

    async def test_untitled_rows_without_key_are_skipped(self):
        rows = [{"key": "", "preview": ""}, {"key": "cli:ok", "preview": ""}]
        runtime, peeks, _ = self._runtime(rows)
        runtime.ensure_session_titles()
        self.assertEqual(peeks, ["cli:ok"])

    async def test_titled_sessions_are_left_alone(self):
        rows = [{"key": "cli:a", "title": "Already named", "preview": "x"}]
        runtime, peeks, _ = self._runtime(rows)
        runtime.ensure_session_titles()
        self.assertEqual(peeks, [])


class UiFilePreviewDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_maps_the_bus_event_to_a_ui_event(self):
        emitted: list = []

        async def fake_emit(event):
            emitted.append(event)

        runtime = TuiRuntime.__new__(TuiRuntime)
        runtime._emit = fake_emit  # type: ignore[method-assign]
        runtime.agent_loop = SimpleNamespace(_active_tasks={}, channels_config=None)
        runtime.status = SimpleNamespace(turn_active=False, session_key="cli:direct")

        msg = outbound_message_for_event(
            channel="cli", chat_id="direct",
            event=FilePreviewOpenRequestedEvent(path="/tmp/x.md"),
        )
        await runtime._dispatch(msg)
        self.assertTrue(emitted)
        self.assertIsInstance(emitted[-1], UiFilePreview)
        self.assertEqual(emitted[-1].path, "/tmp/x.md")


if __name__ == "__main__":
    unittest.main()
