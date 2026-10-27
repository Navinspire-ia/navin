# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Heavy disk work in tools must run off the shared engine event loop."""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path

import pytest

from navin.agent.checkpoints import (
    TurnRecorder,
    bind_checkpoint_recorder,
    reset_checkpoint_recorder,
)
from navin.agent.tools import file_manage
from navin.agent.tools.file_manage import ManageFilesTool
from navin.agent.tools.file_state import FileStates

_ROOT = Path(__file__).resolve().parents[1] / "navin" / "agent" / "tools"
_MODULES = sorted(p.name for p in _ROOT.glob("*.py") if p.name != "__init__.py")
# Reading, writing and modifying files, memory, git, exec, the browser and the
# sandbox are the tools the user drives all day: every one of them is scanned,
# so a new blocking read or write on the loop fails here before it ships.
_MUST_COVER = {
    "filesystem.py", "file_manage.py", "shell.py", "exec_session.py", "browser.py",
    "cloud_sandbox.py", "git_tool.py", "notebook.py", "mobile.py", "quality.py",
}
_SHUTIL = {"rmtree", "copytree", "move", "copy2"}
_METHODS = {"read_bytes", "write_bytes", "read_text", "write_text"}


def _blocking_calls(node: ast.AsyncFunctionDef) -> list[int]:
    """Lines of blocking calls in an async body, skipping nested defs and to_thread args."""
    found: list[int] = []

    def visit(n: ast.AST) -> None:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return
        if isinstance(n, ast.Call):
            func = n.func
            if isinstance(func, ast.Attribute) and func.attr == "to_thread":
                return
            if isinstance(func, ast.Attribute):
                is_shutil = (
                    func.attr in _SHUTIL
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "shutil"
                )
                if is_shutil or func.attr in _METHODS:
                    found.append(n.lineno)
        for child in ast.iter_child_nodes(n):
            visit(child)

    for stmt in node.body:
        visit(stmt)
    return found


@pytest.mark.parametrize("module", _MODULES)
def test_no_blocking_disk_calls_in_async_bodies(module: str) -> None:
    tree = ast.parse((_ROOT / module).read_text(encoding="utf-8"))
    offenders = [
        f"{module}:{line} in async def {fn.name}"
        for fn in ast.walk(tree)
        if isinstance(fn, ast.AsyncFunctionDef)
        for line in _blocking_calls(fn)
    ]
    assert not offenders, "blocking disk call on the event loop:\n" + "\n".join(offenders)


def test_the_guard_scans_every_tool_module() -> None:
    present = {name for name in _MUST_COVER if (_ROOT / name).is_file()}
    assert {"filesystem.py", "file_manage.py", "shell.py", "browser.py"} <= present
    assert present <= set(_MODULES)
    assert len(_MODULES) > 40


def test_manage_files_delete_does_not_freeze_loop(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    folder = root / "big"
    folder.mkdir()
    for i in range(20):
        (folder / f"f{i}.txt").write_text("x", encoding="utf-8")

    real_rmtree = file_manage.shutil.rmtree

    def slow_rmtree(path, *args, **kwargs):
        time.sleep(0.6)
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(file_manage.shutil, "rmtree", slow_rmtree)
    tool = ManageFilesTool(workspace=root, allowed_dir=root, file_states=FileStates())

    async def scenario() -> tuple[float, float, str]:
        gaps: list[float] = []
        stop = asyncio.Event()

        async def ticker() -> None:
            last = time.perf_counter()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.03)
        token = bind_checkpoint_recorder(TurnRecorder())
        started = time.perf_counter()
        try:
            out = str(await tool.execute(action="delete", paths=["big"], recursive=True))
        finally:
            reset_checkpoint_recorder(token)
        elapsed = time.perf_counter() - started
        stop.set()
        await task
        return max(gaps), elapsed, out

    longest, elapsed, out = asyncio.run(scenario())
    assert "Deleted" in out, out
    assert not folder.exists()
    assert elapsed >= 0.6
    assert longest < 0.25, f"event loop froze for {longest:.2f}s"
