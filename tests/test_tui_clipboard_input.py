# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Clipboard backends and error output must leave the live prompt usable."""

import asyncio
import threading
from unittest.mock import patch

import pytest
from textual import events
from textual.widgets import Button, Static

from navin.tui.clipboard import _write_windows_clipboard
from navin.tui.runtime import UiToolEvent
from navin.tui.widgets import ToolCall
from tests.test_tui_queue import make_app


@pytest.mark.parametrize("payload", ["copied text", "long line\n" * 1000], ids=["short", "large"])
def test_paste_into_empty_prompt_can_be_undone_and_restored(tmp_path, payload):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            app.composer.post_message(events.Paste(payload))
            await pilot.pause()
            assert app.composer.expand_for_submit() == payload
            await pilot.press("ctrl+z")
            assert app.composer.text == ""
            await pilot.press("ctrl+y")
            assert app.composer.expand_for_submit() == payload
            assert app.runtime.bus.inbound_size == 0
    asyncio.run(run())


def test_slow_copy_keeps_single_click_typing_live_and_coalesces_writes(tmp_path):
    async def run():
        app = make_app(tmp_path)
        started, release = threading.Event(), threading.Event()
        writes = []

        def slow_write(text):
            writes.append(text)
            if len(writes) == 1:
                started.set()
                release.wait(5)
            return True

        async with app.run_test(size=(100, 30)) as pilot:
            await app.submit_text("keep working")
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiToolEvent("job", "exec", "start", {"command": "pytest"}))
            app.composer.set_text("abcdefghi")
            app.transcript.focus()
            with patch("navin.tui.clipboard.write_os_clipboard", side_effect=slow_write):
                try:
                    app.copy_to_clipboard("first", quiet=True)
                    assert await asyncio.to_thread(started.wait, 1)
                    for index in range(20):
                        app.copy_to_clipboard(f"selection {index}", quiet=True)
                        await app._on_runtime_event(UiToolEvent("job", "exec", "output", output=f"line {index}\n"))
                    await pilot.click(app.composer, offset=(3, 0))
                    await pilot.press("x")
                    assert app.composer.has_focus
                    assert app.composer.text == "abcxdefghi"
                    assert app.runtime.turn_active
                    assert not release.is_set()
                    assert writes == ["first"]
                    # A just-copied selection is usable while the host is busy.
                    with patch("navin.tui.clipboard.read_clipboard") as read:
                        await pilot.press("ctrl+v")
                        await pilot.pause()
                        assert "selection 19" in app.composer.text
                        read.assert_not_called()
                finally:
                    release.set()
                await app.workers.wait_for_complete()
            assert writes == ["first", "selection 19"]
            assert app.clipboard == "selection 19"
    asyncio.run(run())


@pytest.mark.parametrize("interaction", ["type", "move"])
def test_delayed_paste_cannot_overwrite_a_changed_prompt(tmp_path, interaction):
    async def run():
        app = make_app(tmp_path)
        started, release = threading.Event(), threading.Event()

        def slow_read():
            started.set()
            release.wait(5)
            return "late paste"

        async with app.run_test(size=(80, 24)) as pilot:
            app.composer.set_text("draft")
            with patch("navin.tui.clipboard.read_clipboard", side_effect=slow_read):
                try:
                    await pilot.press("ctrl+v")
                    assert await asyncio.to_thread(started.wait, 1)
                    await pilot.press("x" if interaction == "type" else "left")
                    snapshot = app.composer.text, app.composer.selection
                finally:
                    release.set()
                await app.workers.wait_for_complete()
            assert (app.composer.text, app.composer.selection) == snapshot
            assert "late paste" not in app.composer.text
    asyncio.run(run())


@pytest.mark.parametrize("width", [48, 110])
def test_long_edit_error_is_compact_and_details_stay_accessible(tmp_path, width):
    async def run():
        app = make_app(tmp_path)
        path = "integrations/magento2/app/code/Guidia/Widget/etc/adminhtml/system.xml"
        error = (f"Error: old_text not found in {path}.\n"
                 "Best match (80% similar) at line 10:\n"
                 "--- old_text (provided)\n+++ actual\n@@ -1,5 +1,5 @@\n"
                 + "\n".join(f'+<section id="widget-{i}" />' for i in range(40)))
        async with app.run_test(size=(width, 30)) as pilot:
            await app.submit_text("update the configuration")
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiToolEvent("edit", "edit_file", "start", {"path": path}))
            await app._on_runtime_event(UiToolEvent("edit", "edit_file", "error", error=error))
            app.composer.set_text("unsent draft")
            await pilot.pause()
            row = app.query_one(ToolCall)
            body = row.query_one(".tool-body", Static)
            assert body.region.height <= 3
            assert "old_text not found" in str(body.content)
            assert "widget-39" not in str(body.content)
            assert "widget-39" in row.copy_text()
            more = row.query_one(".tool-more", Button)
            assert more.display and more.label.plain == "Show error details"
            await pilot.click(more)
            assert "widget-39" in str(body.content)
            assert app.composer.text == "unsent draft"
    asyncio.run(run())


def test_missing_windows_clipboard_codec_is_recoverable():
    class MissingCodec(str):
        def encode(self, encoding="utf-8", *args, **kwargs):
            if encoding == "utf-16-le":
                raise LookupError("unknown encoding: utf-16-le")
            return super().encode(encoding, *args, **kwargs)

    with patch("navin.tui.clipboard.shutil.which", side_effect=lambda name: "clip.exe" if name == "clip.exe" else None):
        assert not _write_windows_clipboard(MissingCodec("copied text"))
