# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One package-manager command at a time per project.

A measured session ran npm install, npm list and rmdir node_modules while an
earlier npm install was still running in the background: on Windows they
failed on locked files and the agent looped; elsewhere they race on
node_modules and the lockfile.
"""

from __future__ import annotations

import asyncio

from navin.agent.tools import shell
from navin.agent.tools.shell import ExecTool


class _Process:
    def __init__(self, finish_after: float | None) -> None:
        self.returncode: int | None = None
        self._finish_after = finish_after

    async def wait(self) -> int:
        if self._finish_after is None:
            await asyncio.sleep(3600)
        await asyncio.sleep(self._finish_after)
        self.returncode = 0
        return 0


class _Session:
    def __init__(self, command: str, cwd: str, finish_after: float | None) -> None:
        self.command = command
        self.cwd = cwd
        self.process = _Process(finish_after)


class _Manager:
    def __init__(self, sessions) -> None:
        self._sessions = sessions

    def running_sessions(self):
        return [(f"s{i}", s) for i, s in enumerate(self._sessions) if s.process.returncode is None]


def _tool(tmp_path, sessions) -> ExecTool:
    tool = ExecTool(working_dir=str(tmp_path))
    tool._session_manager = _Manager(sessions)
    return tool


def test_second_install_waits_for_the_running_one(tmp_path):
    running = _Session("cd app && npm install @mui/material", str(tmp_path), finish_after=0.05)
    tool = _tool(tmp_path, [running])
    note = asyncio.run(tool._wait_for_package_manager("npm list @mui/material", str(tmp_path / "app")))
    assert note.startswith("[navin] Waited")
    assert running.process.returncode == 0


def test_scripts_and_other_projects_do_not_wait(tmp_path):
    running = _Session("npm install", str(tmp_path / "app"), finish_after=None)
    tool = _tool(tmp_path, [running])
    assert asyncio.run(tool._wait_for_package_manager("npm run dev", str(tmp_path / "app"))) == ""
    assert asyncio.run(tool._wait_for_package_manager("npm install", str(tmp_path / "other"))) == ""
    dev_server = _Session("npm run dev", str(tmp_path / "web"), finish_after=None)
    tool = _tool(tmp_path, [dev_server])
    assert asyncio.run(tool._wait_for_package_manager("npm install", str(tmp_path / "web"))) == ""


def test_a_stuck_install_refuses_instead_of_racing(tmp_path, monkeypatch):
    monkeypatch.setattr(shell, "_PACKAGE_MANAGER_WAIT_S", 0.05)
    running = _Session("npm install", str(tmp_path), finish_after=None)
    tool = _tool(tmp_path, [running])
    result = asyncio.run(tool._wait_for_package_manager("rmdir /s /q node_modules", str(tmp_path)))
    assert result.startswith("Error") and "s0" in result and "was not run" in result


def test_an_adopted_foreground_command_holds_dependency_work_whatever_its_name(tmp_path):
    # e.g. "./scripts/bootstrap.sh" ran past 60s and was adopted: unknown name,
    # but it was meant to finish, so a command touching node_modules waits.
    running = _Session("./scripts/bootstrap.sh", str(tmp_path), finish_after=0.05)
    running.adopted = True
    tool = _tool(tmp_path, [running])
    note = asyncio.run(tool._wait_for_package_manager("ls node_modules/.bin", str(tmp_path)))
    assert note.startswith("[navin] Waited")


def test_a_server_started_in_background_never_holds_anything(tmp_path):
    server = _Session("./run-server.sh", str(tmp_path), finish_after=None)  # adopted stays False
    tool = _tool(tmp_path, [server])
    assert asyncio.run(tool._wait_for_package_manager("npm install", str(tmp_path))) == ""


def test_different_toolchains_in_one_tree_do_not_wait_for_each_other(tmp_path):
    install = _Session("npm install", str(tmp_path), finish_after=None)
    tool = _tool(tmp_path, [install])
    assert asyncio.run(tool._wait_for_package_manager("terraform apply", str(tmp_path / "infra"))) == ""
    assert asyncio.run(tool._wait_for_package_manager("python manage.py migrate", str(tmp_path))) == ""


def test_same_family_infra_and_migrations_wait(tmp_path):
    for running_cmd, new_cmd in (
        ("terraform apply -auto-approve", "terraform plan"),
        ("npx prisma migrate dev", "npx prisma generate"),
        ("helm upgrade api ./chart", "helm upgrade api ./chart"),
    ):
        running = _Session(running_cmd, str(tmp_path), finish_after=0.02)
        tool = _tool(tmp_path, [running])
        assert asyncio.run(tool._wait_for_package_manager(new_cmd, str(tmp_path))).startswith("[navin] Waited")


def test_a_dev_server_adopted_by_mistake_never_blocks(tmp_path):
    server = _Session("npm run dev", str(tmp_path), finish_after=None)
    server.adopted = True
    tool = _tool(tmp_path, [server])
    assert asyncio.run(tool._wait_for_package_manager("npm install axios", str(tmp_path))) == ""


def test_an_unknown_adopted_command_only_delays_briefly(tmp_path, monkeypatch):
    monkeypatch.setattr(shell, "_UNKNOWN_SESSION_WAIT_S", 0.05)
    job = _Session("./scripts/long-job.sh", str(tmp_path), finish_after=None)
    job.adopted = True
    tool = _tool(tmp_path, [job])
    note = asyncio.run(tool._wait_for_package_manager("npm install", str(tmp_path)))
    assert note.startswith("[navin]") and "ran this anyway" in note


def test_installs_stay_in_the_foreground_until_they_finish(tmp_path):
    tool = ExecTool(working_dir=str(tmp_path))

    def timeout_for(command, timeout=None):
        prepared = asyncio.run(tool._prepare_command(command, None, timeout, None, None))
        assert not isinstance(prepared, str), prepared
        return prepared.timeout

    assert timeout_for("npm install @mui/material") == shell._INSTALL_TIMEOUT_S
    assert timeout_for("cd app && pnpm add zod") == shell._INSTALL_TIMEOUT_S
    # Servers keep the short window so they hand over quickly; plain commands too.
    assert timeout_for("npm run dev") == tool.timeout
    assert timeout_for("ls -la") == tool.timeout
    # A timeout the model asked for stands.
    assert timeout_for("npm install", 90) == 90


def test_npm_runs_without_audit_fund_or_progress_noise():
    from navin.agent.tools.exec_env import build_exec_env

    env = build_exec_env(base={"HOME": "/h"}, environ={})
    assert env["npm_config_audit"] == "false" and env["npm_config_fund"] == "false"
    assert env["npm_config_progress"] == "false"
    kept = build_exec_env(base={"HOME": "/h"}, environ={"npm_config_audit": "true"})
    assert "npm_config_audit" not in kept or kept["npm_config_audit"] == "true"


def test_a_cancelled_scaffold_says_to_use_a_sub_folder():
    hint = shell._scaffold_hint(
        "npm create vite@latest . -- --template react-ts", "└  Operation cancelled\n\nExit code: 0"
    )
    assert "sub-folder" in hint and "Do not retry" in hint
    assert shell._scaffold_hint("npm install", "Operation cancelled") == ""
    assert shell._scaffold_hint("npm create vite@latest app", "Done. Now run: cd app") == ""


def test_shell_search_hint_moves_recursive_code_search_to_grep_tool():
    from navin.agent.tools import shell as shell_tool

    for command in (
        'grep -rn "def foo" navin/ | head -20',
        "cd backend && grep -n -E -r 'webhook' routers/",
        "grep --include=*.py --recursive foo .",
        "cd frontend && rg -n useSearchParams src",
        "git grep -n TODO",
    ):
        assert "grep tool" in shell_tool._shell_search_hint(command), command

    for command in (
        "grep -n foo navin/agent/x.py",
        "ls tests | grep -i spawn",
        "cat log.txt | rg error",
        "grep -rl 'Audit' /mnt/c/Users/me/.navin/webui",
        "grep -r foo ~/.navin/logs",
        "npm run build",
    ):
        assert shell_tool._shell_search_hint(command) == "", command
