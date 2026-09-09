# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Relaunching Navin.app on macOS must reopen the app window, not a blank tab.

macOS keeps Chrome alive after its last window closes, and Chromium's process
singleton on macOS does not relay command lines to the running instance. A
second launch of Navin.app therefore degraded into a bare "reopen" event: the
``--app=<url>`` argument was dropped and the lingering private-profile Chrome
answered with an empty new-tab window. The fix terminates that window-less
instance (matched by Navin's private profile directory, never the user's own
browser) before launching fresh.
"""

from __future__ import annotations

import unittest
from unittest import mock

from navin.cli import commands


def _run_result(returncode: int) -> mock.Mock:
    return mock.Mock(returncode=returncode)


class StaleBrowserCleanupTest(unittest.TestCase):
    def test_other_platforms_do_nothing(self) -> None:
        with (
            mock.patch("platform.system", return_value="Linux"),
            mock.patch("subprocess.run") as run,
        ):
            commands._close_stale_macos_app_browser()
        run.assert_not_called()

    def test_no_lingering_instance_means_no_kill(self) -> None:
        with (
            mock.patch("platform.system", return_value="Darwin"),
            mock.patch("subprocess.run", side_effect=[_run_result(1)]) as run,
        ):
            commands._close_stale_macos_app_browser()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args_list[0].args[0][0], "pgrep")

    def test_a_lingering_instance_is_terminated_and_awaited(self) -> None:
        # pgrep finds it, pkill fires, the next pgrep confirms it is gone.
        with (
            mock.patch("platform.system", return_value="Darwin"),
            mock.patch(
                "subprocess.run",
                side_effect=[_run_result(0), _run_result(0), _run_result(1)],
            ) as run,
        ):
            commands._close_stale_macos_app_browser()
        commands_run = [call.args[0][0] for call in run.call_args_list]
        self.assertEqual(commands_run, ["pgrep", "pkill", "pgrep"])
        # Only Navin's private profile is targeted, never the user's browser.
        pattern = run.call_args_list[1].args[0][-1]
        self.assertIn("--user-data-dir=", pattern)
        self.assertIn("browser-profile", pattern)

    def test_a_missing_pgrep_never_breaks_the_launch(self) -> None:
        with (
            mock.patch("platform.system", return_value="Darwin"),
            mock.patch("subprocess.run", side_effect=OSError("no pgrep")),
        ):
            commands._close_stale_macos_app_browser()  # must not raise


class AppWindowLaunchTest(unittest.TestCase):
    def test_the_app_window_launch_clears_stale_instances_first(self) -> None:
        order: list[str] = []
        with (
            mock.patch.dict("os.environ", {}, clear=False),
            mock.patch("navin.utils.wsl.is_wsl_guest", return_value=False),
            mock.patch.object(
                commands, "_find_chromium_browser", return_value=["/usr/bin/chrome"]
            ),
            mock.patch.object(
                commands,
                "_close_stale_macos_app_browser",
                side_effect=lambda: order.append("cleanup"),
            ),
            mock.patch.object(commands, "_app_window_chrome_args", return_value=[]),
            mock.patch("subprocess.Popen", side_effect=lambda *a, **k: order.append("launch")),
            mock.patch.object(commands, "console"),
        ):
            import os

            os.environ.pop("NAVIN_WEBUI_TAB", None)
            commands._open_webui_browser("http://127.0.0.1:8766/", wait=False)
        self.assertEqual(order, ["cleanup", "launch"])


if __name__ == "__main__":
    unittest.main()
