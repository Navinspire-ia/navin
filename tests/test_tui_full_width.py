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
from textual import events
from textual.widgets import Static

from navin.bus.queue import MessageBus
from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.widgets import EdgeScrollbar, Sidebar, ToolCall


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
            # The sessions open at start; once hidden, the chat alone fills the window.
            await pilot.press("ctrl+s")
            await pilot.pause()
            block = await app._ensure_assistant()
            await block.tool_event("run", "exec", "end", {"command": "check"}, "result " * 90, None, None)
            app.composer.set_text("Conserver mon brouillon")
            for width in (100, 220, 72):
                await pilot.resize_terminal(width, 35)
                await pilot.pause()
                # One column goes to the thin scrollbar on the screen edge.
                assert app.transcript.region.width == width - 1
                assert app.composer.region.right >= width - 4
                body = block.query_one(ToolCall).query_one(".tool-body", Static)
                assert body.region.right >= width - 4
                assert body.region.height == 1
                assert app.composer.text == "Conserver mon brouillon"
            await pilot.resize_terminal(160, 35)
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert app.query_one(Sidebar).display
            assert app.transcript.region.width == 123
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert not app.query_one(Sidebar).display
            assert app.transcript.region.width == 159
    asyncio.run(run())


def test_thin_scrollbar_sits_on_the_screen_edge_and_drives_the_chat(tmp_path):
    async def run():
        prefs = TuiPrefs(mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        app = LayoutHost(SimpleNamespace(workspace_path=tmp_path), prefs=prefs)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            bar = app.query_one(EdgeScrollbar)
            transcript = app.transcript
            assert (bar.region.width, bar.region.right) == (1, 120)
            assert bar.shape[1] == -1
            assert bar.render_line(0).text.strip() == ""

            await transcript.add(Static("\n".join(f"row {index}" for index in range(300))))
            await pilot.pause()
            track, start, size = bar.shape
            assert track == transcript.outer_size.height
            assert 1 <= size < track and start + size == track
            assert bar.render_line(track - 1).text == EdgeScrollbar.THUMB_GLYPH
            assert bar.render_line(0).text == EdgeScrollbar.TRACK_GLYPH
            # Next to the composer the column stays blank.
            assert bar.render_line(track).text.strip() == ""
            thumb = list(bar.render_line(track - 1))[0].style
            rail = list(bar.render_line(0))[0].style
            assert thumb.color != rail.color

            # The sidebar opens to the bar's left: the bar keeps the screen edge.
            await pilot.press("ctrl+b")
            await pilot.pause()
            sidebar = app.query_one(Sidebar)
            assert sidebar.display
            assert bar.region.x == sidebar.region.right
            assert bar.region.right == 120
            assert transcript.region.right == sidebar.region.x

            # A click at the top of the track jumps there and stops following.
            await pilot.click(EdgeScrollbar, offset=(0, 0))
            await pilot.pause()
            assert transcript.scroll_y == 0
            assert not transcript.auto_follow
            assert bar.shape[1] == 0

            # Dragging the thumb to the bottom returns to the tail and follows.
            await pilot.mouse_down(EdgeScrollbar, offset=(0, 0))
            assert bar.has_class("-dragging")
            await pilot.hover(EdgeScrollbar, offset=(0, track - 1))
            await pilot.mouse_up(EdgeScrollbar, offset=(0, track - 1))
            await pilot.pause()
            assert not bar.has_class("-dragging")
            assert transcript.is_vertical_scroll_end
            assert transcript.auto_follow
            assert bar.shape[1] + bar.shape[2] == track

            # The wheel over the bar scrolls the conversation.
            before = transcript.scroll_y
            bar.post_message(events.MouseScrollUp(bar, 0, 1, 0, -1, 0, False, False, False))
            await pilot.pause()
            assert transcript.scroll_y == before - EdgeScrollbar.WHEEL_LINES
            assert bar.shape[1] + bar.shape[2] < track
    asyncio.run(run())
