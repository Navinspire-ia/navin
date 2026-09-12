# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""TUI path coloring and OS clipboard helpers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from textual.content import Content, Span

from navin.tui.clipboard import (
    decode_windows_clipboard_bytes,
    osc52_allowed,
    pick_paste_text,
    pointer_copy_text,
    read_clipboard,
    write_clipboard,
    write_os_clipboard,
)
from navin.tui.markdown import restyle_inline_code
from navin.tui.paths import PATH_INK, looks_like_path
from navin.tui.theme import NAVIN_DARK, NAVIN_LIGHT, PRIMARY_INK, PRIMARY_INK_LIGHT


class LooksLikePathTests(unittest.TestCase):
    def test_paths_and_filenames(self) -> None:
        for value in (
            "helm/charts/mcp-pod-creator/templates/mcp_pod_creator/deployment.yaml",
            "/tmp/unified_deploy",
            "./src/main.rs",
            "../os/windows/x64/Navin.exe",
            "~/Projects/app",
            r"C:\Users\gadhg\file.ts",
            "deployment.yaml",
            "package.json",
            ".env",
        ):
            self.assertTrue(looks_like_path(value), value)

    def test_commands_and_prose_are_not_paths(self) -> None:
        for value in (
            "cd /tmp/unified_deploy && python3 setup.py",
            "python3 -m pip install foo",
            "true",
            "navin",
            "https://navin.live/docs",
            "v2.0.1",
            "TODO",
            "ls -la /tmp",
        ):
            self.assertFalse(looks_like_path(value), value)


class MarkdownPathStyleTests(unittest.TestCase):
    def test_only_path_spans_become_code_path(self) -> None:
        content = Content(
            "see src/app.ts then run npm install",
            spans=[
                Span(4, 14, ".code_inline"),
                Span(24, 35, ".code_inline"),
            ],
        )
        styled = restyle_inline_code(content)
        styles = [span.style for span in styled.spans]
        self.assertEqual(styles, [".code_path", ".code_inline"])

    def test_theme_exposes_path_green(self) -> None:
        self.assertEqual(NAVIN_DARK.variables["path"], PATH_INK)
        self.assertEqual(NAVIN_LIGHT.variables["path"], "#2D6A4F")
        self.assertNotEqual(NAVIN_DARK.variables["path"], NAVIN_DARK.primary)

    def test_primary_is_sky_not_neon(self) -> None:
        self.assertEqual(NAVIN_DARK.primary, PRIMARY_INK)
        self.assertEqual(NAVIN_LIGHT.primary, PRIMARY_INK_LIGHT)
        self.assertNotEqual(NAVIN_DARK.primary, "#0369FF")
        self.assertNotEqual(NAVIN_LIGHT.primary, "#0369FF")


class ClipboardTests(unittest.TestCase):
    def test_paste_prefers_the_current_os_clipboard(self) -> None:
        self.assertEqual(pick_paste_text("old", "a much longer os paste"), "a much longer os paste")
        self.assertEqual(pick_paste_text("a stale long copy", "new"), "new")
        self.assertEqual(pick_paste_text("kept", ""), "kept")
        self.assertEqual(pick_paste_text("", "os"), "os")
        self.assertFalse(osc52_allowed("x" * 5000))
        self.assertTrue(osc52_allowed("short"))

    def test_windows_keeps_large_copies_in_app(self) -> None:
        large = "x" * 5000
        with (
            patch("navin.tui.clipboard.sys.platform", "linux"),
            patch("navin.tui.clipboard.write_clipboard") as write,
        ):
            self.assertFalse(write_os_clipboard(large))
            write.assert_not_called()
            self.assertTrue(write_os_clipboard("short"))
            write.assert_called_once_with("short")

    def test_macos_writes_large_copies_to_pbcopy(self) -> None:
        large = "x" * 5000
        with (
            patch("navin.tui.clipboard.sys.platform", "darwin"),
            patch("navin.tui.clipboard.write_clipboard", return_value=True) as write,
        ):
            self.assertTrue(write_os_clipboard(large))
            write.assert_called_once_with(large)

    def test_right_click_prefers_selection(self) -> None:
        self.assertEqual(pointer_copy_text("sel", "reply"), "sel")
        self.assertEqual(pointer_copy_text("", "reply"), "reply")
        self.assertEqual(pointer_copy_text("", ""), "")

    def test_write_skips_empty(self) -> None:
        self.assertFalse(write_clipboard(""))

    def test_utf8_accents_are_not_oem_mojibake(self) -> None:
        self.assertEqual(
            decode_windows_clipboard_bytes("tête".encode("utf-8")),
            "tête",
        )
        self.assertEqual(
            decode_windows_clipboard_bytes("tête".encode("utf-16-le")),
            "tête",
        )
        broken = "Problèmes".encode("utf-8").decode("latin-1")
        self.assertNotEqual("Problèmes", broken)

    def test_windows_clip_sends_utf16(self) -> None:
        with (
            patch("navin.tui.clipboard.sys.platform", "linux"),
            patch(
                "navin.tui.clipboard.shutil.which",
                side_effect=lambda name: "/mnt/c/Windows/System32/clip.exe"
                if name == "clip.exe"
                else None,
            ),
            patch("navin.tui.clipboard.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            self.assertTrue(write_clipboard("tête"))
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][0].endswith("clip.exe"), True)
            self.assertEqual(run.call_args.kwargs["input"], "tête".encode("utf-16-le"))
            self.assertNotEqual(run.call_args.kwargs.get("text"), True)

    def test_write_uses_first_successful_backend(self) -> None:
        with (
            patch("navin.tui.clipboard.sys.platform", "linux"),
            patch("navin.tui.clipboard.shutil.which", side_effect=lambda name: name == "xclip"),
            patch("navin.tui.clipboard.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            self.assertTrue(write_clipboard("hello"))
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ["xclip", "-selection", "clipboard"])
            self.assertEqual(run.call_args.kwargs["input"], "hello")

    def test_darwin_prefers_pbcopy(self) -> None:
        with (
            patch("navin.tui.clipboard.sys.platform", "darwin"),
            patch("navin.tui.clipboard.shutil.which", return_value="/usr/bin/pbcopy"),
            patch("navin.tui.clipboard.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            self.assertTrue(write_clipboard("tête"))
            self.assertEqual(run.call_args.args[0], ["pbcopy"])
            self.assertEqual(run.call_args.kwargs["input"], "tête")
            self.assertEqual(run.call_args.kwargs.get("encoding"), "utf-8")

    def test_read_strips_single_trailing_newline(self) -> None:
        with (
            patch("navin.tui.clipboard.sys.platform", "darwin"),
            patch("navin.tui.clipboard.shutil.which", return_value="/usr/bin/pbpaste"),
            patch("navin.tui.clipboard.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = "copied\n"
            self.assertEqual(read_clipboard(), "copied")


class FileEditDiffTextTests(unittest.TestCase):
    def test_reads_unified_diff_payload(self) -> None:
        from navin.tui.runtime import _file_edit_diff_text

        self.assertEqual(
            _file_edit_diff_text({"diff": {"format": "unified", "text": "@@ -1 +1 @@\n+ok\n"}}),
            "@@ -1 +1 @@\n+ok\n",
        )
        self.assertEqual(_file_edit_diff_text({"diff": "raw"}), "raw")
        self.assertEqual(_file_edit_diff_text({}), "")


class MacosBindingsTests(unittest.TestCase):
    def test_app_and_composer_bind_command_keys(self) -> None:
        from navin.tui.app import NavinApp
        from navin.tui.widgets import Composer

        app_keys = {binding.key for binding in NavinApp.BINDINGS}
        composer_keys = {binding.key for binding in Composer.BINDINGS}
        for key in ("super+c", "super+v", "super+shift+c", "super+f", "super+alt+v"):
            self.assertIn(key, app_keys, key)
        for key in ("super+c", "super+v", "super+a", "super+f", "super+alt+v"):
            self.assertIn(key, composer_keys, key)


if __name__ == "__main__":
    unittest.main()
