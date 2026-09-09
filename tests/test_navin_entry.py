# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The packaged entry point must not steal stdout from CLI invocations.

`_is_windows_desktop` once matched on the executable name alone, so every
``navin --version`` / ``navin .`` on Windows had its stdout redirected to the
desktop startup log and printed nothing. An application launch is only the
no-argument, no-terminal case - same rule as the macOS Finder detection.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.config.secrets import ENC_PREFIX, encrypt_secret, load_or_create_fernet


def _load_entry():
    path = Path(__file__).resolve().parent.parent / "packaging" / "pyinstaller" / "navin_entry.py"
    spec = importlib.util.spec_from_file_location("navin_entry_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class WindowsDesktopDetectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.entry = _load_entry()
        self.patches = [
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(sys, "executable", "C:/Navin/navin-dist/navin.exe"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_cli_arguments_always_mean_cli(self) -> None:
        # `navin --version` from PowerShell or WSL must keep its stdout even
        # though the stream is a pipe, not a tty.
        with mock.patch.object(sys, "argv", ["navin", "--version"]):
            self.assertFalse(self.entry._is_windows_desktop())

    def test_bare_launch_without_terminal_is_desktop(self) -> None:
        stdout = mock.Mock()
        stdout.isatty.return_value = False
        with mock.patch.object(sys, "argv", ["navin"]):
            with mock.patch.object(sys, "stdout", stdout):
                self.assertTrue(self.entry._is_windows_desktop())

    def test_bare_launch_in_a_terminal_is_cli(self) -> None:
        stdout = mock.Mock()
        stdout.isatty.return_value = True
        with mock.patch.object(sys, "argv", ["navin"]):
            with mock.patch.object(sys, "stdout", stdout):
                self.assertFalse(self.entry._is_windows_desktop())

    def test_other_executable_names_are_never_desktop(self) -> None:
        with mock.patch.object(sys, "executable", "C:/other/tool.exe"):
            with mock.patch.object(sys, "argv", ["tool"]):
                self.assertFalse(self.entry._is_windows_desktop())


class DesktopConfigUnlockTest(unittest.TestCase):
    def test_encrypted_token_issue_secret_is_unlocked(self) -> None:
        entry = _load_entry()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            navin = home / ".navin"
            navin.mkdir()
            config_path = navin / "config.json"
            fernet = load_or_create_fernet(config_path)
            secret = "ws-issue-secret-desktop"
            config_path.write_text(
                json.dumps(
                    {
                        "channels": {
                            "websocket": {
                                "port": 8766,
                                "tokenIssueSecret": encrypt_secret(secret, fernet),
                            }
                        },
                        "gateway": {"port": 18791},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(entry.Path, "home", return_value=home):
                webui_port, gateway_port, unlocked = entry._desktop_config()
        self.assertEqual(webui_port, 8766)
        self.assertEqual(gateway_port, 18791)
        self.assertEqual(unlocked, secret)
        self.assertFalse(str(unlocked).startswith(ENC_PREFIX))


if __name__ == "__main__":
    unittest.main()
