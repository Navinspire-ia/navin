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
from navin.tui import file_preview
from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.runtime import TuiRuntime, UiFilePreview
from navin.tui.screens import MarkdownScreen
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
        runtime.channel, runtime.chat_id = "cli", "direct"
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


class SessionPickerScopeTests(unittest.TestCase):
    """The CLI Sessions picker lists the conversations of the project open here.

    Opening financeIa-v2 listed memory consolidation runs ("You are a memory
    consolidation engine..."), the heartbeat, and desktop chats saved in that
    folder but opened in another project (a subfolder, /home/aymen/bbac).
    """

    PROJECT = "/home/u/financeIa-v2"

    def _rows(self, rows, current="cli:direct"):
        class Sessions:
            workspace = Path(self.PROJECT)

            def list_sessions(self):
                return rows

        runtime = TuiRuntime.__new__(TuiRuntime)
        runtime.agent_loop = SimpleNamespace(sessions=Sessions())
        runtime.channel, _, runtime.chat_id = current.partition(":")
        return [row["key"] for row in runtime.session_rows()]

    def test_engine_sessions_are_not_chats(self):
        rows = [
            {"key": "cli:1", "title": "facture filtre"},
            {"key": "dream:20261028-204804", "title": "You are a memory consolidation engine"},
            {"key": "heartbeat", "title": "[Your response will be delivered"},
            {"key": "cron:job-1", "title": "daily report"},
            {"key": "system:boot", "title": "x"},
            {"key": "sdk:abc", "title": "sdk chat"},
        ]
        self.assertEqual(self._rows(rows), ["cli:1", "sdk:abc"])

    def test_desktop_chats_of_other_projects_are_left_out(self):
        rows = [
            {"key": "websocket:mine", "title": "ici", "workspace_scope": {"project_path": self.PROJECT}},
            {"key": "websocket:slash", "title": "ici", "workspace_scope": {"project_path": self.PROJECT + "/"}},
            {"key": "websocket:sub", "title": "test", "workspace_scope": {"project_path": self.PROJECT + "/test"}},
            {"key": "websocket:other", "title": "Audit", "workspace_scope": {"project_path": "/home/u/bbac"}},
            {"key": "websocket:unscoped", "title": "old", "workspace_scope": None},
            {"key": "cli:2", "title": "cli chat"},
        ]
        self.assertEqual(self._rows(rows), ["websocket:mine", "websocket:slash", "websocket:unscoped", "cli:2"])

    def test_the_open_session_always_stays(self):
        rows = [{"key": "websocket:other", "title": "x", "workspace_scope": {"project_path": "/elsewhere"}}]
        self.assertEqual(self._rows(rows, current="websocket:other"), ["websocket:other"])


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


class TerminalPreviewScreenTests(unittest.IsolatedAsyncioTestCase):
    """An HTML report previewed from the CLI opens in the browser.

    Its source filled a terminal box: rows of <tr data-family=...> cut at the
    edge, then "... (truncated)".
    """

    REPORT = (
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<title>Agent catalog audit</title><style>td{color:red}</style>"
        "<script>var row = '<tr>';</script></head><body>"
        "<h1>Audit du catalogue</h1><p>Statut des agents &amp; familles.</p>"
        "<button>Exporter</button>"
        "<table><thead><tr><th>Agent</th><th>Statut</th></tr></thead><tbody>"
        "<tr data-family='banking' data-status='planned'><td class='agent'>"
        "<strong>Navin Enterprise KYC / AML &amp; Sanctions</strong></td><td>Prévu</td></tr>"
        "<tr><td>Agent Contrats | Polices</td><td>Publié annoncé</td></tr>"
        "</tbody></table><ul><li>Banking</li><li></li><li>Insurance</li></ul>"
        "<p><a href='https://navin.live/docs'>Documentation</a></p></body></html>"
    )

    def test_an_html_report_reads_as_markdown_not_tags(self):
        markdown = file_preview.html_to_markdown(self.REPORT)
        for leftover in ("<tr", "data-family", "color:red", "var row", "Exporter"):
            self.assertNotIn(leftover, markdown)
        self.assertTrue(markdown.startswith("# Audit du catalogue\n\nStatut des agents & familles."))
        self.assertIn(
            "| Agent | Statut |\n| --- | --- |\n"
            "| Navin Enterprise KYC / AML & Sanctions | Prévu |\n"
            "| Agent Contrats \\| Polices | Publié annoncé |",
            markdown,
        )
        self.assertIn("- Banking\n- Insurance", markdown)
        self.assertIn("[Documentation](https://navin.live/docs)", markdown)

    async def _preview(self, path: Path, *, opened: bool):
        app = _make_app(path.parent)
        notes: list[str] = []
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            with (
                patch("navin.utils.open_browser.open_local_file", return_value=opened) as opener,
                patch(
                    "navin.utils.open_browser.file_unavailable_reason",
                    return_value="No browser or file viewer could be opened from here.",
                ),
                patch.object(app, "notify", side_effect=lambda message, **_: notes.append(str(message))),
            ):
                app._open_file_preview(str(path))
                for _ in range(3):
                    await app.workers.wait_for_complete()
                    await pilot.pause()
            screen = app.screen
            shown = screen._markdown if isinstance(screen, MarkdownScreen) else None
        return opener, notes, shown

    async def test_the_report_opens_in_the_browser_not_in_a_terminal_box(self):
        with tempfile.TemporaryDirectory() as name:
            report = Path(name) / "report.html"
            report.write_text(self.REPORT, encoding="utf-8")
            opener, notes, shown = await self._preview(report, opened=True)
        opener.assert_called_once_with(str(report))
        self.assertIsNone(shown)
        self.assertIn("report.html opened in your browser.", notes)

    async def test_without_a_browser_the_report_is_readable_text(self):
        with tempfile.TemporaryDirectory() as name:
            report = Path(name) / "report.html"
            report.write_text(self.REPORT, encoding="utf-8")
            _, notes, shown = await self._preview(report, opened=False)
        self.assertFalse([note for note in notes if "report.html" in note])
        self.assertIsNotNone(shown)
        self.assertTrue(shown.startswith(
            "**report.html** - shown here as text. No browser or file viewer could be opened from here."
        ))
        self.assertIn("| Navin Enterprise KYC / AML & Sanctions | Prévu |", shown)
        self.assertNotIn("<tr", shown)

    async def test_missing_report_is_an_error_not_a_browser_failure(self):
        with tempfile.TemporaryDirectory() as name:
            report = Path(name) / "missing.html"
            opener, notes, shown = await self._preview(report, opened=True)
        opener.assert_not_called()
        self.assertIsNone(shown)
        self.assertTrue(any("file not found" in note for note in notes))

    async def test_a_markdown_file_stays_in_the_terminal_viewer(self):
        with tempfile.TemporaryDirectory() as name:
            notes_file = Path(name) / "notes.md"
            notes_file.write_text("# Plan\n\n- one", encoding="utf-8")
            opener, _, shown = await self._preview(notes_file, opened=True)
        opener.assert_not_called()
        self.assertEqual(shown, "# Plan\n\n- one")

    def test_code_is_fenced_for_its_language_even_with_backticks_inside(self):
        shown = file_preview.terminal_markdown(Path("/w/run.py"), b"print('```')\n")
        self.assertEqual(shown, "**/w/run.py**\n\n````python\nprint('```')\n\n````")
        self.assertIsNone(file_preview.terminal_markdown(Path("/w/a.bin"), b"\xff\xfe\x00"))


if __name__ == "__main__":
    unittest.main()
