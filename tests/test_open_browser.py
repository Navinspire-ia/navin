# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""System browser opener used by the account connect handoff."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from navin.utils.open_browser import open_external_url


class OpenExternalUrlTest(unittest.TestCase):
    def test_rejects_non_http(self):
        self.assertFalse(open_external_url("javascript:alert(1)"))
        self.assertFalse(open_external_url(""))

    def test_linux_uses_host_xdg_open(self):
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch("sys.platform", "linux"),
            mock.patch("os.path.isfile", side_effect=lambda p: p == "/usr/bin/xdg-open"),
            mock.patch("shutil.which", return_value=None),
            mock.patch("subprocess.Popen") as popen,
            mock.patch("webbrowser.open") as browser,
        ):
            self.assertTrue(open_external_url("https://navin.live/connect"))
            popen.assert_called_once()
            self.assertEqual(
                popen.call_args.args[0],
                ["/usr/bin/xdg-open", "https://navin.live/connect"],
            )
            self.assertIn("env", popen.call_args.kwargs)
            browser.assert_not_called()

    def test_linux_bundle_strips_library_path_for_xdg_open(self):
        bundled = {
            "PATH": "/tmp/appdir/usr/bin:/usr/bin",
            "LD_LIBRARY_PATH": "/tmp/appdir/usr/lib",
            "APPIMAGE": "/tmp/Navin.AppImage",
        }
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch("sys.platform", "linux"),
            mock.patch("os.path.isfile", side_effect=lambda p: p == "/usr/bin/xdg-open"),
            mock.patch("shutil.which", return_value=None),
            mock.patch.dict(os.environ, bundled, clear=False),
            mock.patch("subprocess.Popen") as popen,
            mock.patch("webbrowser.open") as browser,
        ):
            self.assertTrue(open_external_url("https://navin.live/pricing"))
            env = popen.call_args.kwargs["env"]
            self.assertNotIn("LD_LIBRARY_PATH", env)
            browser.assert_not_called()

    def test_linux_deb_rpm_frozen_also_strips_library_path(self):
        bundled = {"LD_LIBRARY_PATH": "/tmp/_MEIxyz/lib"}
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch("sys.platform", "linux"),
            mock.patch("sys.frozen", True, create=True),
            mock.patch("os.path.isfile", side_effect=lambda p: p == "/usr/bin/xdg-open"),
            mock.patch("shutil.which", return_value=None),
            mock.patch.dict(os.environ, bundled, clear=False),
            mock.patch("subprocess.Popen") as popen,
        ):
            self.assertTrue(open_external_url("https://navin.live/pricing"))
            self.assertNotIn("LD_LIBRARY_PATH", popen.call_args.kwargs["env"])

    def test_macos_still_uses_open_without_linux_env(self):
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch("sys.platform", "darwin"),
            mock.patch("shutil.which", return_value="/usr/bin/open"),
            mock.patch("os.path.isfile", return_value=True),
            mock.patch("subprocess.Popen") as popen,
            mock.patch("webbrowser.open") as browser,
        ):
            self.assertTrue(open_external_url("https://navin.live/pricing"))
            self.assertEqual(popen.call_args.args[0], ["open", "https://navin.live/pricing"])
            self.assertNotIn("env", popen.call_args.kwargs)
            browser.assert_not_called()

    def test_windows_still_uses_startfile(self):
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch("sys.platform", "win32"),
            mock.patch("os.startfile", create=True) as startfile,
            mock.patch("subprocess.Popen") as popen,
            mock.patch("webbrowser.open") as browser,
        ):
            self.assertTrue(open_external_url("https://navin.live/pricing"))
            startfile.assert_called_once_with("https://navin.live/pricing")
            popen.assert_not_called()
            browser.assert_not_called()

    def test_uses_host_open_inside_wsl(self):
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=True),
            mock.patch("navin.utils.wsl.open_url_on_host", return_value=True) as host,
            mock.patch("webbrowser.open") as browser,
        ):
            self.assertTrue(open_external_url("https://navin.live/connect"))
            host.assert_called_once()
            browser.assert_not_called()
