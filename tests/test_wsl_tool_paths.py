# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Paths and shell overrides retain their meaning across the Windows/WSL bridge."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from navin.agent.tools import shell
from navin.security import workspace_policy
from navin.utils import wsl

ROOT = r"\\wsl.localhost\Ubuntu\home\me\projet équipe"


@pytest.mark.parametrize("path,expected", [
    ("/home/me/projet équipe/src/new.py", ROOT + r"\src\new.py"),
    ("/tmp/audit.txt", r"\\wsl.localhost\Ubuntu\tmp\audit.txt"),
    ("/", r"\\wsl.localhost\Ubuntu"),
    ("src/new.py", "src/new.py"),
    (r"src\new.py", r"src\new.py"),
    (r"C:\work\new.py", r"C:\work\new.py"),
    (r"\\wsl$\Debian\tmp\new.py", r"\\wsl$\Debian\tmp\new.py"),
])
def test_wsl_paths_round_trip_without_guessing_the_file_location(path, expected):
    assert wsl.host_tool_path(path, ROOT, platform="win32") == expected


@pytest.mark.parametrize("platform,workspace", [
    ("linux", "/home/me/project"), ("darwin", "/Users/me/project"),
    ("win32", r"C:\work\project"), ("win32", r"\\server\share\project"),
])
def test_native_workspaces_keep_native_path_semantics(platform, workspace):
    assert wsl.host_tool_path("/src/file.py", workspace, platform=platform) == "/src/file.py"


def test_shared_tool_resolver_keeps_the_absolute_wsl_path(monkeypatch):
    monkeypatch.setattr(workspace_policy, "host_tool_path", lambda path, root: wsl.host_tool_path(
        path, root, platform="win32",
    ))
    # This is the common resolution entry point for file, Git and exec tools.
    assert workspace_policy.project_rooted_path(
        "/home/me/projet équipe/src/new.py", ROOT, [ROOT],
    ) == ROOT + r"\src\new.py"


@pytest.mark.parametrize("program,login,expected", [
    (None, False, ("bash", "-lc")),
    ("bash", False, ("bash", "-c")),
    ("/bin/bash", True, ("/bin/bash", "-lc")),
    ("/usr/bin/zsh", True, ("/usr/bin/zsh", "-lc")),
    ("sh", True, ("sh", "-c")),
    ("wsl", False, ("bash", "-lc")),
])
def test_wsl_shell_is_resolved_in_the_distribution(tmp_path, monkeypatch, program, login, expected):
    monkeypatch.setattr(shell, "_IS_WINDOWS", True)
    monkeypatch.setattr(shell.shutil, "which", lambda name: None)
    monkeypatch.setattr(wsl, "resolve_distro", lambda name: name)
    monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
    monkeypatch.setattr(shell.ExecTool, "_resolve_cwd", staticmethod(lambda *args, **kwargs: ROOT))
    spawn = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(shell.asyncio, "create_subprocess_exec", spawn)

    async def run():
        tool = shell.ExecTool(working_dir=str(tmp_path))
        prepared = await tool._prepare_command("printf 'ok'", shell=program, login=login)
        assert not isinstance(prepared, str), prepared
        await tool._spawn(
            prepared.command, prepared.cwd, prepared.env, prepared.shell_program, prepared.login,
        )

    asyncio.run(run())
    assert spawn.call_args.args == (
        "wsl.exe", "-d", "Ubuntu", "--cd", "/home/me/projet équipe", "--",
        *expected, "printf 'ok'",
    )


@pytest.mark.parametrize("program", [r"C:\Windows\powershell.exe", "bash -i", "bin/bash", "bash\nwhoami"])
def test_invalid_wsl_shell_returns_a_recoverable_error(program):
    resolved, error = shell.ExecTool()._resolve_shell(program, wsl_project=True)
    assert resolved is None and error.is_error


def test_wsl_shell_keeps_the_operators_allowlist():
    resolved, error = shell.ExecTool(allowed_shells=["bash"])._resolve_shell("/bin/zsh", wsl_project=True)
    assert resolved is None and error.is_error
    assert "Allowed by tools.exec.allowedShells: bash" in error
