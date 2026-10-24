# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""User-tool PATH for the packaged desktop app (Linux, Windows, macOS)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.utils import path_env


class PathEnvTest(unittest.TestCase):
    def test_posix_candidates_cover_uvx_and_homebrew(self) -> None:
        home = Path("/Users/me")
        rows = [str(item) for item in path_env._posix_candidates(home)]
        self.assertIn(str(home / ".local" / "bin"), rows)
        self.assertIn("/opt/homebrew/bin", rows)
        self.assertIn("/usr/local/bin", rows)
        self.assertIn("/home/linuxbrew/.linuxbrew/bin", rows)
        self.assertIn("/opt/local/bin", rows)
        self.assertIn(str(home / ".pyenv" / "shims"), rows)
        self.assertIn(str(home / ".asdf" / "shims"), rows)

    def test_nvm_default_alias_wins_over_newest_version(self) -> None:
        """Finder-launched Desktop must use `nvm alias default`, not just the newest node."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for version in ("v20.11.1", "v22.3.0"):
                (home / ".nvm" / "versions" / "node" / version / "bin").mkdir(parents=True)
            rows = path_env._posix_candidates(home)
            self.assertIn(home / ".nvm" / "versions" / "node" / "v22.3.0" / "bin", rows)
            (home / ".nvm" / "alias").mkdir()
            (home / ".nvm" / "alias" / "default").write_text("20\n")
            rows = path_env._posix_candidates(home)
            self.assertIn(home / ".nvm" / "versions" / "node" / "v20.11.1" / "bin", rows)
            self.assertNotIn(home / ".nvm" / "versions" / "node" / "v22.3.0" / "bin", rows)

    def test_windows_candidates_cover_uvx_winget_and_scoop(self) -> None:
        home = Path(r"C:\Users\me")
        env = {
            "APPDATA": r"C:\Users\me\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
            "ProgramFiles": r"C:\Program Files",
        }
        with patch.dict(os.environ, env, clear=False):
            rows = path_env._windows_candidates(home)
        self.assertIn(home / ".local" / "bin", rows)
        self.assertIn(home / "scoop" / "shims", rows)
        self.assertIn(Path(env["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links", rows)
        self.assertIn(Path(env["LOCALAPPDATA"]) / "uv", rows)
        self.assertIn(Path(env["LOCALAPPDATA"]) / "Programs" / "uv", rows)
        self.assertIn(Path(env["APPDATA"]) / "npm", rows)
        # Git for Windows, machine-wide (winget Git.Git) and per-user installs.
        self.assertIn(Path(env["ProgramFiles"]) / "Git" / "cmd", rows)
        self.assertIn(Path(env["LOCALAPPDATA"]) / "Programs" / "Git" / "cmd", rows)

    def test_a_git_installed_after_start_is_put_on_path(self) -> None:
        """Explorer's PATH predates the install: Program Files\\Git\\cmd is added."""
        with tempfile.TemporaryDirectory() as tmp:
            program_files = Path(tmp) / "Program Files"
            git_cmd = program_files / "Git" / "cmd"
            git_cmd.mkdir(parents=True)
            (git_cmd / "git.exe").write_bytes(b"")
            system32 = str(Path(tmp) / "system32")
            env = {"PATH": system32, "ProgramFiles": str(program_files)}
            # os.name stays as it is: patching it to "nt" makes pathlib build
            # WindowsPath objects, which a Linux test host cannot create.
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("LOCALAPPDATA", None)
                os.environ.pop("APPDATA", None)
                rows = path_env._windows_candidates(Path(tmp) / "home")
                with patch.object(path_env, "_posix_candidates", return_value=rows):
                    path_env.augment_path_for_user_tools()
                    parts = os.environ["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], system32)
        self.assertIn(str(git_cmd), parts)

    def test_augment_appends_existing_user_bins_without_dropping_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            local = home / ".local" / "bin"
            local.mkdir(parents=True)
            with patch.object(path_env.Path, "home", return_value=home):
                with patch.object(path_env.os, "name", "posix"):
                    with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False):
                        path_env.augment_path_for_user_tools()
                        parts = os.environ["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], "/usr/bin")
        self.assertIn(str(local), parts)


class EnsureRipgrepTest(unittest.TestCase):
    """Windows installs ripgrep with winget by default, once, in the background."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "ripgrep-install.json"
        for target, kwargs in (
            ("_ripgrep_state_path", {"return_value": self.state}),
            ("augment_path_for_user_tools", {"return_value": None}),
        ):
            patcher = patch.object(path_env, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {path_env.RIPGREP_OPT_OUT_ENV: ""}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.installed = False

    def _which(self, name: str) -> str | None:
        if name in {"winget", "winget.exe"}:
            return r"C:\winget.exe"
        return r"C:\Links\rg.exe" if name == "rg" and self.installed else None

    def _run(self, argv, **kwargs):
        self.installed = True
        self.argv = argv
        self.kwargs = kwargs
        import subprocess

        return subprocess.CompletedProcess(argv, 0, stdout="Successfully installed", stderr="")

    def test_missing_rg_is_installed_with_winget_unattended(self) -> None:
        with patch.object(path_env.shutil, "which", side_effect=self._which):
            result = path_env.ensure_ripgrep(background=False, platform="win32", run=self._run)
        self.assertEqual(result, "installed")
        self.assertEqual(self.argv[:5], [r"C:\winget.exe", "install", "--exact", "--id", "BurntSushi.ripgrep.MSVC"])
        for flag in ("--silent", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"):
            self.assertIn(flag, self.argv)
        self.assertIn("timeout", self.kwargs)
        self.assertTrue(json.loads(self.state.read_text())["ok"])

    def test_an_attempt_this_week_is_not_repeated(self) -> None:
        self.state.write_text(json.dumps({"attempted_at": 1000.0, "ok": False}))
        with patch.object(path_env.shutil, "which", side_effect=self._which):
            result = path_env.ensure_ripgrep(background=False, platform="win32", run=self._run, now=1000.0 + 3600)
        self.assertEqual(result, "recent-attempt")
        self.assertFalse(self.installed)

    def test_present_disabled_and_other_systems_do_nothing(self) -> None:
        self.installed = True
        with patch.object(path_env.shutil, "which", side_effect=self._which):
            self.assertEqual(path_env.ensure_ripgrep(platform="win32", run=self._run), "present")
        self.installed = False
        with patch.dict(os.environ, {path_env.RIPGREP_OPT_OUT_ENV: "1"}):
            self.assertEqual(path_env.ensure_ripgrep(platform="win32", run=self._run), "disabled")
        self.assertEqual(path_env.ensure_ripgrep(platform="linux", run=self._run), "unsupported")
        self.assertFalse(self.installed)

    def test_background_install_returns_at_once(self) -> None:
        import threading

        done = threading.Event()

        def slow_run(argv, **kwargs):
            done.wait(5)
            return self._run(argv, **kwargs)

        with patch.object(path_env.shutil, "which", side_effect=self._which):
            self.assertEqual(path_env.ensure_ripgrep(platform="win32", run=slow_run), "started")
            self.assertEqual(path_env.ensure_ripgrep(platform="win32", run=slow_run), "already-running")
            done.set()
            for _ in range(100):
                if not path_env.winget_install_running(path_env.RIPGREP):
                    break
                threading.Event().wait(0.05)
        self.assertTrue(self.installed)


class EnsureGitTest(unittest.TestCase):
    """Windows without git gets Git for Windows from winget, then finds git.exe."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "git-install.json"
        self.installed = False
        self.augmented = 0

        def augment() -> None:
            self.augmented += 1

        for target, kwargs in (
            ("_git_state_path", {"return_value": self.state}),
            ("augment_path_for_user_tools", {"side_effect": augment}),
        ):
            patcher = patch.object(path_env, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(
            os.environ, {path_env.GIT_OPT_OUT_ENV: "", path_env.RIPGREP_OPT_OUT_ENV: ""}, clear=False
        )
        env.start()
        self.addCleanup(env.stop)

    def _which(self, name: str) -> str | None:
        if name in {"winget", "winget.exe"}:
            return r"C:\winget.exe"
        if name == "git" and self.installed:
            return r"C:\Program Files\Git\cmd\git.exe"
        return None

    def _run(self, argv, **kwargs):
        import subprocess

        self.argv = argv
        self.kwargs = kwargs
        self.installed = True
        return subprocess.CompletedProcess(argv, 0, stdout="Successfully installed", stderr="")

    def test_missing_git_is_installed_with_the_requested_winget_command(self) -> None:
        with patch.object(path_env.shutil, "which", side_effect=self._which):
            result = path_env.ensure_git(background=False, platform="win32", run=self._run)
        self.assertEqual(result, "installed")
        # winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
        self.assertEqual(self.argv[:5], [r"C:\winget.exe", "install", "--exact", "--id", "Git.Git"])
        for flag in ("--silent", "--accept-package-agreements", "--accept-source-agreements"):
            self.assertIn(flag, self.argv)
        self.assertEqual(self.kwargs["timeout"], path_env.GIT.timeout_s)
        # The PATH is completed after winget so Program Files\Git\cmd is seen.
        self.assertGreaterEqual(self.augmented, 2)
        state = json.loads(self.state.read_text())
        self.assertTrue(state["ok"])
        self.assertEqual(state["git"], r"C:\Program Files\Git\cmd\git.exe")

    def test_winget_that_leaves_no_git_is_reported_failed(self) -> None:
        import subprocess

        def broken(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="0x8a150011 Installer failed")

        with patch.object(path_env.shutil, "which", side_effect=self._which):
            result = path_env.ensure_git(background=False, platform="win32", run=broken)
        self.assertEqual(result, "failed")
        state = json.loads(self.state.read_text())
        self.assertFalse(state["ok"])
        self.assertEqual(state["returncode"], 1)
        self.assertIn("Installer failed", state["detail"])

    def test_present_disabled_no_winget_and_other_systems_do_nothing(self) -> None:
        self.installed = True
        with patch.object(path_env.shutil, "which", side_effect=self._which):
            self.assertEqual(path_env.ensure_git(platform="win32", run=self._run), "present")
        self.installed = False
        with patch.dict(os.environ, {path_env.GIT_OPT_OUT_ENV: "1"}):
            self.assertEqual(path_env.ensure_git(platform="win32", run=self._run), "disabled")
        with patch.object(path_env.shutil, "which", return_value=None):
            self.assertEqual(path_env.ensure_git(platform="win32", run=self._run), "no-winget")
        self.assertEqual(path_env.ensure_git(platform="linux", run=self._run), "unsupported")
        self.assertFalse(self.installed)

    def test_hint_tells_the_user_what_happens(self) -> None:
        with patch.object(path_env, "ensure_git", return_value="started"):
            self.assertIn("installing Git for Windows", path_env.git_missing_hint(platform="win32"))
        with patch.object(path_env, "ensure_git", return_value="recent-attempt"):
            hint = path_env.git_missing_hint(platform="win32")
        self.assertIn("winget install --id Git.Git -e --silent", hint)
        self.assertIn(r'Test-Path "C:\Program Files\Git\cmd\git.exe"', hint)
        self.assertEqual(path_env.git_missing_hint(platform="linux"), "")

    def test_ripgrep_and_git_track_their_own_attempts(self) -> None:
        rg_state = Path(self._tmp.name) / "ripgrep-install.json"
        rg_state.write_text(json.dumps({"attempted_at": 1000.0, "ok": False}))
        with patch.object(path_env, "_ripgrep_state_path", return_value=rg_state):
            with patch.object(path_env.shutil, "which", side_effect=self._which):
                self.assertEqual(
                    path_env.ensure_ripgrep(background=False, platform="win32", run=self._run, now=1000.0 + 60),
                    "recent-attempt",
                )
                self.assertEqual(
                    path_env.ensure_git(background=False, platform="win32", run=self._run, now=1000.0 + 60),
                    "installed",
                )


if __name__ == "__main__":
    unittest.main()
