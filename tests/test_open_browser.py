# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""System browser opener used by the account connect handoff."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from navin.utils import wsl
from navin.utils.open_browser import open_external_url, open_local_file, unavailable_reason


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

    def test_windows_opens_url_with_the_native_shell(self):
        shell = mock.Mock(ShellExecuteW=mock.Mock(return_value=42))
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch("sys.platform", "win32"),
            mock.patch("ctypes.windll", create=True, **{"shell32": shell}),
            mock.patch("subprocess.Popen") as popen,
            mock.patch("webbrowser.open") as browser,
        ):
            self.assertTrue(open_external_url("https://navin.live/pricing"))
        shell.ShellExecuteW.assert_called_once_with(
            None, "open", "https://navin.live/pricing", None, None, 1,
        )
        popen.assert_not_called()
        browser.assert_not_called()

    def test_windows_fallback_never_lets_cmd_parse_the_link(self):
        # When ShellExecute refused, the link went to `cmd /c start`: the `&`
        # of a query string was a command separator there, so
        # https://a/?x=1&calc started calc.
        shell = mock.Mock(ShellExecuteW=mock.Mock(return_value=2))
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch("sys.platform", "win32"),
            mock.patch("ctypes.windll", create=True, **{"shell32": shell}),
            mock.patch("shutil.which", side_effect=lambda name: f"C:\\Windows\\{name}"),
            mock.patch("subprocess.Popen") as popen,
        ):
            self.assertTrue(open_external_url("https://a.example/?x=1&calc"))
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ["explorer.exe", "https://a.example/?x=1&calc"])
        self.assertNotIn("cmd", argv)

    def test_windows_shell_failure_does_not_claim_a_file_opened(self):
        shell = mock.Mock(ShellExecuteW=mock.Mock(return_value=2))
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch("sys.platform", "win32"),
            mock.patch("ctypes.windll", create=True, **{"shell32": shell}),
            mock.patch("subprocess.Popen") as popen,
            tempfile.TemporaryDirectory() as directory,
        ):
            report = os.path.join(directory, "report.html")
            with open(report, "w", encoding="utf-8") as handle:
                handle.write("<h1>Audit</h1>")
            self.assertFalse(open_local_file(report))
        popen.assert_not_called()

    def test_uses_host_open_inside_wsl(self):
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=True),
            mock.patch("navin.utils.wsl.open_url_on_host", return_value=True) as host,
            mock.patch("webbrowser.open") as browser,
        ):
            self.assertTrue(open_external_url("https://navin.live/connect"))
            host.assert_called_once()
            browser.assert_not_called()

    def test_wsl_without_interop_or_display_launches_nothing(self):
        """Every Linux opener ends in wslview there, which printed over the CLI."""
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=True),
            mock.patch("navin.utils.wsl.host_interop_available", return_value=False),
            # Not even WSL's /init can start a Windows program.
            mock.patch("navin.utils.wsl.windows_program_argv", side_effect=list),
            mock.patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}),
            mock.patch("subprocess.run") as run,
            mock.patch("subprocess.Popen") as popen,
            mock.patch("webbrowser.open") as browser,
        ):
            self.assertFalse(open_external_url("https://navin.live/connect"))
            self.assertEqual(unavailable_reason("https://navin.live/connect"), wsl.INTEROP_OFF)
        run.assert_not_called()
        popen.assert_not_called()
        browser.assert_not_called()

    def test_wslg_whose_only_browser_is_wslview_is_not_a_success(self):
        """The machine this was found on: DISPLAY=:0, x-www-browser and the https handler are wslview."""
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=True),
            mock.patch("navin.utils.wsl.host_interop_available", return_value=False),
            mock.patch.dict(os.environ, {"DISPLAY": ":0", "BROWSER": "wslview"}),
            mock.patch("shutil.which", side_effect=lambda n: "/usr/bin/" + n if n in {"wslview", "x-www-browser"} else None),
            mock.patch("os.path.realpath", return_value="/usr/bin/wslview"),
            mock.patch("subprocess.Popen") as popen,
        ):
            self.assertFalse(open_external_url("https://navin.live/connect"))
        popen.assert_not_called()

    def test_wslg_with_a_real_linux_browser_opens_it_directly(self):
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=True),
            mock.patch("navin.utils.wsl.host_interop_available", return_value=False),
            mock.patch.dict(os.environ, {"DISPLAY": ":0", "BROWSER": ""}),
            mock.patch("shutil.which", side_effect=lambda n: "/usr/bin/firefox" if n in {"firefox", "/usr/bin/firefox"} else None),
            mock.patch("os.path.realpath", side_effect=lambda p: p),
            mock.patch("subprocess.Popen") as popen,
        ):
            self.assertTrue(open_external_url("https://navin.live/connect"))
        # Straight to the browser: xdg-open would pick the wslview handler.
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/firefox", "https://navin.live/connect"])
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_no_launcher_is_an_honest_false_never_webbrowser(self):
        """webbrowser ran www-browser on the CLI's own terminal and waited for it."""
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch("sys.platform", "linux"),
            mock.patch("os.path.isfile", return_value=False),
            mock.patch("shutil.which", return_value=None),
            mock.patch("subprocess.Popen") as popen,
            mock.patch("webbrowser.open") as browser,
        ):
            self.assertFalse(open_external_url("https://navin.live/pricing"))
            self.assertEqual(unavailable_reason("https://navin.live/pricing"), "No browser could be opened from here.")
        popen.assert_not_called()
        browser.assert_not_called()
        self.assertEqual(unavailable_reason("notes/plan.md"), "Only web links open in the browser.")


class OpenLocalFileTest(unittest.TestCase):
    """A report the terminal cannot show opens with the system's default app."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.report = os.path.join(folder.name, "report.html")
        with open(self.report, "w", encoding="utf-8") as handle:
            handle.write("<h1>Audit</h1>")

    def test_a_missing_file_starts_nothing(self):
        with (
            mock.patch("navin.utils.wsl.open_path_on_host") as host,
            mock.patch("subprocess.Popen") as popen,
        ):
            self.assertFalse(open_local_file(self.report + ".gone"))
            self.assertFalse(open_local_file(""))
        host.assert_not_called()
        popen.assert_not_called()

    def test_linux_hands_the_file_to_xdg_open(self):
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch("sys.platform", "linux"),
            mock.patch("shutil.which", side_effect=lambda n: n if n == "/usr/bin/xdg-open" else None),
            mock.patch("subprocess.Popen") as popen,
        ):
            self.assertTrue(open_local_file(self.report))
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/xdg-open", self.report])
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_wsl_opens_the_file_with_its_windows_default_app(self):
        with (
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=True),
            mock.patch("navin.utils.wsl.open_path_on_host", return_value=True) as host,
            mock.patch("subprocess.Popen") as popen,
        ):
            self.assertTrue(open_local_file(self.report))
        host.assert_called_once_with(self.report)
        popen.assert_not_called()
