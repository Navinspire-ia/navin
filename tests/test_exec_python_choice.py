# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""`python` means navin's interpreter for skill scripts, the project's in a Python project."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

from navin import python_runtime
from navin.agent.tools import shell
from navin.agent.tools.shell import ExecTool, _project_venv_bin, _python_project_root


def test_python_project_detection(tmp_path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "backend" / "app").mkdir()
    assert _python_project_root(tmp_path / "backend" / "app") == (tmp_path / "backend").resolve()
    (tmp_path / "docs").mkdir()
    assert _python_project_root(tmp_path / "docs") is None or _python_project_root(tmp_path / "docs") != tmp_path / "backend"


def _venv(folder: Path) -> Path:
    bin_dir = folder / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True)
    (folder / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    return bin_dir


def test_path_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(shell, "_navin_python_dir", lambda: "/navin/np")
    tool = ExecTool(working_dir=str(tmp_path), runtime_bin="/navin/bin", path_prepend="/op/pre")
    (tmp_path / "skill").mkdir()
    front, back = tool._path_for(str(tmp_path / "skill"), "")
    assert front.split(os.pathsep)[0] == "/navin/bin"  # skill scripts: navin first
    project = tmp_path / "api"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='api'\n")
    front, back = tool._path_for(str(project), "")
    assert "/navin/bin" not in front.split(os.pathsep)
    assert back.split(os.pathsep)[-2:] == ["/navin/bin", "/navin/np"]  # fallback + navin-python
    venv_bin = _venv(project)
    front, _ = tool._path_for(str(project), "")
    assert front.split(os.pathsep)[0] == str(venv_bin.resolve())
    assert _project_venv_bin(project / "src", project.resolve()) == venv_bin.resolve()


def test_store_alias_is_not_a_python(monkeypatch) -> None:
    fake = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"
    monkeypatch.setattr(python_runtime.shutil, "which", lambda name, path=None: fake if name == "python" else None)
    assert python_runtime.system_python() is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX interpreters")
def test_live_python_choice(tmp_path) -> None:
    system = python_runtime.system_python(os.environ.get("PATH"))
    if system is None:
        pytest.skip("no system python on this machine")
    runtime_bin = str(Path(sys.executable).parent)  # navin's own venv, like a source install
    tool = ExecTool(timeout=30, working_dir=str(tmp_path), runtime_bin=runtime_bin)

    def which_python(cwd: Path) -> str:
        out = str(asyncio.run(tool.execute(
            command="python3 -c 'import sys; print(\"EXE=\" + sys.executable)'", working_dir=str(cwd))))
        return out.split("EXE=", 1)[1].split()[0]

    skill = tmp_path / "skill"
    skill.mkdir()
    assert Path(which_python(skill)).parent == Path(runtime_bin)  # navin's interpreter
    project = tmp_path / "api"
    project.mkdir()
    (project / "requirements.txt").write_text("")
    assert Path(which_python(project)).parent != Path(runtime_bin)  # the user's python
    out = str(asyncio.run(tool.execute(command="navin-python -c 'print(1+1)'", working_dir=str(project))))
    assert "2" in out


def test_bundled_library_hint() -> None:
    hint = shell._failure_hint("ModuleNotFoundError: No module named 'docx'")
    assert "navin-python" in hint


def test_not_a_repo_names_the_project_root(tmp_path) -> None:
    """git -C on a folder outside the repo gets one actionable line, not the generic diagnosis."""
    repo = tmp_path / "proj"
    (repo / ".git").mkdir(parents=True)
    out = "fatal: not a git repository (or any of the parent directories): .git\n\nExit code: 128"
    hint = shell._not_a_repo_hint("git -C /tmp rev-parse --show-toplevel", out, str(repo), str(repo))
    assert hint.strip() == f"Hint: /tmp is not a git repository - use the project root {repo}"
    assert shell._not_a_repo_hint("ls", "Exit code: 2", str(repo), str(repo)) == ""


def test_not_a_repo_without_any_repo(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(shell, "_find_git_root", lambda start: None)
    out = "fatal: not a git repository (or any of the parent directories): .git"
    hint = shell._not_a_repo_hint("git status", out, str(tmp_path), str(tmp_path))
    assert "is not a git repository" in hint and "git init" in hint


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_exec_git_outside_repo_replaces_generic_hint(tmp_path) -> None:
    if not shutil.which("git"):
        pytest.skip("git missing")
    repo = tmp_path / "proj"
    repo.mkdir()
    import subprocess
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    tool = ExecTool(timeout=30, working_dir=str(repo))
    out = str(asyncio.run(tool.execute(
        command=f"GIT_CEILING_DIRECTORIES={tmp_path} git -C {outside} status")))
    assert f"use the project root {repo}" in out
    assert "Likely cause" not in out
