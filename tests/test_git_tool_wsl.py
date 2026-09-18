# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""GitTool must use the same WSL routing as desktop repository panels."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from navin.agent.tools import git
from navin.utils import wsl
from navin.utils.git_argv import git_route


def test_wsl_git_runs_without_windows_git_and_uses_linux_working_directory(monkeypatch):
    root = Path(r"\\wsl.localhost\Ubuntu\home\aymen\projects\deploy7\financeIa-v2")
    monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
    monkeypatch.setattr(wsl, "resolve_distro", lambda name: name)
    monkeypatch.setattr(git, "git_route", lambda path: git_route(path, platform="win32"))
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"main\n", b"")))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(git.asyncio, "create_subprocess_exec", spawn)
    code, out, err = asyncio.run(git._git(root, ["status", "--short"]))
    assert code == 0 and out == "main\n" and not err
    argv = spawn.call_args.args
    assert argv[:7] == ("wsl.exe", "-d", "Ubuntu", "--cd", "/home/aymen/projects/deploy7/financeIa-v2", "--", "git")
    assert "\\wsl.localhost" not in " ".join(argv)
    assert "GIT_TERMINAL_PROMPT" in spawn.call_args.kwargs["env"]["WSLENV"]


def test_repository_root_is_translated_back_for_windows_file_access(monkeypatch):
    root = Path(r"\\wsl.localhost\Ubuntu\home\aymen\project\subdir")
    monkeypatch.setattr(git, "_git", AsyncMock(return_value=(0, "/home/aymen/project\n", "")))
    assert str(asyncio.run(git._repo_root(root))) == r"\\wsl.localhost\Ubuntu\home\aymen\project"


@pytest.mark.parametrize("code, reason", [
    (128, "fatal: detected dubious ownership in repository"),
    (127, "git is not installed or not on PATH"),
    (124, "git rev-parse timed out after 10s"),
    (1, "WSL distribution could not be started"),
])
def test_access_errors_never_recommend_initializing_an_existing_repository(tmp_path, monkeypatch, code, reason):
    monkeypatch.setattr(git, "_git", AsyncMock(return_value=(code, "", reason)))
    result = asyncio.run(git.GitTool(workspace=tmp_path).execute(action="status"))
    assert result.is_error
    assert reason in result
    assert "Use action=init" not in result
    assert "not inside a git repository" not in result
