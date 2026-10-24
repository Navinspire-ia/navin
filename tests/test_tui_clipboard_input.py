# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Clipboard backends and error output must leave the live prompt usable."""

import asyncio
import threading
from unittest.mock import patch

import pytest
from textual import events

from navin.tui.clipboard import _write_windows_clipboard
from navin.tui.copy_chip import CopyChip
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
def test_failed_edit_shows_a_cross_without_the_error_dump_and_keeps_its_details(tmp_path, width):
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
            # A red cross and a short reason; never "Failed" or the raw dump.
            assert row.display
            head = row._head_text()
            assert head.startswith("× ") and "Failed" not in head
            assert "widget-39" not in app.export_screenshot()
            assert "widget-39" in row.copy_text()
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


def test_clipboard_helpers_never_inherit_the_terminal_stdin(monkeypatch):
    """A helper reading the TUI's stdin steals keystrokes and can freeze the terminal."""
    import subprocess

    from navin.tui import clipboard, images

    calls: list[dict] = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args[0], 1, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    images._run(["true"])
    monkeypatch.setattr(clipboard, "_read_commands", lambda: [["true"]])
    monkeypatch.setattr(clipboard, "_read_windows_clipboard", lambda: "")
    clipboard.read_clipboard()
    assert calls and all(
        kwargs.get("stdin") is subprocess.DEVNULL or "input" in kwargs for kwargs in calls
    )


def test_empty_terminal_paste_attaches_the_clipboard_image(tmp_path):
    """Some terminals turn Ctrl+V on an image into an empty paste."""
    from unittest.mock import patch

    image = tmp_path / "paste.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    async def run():
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            with patch("navin.tui.clipboard.read_clipboard", return_value=""), \
                    patch("navin.tui.images.save_clipboard_image", return_value=image):
                app.composer.post_message(events.Paste(""))
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
            assert f"[image: {image}]" in app.composer.text

    asyncio.run(run())


def test_empty_paste_without_image_leaves_the_prompt_untouched(tmp_path):
    from unittest.mock import patch

    async def run():
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            with patch("navin.tui.clipboard.read_clipboard", return_value=""), \
                    patch("navin.tui.images.save_clipboard_image", return_value=None):
                app.composer.post_message(events.Paste(""))
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
            assert app.composer.text == ""

    asyncio.run(run())


def test_alt_v_attaches_the_clipboard_image_before_its_text(tmp_path):
    """Windows Terminal keeps Ctrl+V and sends nothing for an image: Alt+V must work."""
    from unittest.mock import patch

    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    async def run():
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            with patch("navin.tui.clipboard.read_clipboard", return_value="alt text of the image"), \
                    patch("navin.tui.images.save_clipboard_image", return_value=image):
                await pilot.press("alt+v")
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
            assert app.composer.text == f"[image: {image}] "

    asyncio.run(run())


def test_alt_v_without_image_pastes_the_clipboard_text(tmp_path):
    from unittest.mock import patch

    async def run():
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            with patch("navin.tui.clipboard.read_clipboard", return_value="plain words"), \
                    patch("navin.tui.images.save_clipboard_image", return_value=None) as save:
                await pilot.press("alt+v")
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
            assert app.composer.text == "plain words"
            assert save.call_count == 1

    asyncio.run(run())


def test_empty_paste_outside_the_prompt_still_attaches_the_image(tmp_path):
    from unittest.mock import patch

    image = tmp_path / "paste.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    async def run():
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            app.set_focus(None)
            await pilot.pause()
            with patch("navin.tui.clipboard.read_clipboard", return_value=""), \
                    patch("navin.tui.images.save_clipboard_image", return_value=image):
                app.post_message(events.Paste(""))
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
            assert f"[image: {image}]" in app.composer.text

    asyncio.run(run())


def test_an_open_tool_row_copies_its_command_and_full_output_with_one_click(tmp_path):
    async def run():
        app = make_app(tmp_path)
        copied: list[str] = []
        output = "".join(f"line {index}\n" for index in range(60))
        async with app.run_test(size=(100, 40)) as pilot:
            await app.submit_text("run the tests")
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiToolEvent("job", "exec", "start", {"command": "pytest -q"}))
            await app._on_runtime_event(UiToolEvent("job", "exec", "output", output=output))
            await app._on_runtime_event(UiToolEvent("job", "exec", "end", result="60 passed"))
            await pilot.pause()
            row = app.query_one(ToolCall)
            chip = row.query_one(".tool-copy", CopyChip)
            if not row._open:
                row.toggle()
                await pilot.pause()
            assert chip.display and row.query_one(".tool-actions").display
            with patch.object(app, "copy_to_clipboard", side_effect=lambda text, **_: copied.append(text)):
                await pilot.click(chip)
                await pilot.pause()
            # Everything the row holds, not only the lines on screen.
            assert copied == [row.copy_text()]
            assert "pytest -q" in copied[0] and "line 0" in copied[0] and "line 59" in copied[0]
            # The click copied; it did not fold the row.
            assert row._open
            row.toggle()
            await pilot.pause()
            assert not chip.display and not row.query_one(".tool-actions").display
    asyncio.run(run())
