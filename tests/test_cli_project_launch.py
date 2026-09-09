# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""`navin .` must open the editor like `cursor .` - the desktop window when
the app is installed, the browser WebUI otherwise - and never break plain CLI
commands. This is the launch path users hit first; a regression here reads as
"navin does nothing"."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.cli import commands


class RewriteProjectDirArgvTest(unittest.TestCase):
    def test_directory_becomes_background_webui(self):
        with tempfile.TemporaryDirectory() as td:
            argv = commands._rewrite_project_dir_argv(["navin", td])
            self.assertEqual(argv[:4], ["navin", "webui", "--background", "--yes"])
            self.assertEqual(argv[argv.index("--project") + 1], str(Path(td).resolve()))

    def test_known_commands_and_flags_are_untouched(self):
        for argv in (["navin", "doctor"], ["navin", "--help"], ["navin"]):
            with self.subTest(argv=argv):
                self.assertIs(commands._rewrite_project_dir_argv(argv), argv)

    def test_missing_directory_is_untouched(self):
        argv = ["navin", "/definitely/not/a/dir"]
        self.assertIs(commands._rewrite_project_dir_argv(argv), argv)


class DesktopShellLaunchTest(unittest.TestCase):
    """The installed apps bundle navin-desktop next to the CLI sidecar."""

    def _run(self, tmp: str, *, with_shell: bool):
        fake_exe = Path(tmp) / ("navin.exe" if sys.platform == "win32" else "navin")
        fake_exe.write_text("")
        shell_name = "navin-desktop.exe" if sys.platform == "win32" else "navin-desktop"
        shell = Path(tmp) / shell_name
        if with_shell:
            shell.write_text("")
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "executable", str(fake_exe)),
            mock.patch.object(commands, "_launch_desktop_shell") as launch,
            mock.patch.object(commands, "app") as app,
            mock.patch.object(sys, "argv", ["navin", tmp]),
        ):
            commands.run()
            final_argv = list(sys.argv)
        return launch, app, shell, final_argv

    def test_project_dir_opens_the_desktop_window(self):
        with tempfile.TemporaryDirectory() as td:
            launch, app, shell, _ = self._run(td, with_shell=True)
            self.assertTrue(launch.called)
            self.assertFalse(app.called, "typer must not also start the WebUI")
            called_shell, called_project = launch.call_args[0]
            self.assertEqual(called_shell, shell)
            self.assertEqual(called_project, str(Path(td).resolve()))

    def test_without_the_app_it_falls_back_to_the_webui(self):
        with tempfile.TemporaryDirectory() as td:
            launch, app, _, final_argv = self._run(td, with_shell=False)
            self.assertFalse(launch.called)
            self.assertTrue(app.called)
            self.assertEqual(final_argv[1], "webui")

    def test_source_installs_never_probe_for_a_shell(self):
        # sys.frozen is absent when running from source; the desktop shell
        # lookup must not trip over that.
        self.assertIsNone(commands._desktop_shell_binary())

    def test_onedir_layout_finds_the_shell_one_level_up(self):
        # Installed layout: <install>/navin-desktop(.exe) with the CLI sidecar
        # below it in <install>/navin-dist/ (the one-dir PyInstaller tree).
        with tempfile.TemporaryDirectory() as td:
            install = Path(td)
            sidecar_dir = install / "navin-dist"
            sidecar_dir.mkdir()
            fake_exe = sidecar_dir / ("navin.exe" if sys.platform == "win32" else "navin")
            fake_exe.write_text("")
            shell_name = "navin-desktop.exe" if sys.platform == "win32" else "navin-desktop"
            shell = install / shell_name
            shell.write_text("")
            with (
                mock.patch.object(sys, "frozen", True, create=True),
                mock.patch.object(sys, "executable", str(fake_exe)),
            ):
                self.assertEqual(commands._desktop_shell_binary(), shell)


if __name__ == "__main__":
    unittest.main()
