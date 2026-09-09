# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""IDE path when Windows opens a project that lives in WSL.

The desktop app's normal case is a UNC root (``\\\\wsl.localhost\\<distro>\\...``).
Git, the integrated terminal, and the workspace watcher must agree on that
boundary. The routing is asserted on every host; the live UNC walk runs only
when a Windows runner can see a distribution.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from navin.utils import wsl as wsl_module
from navin.utils.git_argv import git_argv, git_route
from navin.webui import fs_watch, terminal_ws


class WindowsWslIdeChainTest(unittest.TestCase):
    """Git, terminal launch and the watcher agree on one WSL project."""

    UNC = r"\\wsl.localhost\Ubuntu\home\me\proj"

    def setUp(self) -> None:
        self.addCleanup(setattr, wsl_module, "_distros_cache", None)
        wsl_module._distros_cache = ["Ubuntu"]
        wsl_module._distros_cached_at = float("inf")
        self.exe = mock.patch.object(wsl_module, "wsl_executable", return_value="wsl.exe")
        self.exe.start()
        self.addCleanup(self.exe.stop)

    def test_git_and_terminal_name_the_same_distribution(self) -> None:
        route = git_route(self.UNC, platform="win32")
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.distro, "Ubuntu")
        self.assertEqual(route.root, "/home/me/proj")
        self.assertEqual(route.argv[-1], "git")

        windows = mock.patch.object(terminal_ws, "_IS_WINDOWS", True)
        windows.start()
        self.addCleanup(windows.stop)
        name, path, args, cwd = terminal_ws.prepare_launch(None, self.UNC)
        self.assertEqual(name, "WSL: Ubuntu")
        self.assertEqual(path, "wsl.exe")
        self.assertEqual(args, ["-d", "Ubuntu", "--cd", "/home/me/proj"])
        self.assertNotIn("wsl.localhost", cwd)

    def test_git_argv_prefix_is_the_wsl_bridge(self) -> None:
        argv = git_argv(self.UNC, platform="win32")
        self.assertIsNotNone(argv)
        assert argv is not None
        self.assertEqual(argv[0], "wsl.exe")
        self.assertIn("Ubuntu", argv)
        self.assertEqual(argv[-1], "git")

    def test_workspace_signature_sees_a_real_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "readme.md").write_text("one", encoding="utf-8")
            first = fs_watch.workspace_signature(root)
            (root / "extra.txt").write_text("a new file changes the digest", encoding="utf-8")
            second = fs_watch.workspace_signature(root)
        self.assertNotEqual(first, second)


class LiveRepoFromThisHostTest(unittest.TestCase):
    """The current checkout, the way the IDE would open it from inside WSL."""

    def test_git_status_and_watch_on_this_tree(self) -> None:
        root = Path(__file__).resolve().parents[1]
        git_dir = root / ".git"
        if not git_dir.exists():
            self.skipTest("not a git checkout")
        argv = git_argv(root)
        if argv is None:
            self.skipTest("git is not available")
        out = subprocess.check_output(  # noqa: S603
            [*argv, "rev-parse", "--is-inside-work-tree"],
            text=True,
        )
        self.assertIn("true", out.lower())
        self.assertNotEqual(fs_watch.workspace_signature(root), 0)

    def test_unc_routing_keeps_this_posix_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        posix = root.as_posix()
        unc = r"\\wsl.localhost\Ubuntu" + posix.replace("/", "\\")
        with mock.patch.object(wsl_module, "wsl_executable", return_value="wsl.exe"):
            with mock.patch.object(wsl_module, "distributions", return_value=["Ubuntu"]):
                route = git_route(unc, platform="win32")
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.root, posix)
        self.assertEqual(route.distro, "Ubuntu")


@unittest.skipUnless(sys.platform == "win32", "Windows host required")
class LiveWindowsWslIdeTest(unittest.TestCase):
    """Create a repo inside a distribution and drive it through the UNC path."""

    def test_unc_git_init_and_signature(self) -> None:
        if not wsl_module.wsl_executable():
            self.skipTest("wsl.exe is missing")
        names = wsl_module.distributions(refresh=True)
        if not names:
            self.skipTest("no WSL distribution is installed")
        distro = names[0]
        prefix = wsl_module.command_prefix(distro)
        posix = subprocess.check_output(  # noqa: S603
            [*prefix, "sh", "-c", "mktemp -d /tmp/navin-wsl-ide.XXXXXX"],
            text=True,
        ).strip()
        if not posix.startswith("/"):
            self.skipTest("could not create a temp dir in the distribution")
        unc = wsl_module.to_unc(distro, posix)
        try:
            argv = git_argv(unc, platform="win32")
            self.assertIsNotNone(argv)
            assert argv is not None
            subprocess.check_call(  # noqa: S603
                [*argv, "init"],
                timeout=30,
            )
            readme = wsl_module.to_unc(distro, f"{posix}/readme.md")
            Path(readme).write_text("hello from windows\n", encoding="utf-8")
            first = fs_watch.workspace_signature(unc)
            Path(readme).write_text("changed\n", encoding="utf-8")
            # mtime resolution on 9p can be coarse; give it a beat.
            time.sleep(0.05)
            second = fs_watch.workspace_signature(unc)
            self.assertNotEqual(first, second)
        finally:
            subprocess.run(  # noqa: S603
                [*prefix, "rm", "-rf", posix],
                check=False,
                timeout=15,
            )


if __name__ == "__main__":
    unittest.main()
