# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Skill install must run git / npm with host libraries (issue #15)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.plugins.manager import PluginError, PluginManager
from navin.utils.host_env import failure_detail, host_tool_env

_BUNDLE = "/usr/lib/Navin/navin-dist/_internal"


class HostToolEnvTests(unittest.TestCase):
    def test_strips_bundle_keeps_user_entries(self) -> None:
        env = host_tool_env(
            {"LD_LIBRARY_PATH": f"{_BUNDLE}:/opt/custom/lib", "PATH": "/usr/bin"},
            GIT_TERMINAL_PROMPT="0",
        )
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/custom/lib")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_drops_key_when_only_bundle(self) -> None:
        env = host_tool_env({"LD_LIBRARY_PATH": _BUNDLE, "DYLD_LIBRARY_PATH": _BUNDLE})
        self.assertNotIn("LD_LIBRARY_PATH", env)
        self.assertNotIn("DYLD_LIBRARY_PATH", env)

    def test_restores_bootloader_original(self) -> None:
        env = host_tool_env({"LD_LIBRARY_PATH": _BUNDLE, "LD_LIBRARY_PATH_ORIG": "/home/u/lib"})
        self.assertEqual(env["LD_LIBRARY_PATH"], "/home/u/lib")

    def test_unrelated_internal_path_is_kept(self) -> None:
        env = host_tool_env({"LD_LIBRARY_PATH": "/opt/my_internal/lib"})
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/my_internal/lib")

    def test_failure_detail_keeps_first_error(self) -> None:
        raw = "git-remote-https: version `OPENSSL_3.2.0' not found (required by /usr/lib/libcurl.so.4)\n"
        raw += "x" * 1000 + "\nfatal: remote helper 'https' aborted session"
        detail = failure_detail(raw)
        self.assertTrue(detail.startswith("git-remote-https: version"))
        self.assertTrue(detail.endswith("aborted session"))
        self.assertLessEqual(len(detail), 410)


class SkillInstallEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = PluginManager(root=Path(self._tmp.name) / "plugins")
        patcher = patch.dict("os.environ", {"LD_LIBRARY_PATH": f"{_BUNDLE}:/opt/custom/lib"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_git_clone_runs_without_bundle_libs(self) -> None:
        seen: dict = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)

            class Result:
                returncode = 128
                stdout = ""
                stderr = "fatal: remote helper 'https' aborted session"

            return Result()

        with patch("navin.plugins.manager.subprocess.run", side_effect=fake_run):
            with self.assertRaises(PluginError):
                self.manager.install_from_git("https://github.com/acme/skill.git")
        self.assertEqual(seen["env"]["LD_LIBRARY_PATH"], "/opt/custom/lib")
        self.assertEqual(seen["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_npm_pack_runs_without_bundle_libs(self) -> None:
        seen: dict = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)

            class Result:
                returncode = 1
                stdout = ""
                stderr = "npm ERR! 404"

            return Result()

        with patch("navin.plugins.manager.subprocess.run", side_effect=fake_run):
            with self.assertRaises(PluginError):
                self.manager.install_from_npx("@acme/skill")
        self.assertEqual(seen["env"]["LD_LIBRARY_PATH"], "/opt/custom/lib")


if __name__ == "__main__":
    unittest.main()
