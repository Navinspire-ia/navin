# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Use the terminal's real columns and retain the draft through resizing."""

import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from textual.widgets import Static

from navin.bus.queue import MessageBus
from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.widgets import Sidebar, ToolCall


class LayoutHost(NavinApp):
    async def on_mount(self, event):
        event.prevent_default()
        self.runtime.bus = MessageBus()
        self._engine_ready = True
        self.query_one(Sidebar).set_class(self.prefs.sidebar, "-visible")
        self.composer.focus()

    async def on_unmount(self, event):
        event.prevent_default()
        await self._flush_unsent_work()
        self.runtime._closed = True

    def _refresh_side(self):
        self._set_status()


def test_old_default_panel_migrates_but_explicit_user_choice_is_respected(tmp_path):
    path = tmp_path / "tui.json"
    with patch.object(TuiPrefs, "path", return_value=path):
        assert not TuiPrefs.load().sidebar
        path.write_text(json.dumps({"sidebar": True}))
        assert not TuiPrefs.load().sidebar
        path.write_text(json.dumps({"sidebar": True, "sidebar_explicit": True}))
        assert TuiPrefs.load().sidebar


@pytest.mark.skipif(sys.platform == "win32", reason="Unix PTY resize regression")
def test_stale_shell_columns_cannot_limit_the_tui_and_resize_reads_live_geometry(monkeypatch):
    import fcntl
    import struct
    import termios

    from navin.tui.driver import NavinLinuxDriver

    master, slave = os.openpty()
    try:
        monkeypatch.setenv("COLUMNS", "80")
        monkeypatch.setenv("LINES", "24")
        driver = NavinLinuxDriver.__new__(NavinLinuxDriver)
        driver._file = SimpleNamespace(fileno=lambda: slave)
        driver.fileno = slave
        for columns, lines in [(220, 48), (100, 30), (250, 60)]:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", lines, columns, 0, 0))
            assert driver._get_terminal_size() == (columns, lines)
    finally:
        os.close(slave)
        os.close(master)


def test_transcript_and_composer_fill_the_window_and_panel_toggle_releases_width(tmp_path):
    async def run():
        prefs = TuiPrefs(mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        app = LayoutHost(SimpleNamespace(workspace_path=tmp_path), prefs=prefs)
        async with app.run_test(size=(100, 35)) as pilot:
            block = await app._ensure_assistant()
            await block.tool_event("run", "exec", "end", {"command": "check"}, "result " * 90, None, None)
            app.composer.set_text("Conserver mon brouillon")
            for width in (100, 220, 72):
                await pilot.resize_terminal(width, 35)
                await pilot.pause()
                assert app.transcript.region.width == width
                assert app.composer.region.right >= width - 4
                body = block.query_one(ToolCall).query_one(".tool-body", Static)
                assert body.region.right >= width - 4
                assert body.region.height == 1
                assert app.composer.text == "Conserver mon brouillon"
            await pilot.resize_terminal(160, 35)
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert app.query_one(Sidebar).display
            assert app.transcript.region.width == 124
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert not app.query_one(Sidebar).display
            assert app.transcript.region.width == 160
    asyncio.run(run())
