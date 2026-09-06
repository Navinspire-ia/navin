"""Web / desktop parity: git must run where the project lives.

The Git panel already routed WSL projects through the distribution. Review and
project search each had their own copy of that logic (or none at all), so the
same repository looked healthy in one panel and absent in the next. These tests
pin the shared helper and the fact that every caller now uses it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navin.utils import wsl
from navin.utils.git_argv import git_argv


class TestGitArgv:
    def test_local_project_targets_the_local_git(self, tmp_path: Path) -> None:
        argv = git_argv(tmp_path, platform="linux")
        assert argv is not None
        assert argv[0].endswith("git")
        assert argv[1:] == ["-C", str(tmp_path)]

    def test_returns_none_when_git_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A missing git is "git is not available", never "not a repository".
        monkeypatch.setattr("navin.utils.git_argv.shutil.which", lambda _name: None)
        assert git_argv(tmp_path, platform="linux") is None

    def test_windows_unc_project_is_routed_into_the_distribution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
        monkeypatch.setattr(wsl, "resolve_distro", lambda name: name)
        monkeypatch.setattr(
            wsl,
            "command_prefix",
            lambda distro, cwd: ["wsl.exe", "-d", distro, "--cd", str(cwd), "--"],
        )
        argv = git_argv(r"\\wsl.localhost\Ubuntu\home\me\proj", platform="win32")
        assert argv == [
            "wsl.exe",
            "-d",
            "Ubuntu",
            "--cd",
            "/home/me/proj",
            "--",
            "git",
        ]

    def test_windows_local_project_stays_on_windows_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
        argv = git_argv(r"C:\Users\me\proj", platform="win32")
        assert argv is not None
        assert argv[1:] == ["-C", r"C:\Users\me\proj"]

    def test_windows_unc_falls_back_when_wsl_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No wsl.exe: the UNC path is all we have, so hand it to Windows git
        # rather than returning nothing at all.
        monkeypatch.setattr(wsl, "wsl_executable", lambda: None)
        argv = git_argv(r"\\wsl.localhost\Ubuntu\home\me\proj", platform="win32")
        assert argv is not None
        assert argv[1] == "-C"


class TestCallersShareTheHelper:
    def test_project_search_delegates(self, tmp_path: Path) -> None:
        from navin.webui.project_search import _git_argv

        assert _git_argv(tmp_path) == git_argv(tmp_path)

    def test_project_insights_delegates(self, tmp_path: Path) -> None:
        from navin.webui.project_insights import _git_argv

        assert _git_argv(tmp_path) == git_argv(tmp_path)

    def test_code_review_uses_the_helper(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: the review panel ran a bare ["git", "-C", root], which
        # Windows git refuses on a WSL project.
        from navin.webui import code_review_api

        seen: list[list[str]] = []

        class _Proc:
            returncode = 0
            stdout = "true\n"
            stderr = ""

        def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
            seen.append(list(argv))
            return _Proc()

        monkeypatch.setattr(code_review_api.subprocess, "run", fake_run)
        code_review_api._git(tmp_path, "rev-parse", "--is-inside-work-tree")
        assert seen, "git was never invoked"
        assert seen[0][:1] != ["git"], "review still shells out to a bare git"
        assert seen[0][-2:] == ["rev-parse", "--is-inside-work-tree"]

    def test_code_review_reports_a_missing_git_clearly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from navin.webui import code_review_api

        monkeypatch.setattr(code_review_api, "git_argv", lambda _root: None)
        code, out, err = code_review_api._git(tmp_path, "status")
        assert code == 1
        assert out == ""
        assert "git" in err.lower()
