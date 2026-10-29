# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A runaway agent command is stopped before it fills the machine's memory.

A test that appended a list to itself took 16 GB in 40 seconds under WSL;
the VM went down with every terminal. Each command here is also capped with
``prlimit`` so a broken guard fails the test instead of the machine.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys

import pytest

from navin.agent.tools.shell import ExecTool
from navin.utils import memory_guard
from navin.utils.memory_guard import MemoryGuard

pytestmark = pytest.mark.skipif(not memory_guard.supported(), reason="reads /proc (Linux)")

# Grows about 50 MB every 50 ms and never stops on its own.
_RUNAWAY = (
    "import time\n"
    "chunks = []\n"
    "while True:\n"
    "    chunks.append(bytearray(50 * 1024 * 1024))\n"
    "    time.sleep(0.05)\n"
)
_SAFETY_CAP = "prlimit --as=3000000000 " if shutil.which("prlimit") else ""


def _runaway_command(tmp_path, *, own_group: bool) -> str:
    script = tmp_path / "runaway.py"
    script.write_text(_RUNAWAY, encoding="utf-8")
    # `timeout` moves its child into a process group of its own: the case the
    # guard must follow by parent links, not by group.
    wrapper = "timeout 60 " if own_group else ""
    return f"{_SAFETY_CAP}{wrapper}{sys.executable} {script}"


def test_runaway_command_is_stopped_with_a_clear_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NAVIN_EXEC_MEMORY_LIMIT_MB", "400")
    tool = ExecTool(timeout=60, working_dir=str(tmp_path))
    out = str(asyncio.run(tool.execute(
        command=_runaway_command(tmp_path, own_group=True), working_dir=str(tmp_path),
    )))
    assert "was stopped because it used" in out
    assert "limit for one command" in out
    assert "MemoryError" not in out  # the guard stopped it, not the safety cap


def test_normal_command_is_untouched_and_runs_at_low_priority(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NAVIN_EXEC_MEMORY_LIMIT_MB", "400")
    tool = ExecTool(timeout=30, working_dir=str(tmp_path))
    out = str(asyncio.run(tool.execute(command="echo nice=$(nice)", working_dir=str(tmp_path))))
    # setpriority gives the command an absolute niceness of 10, and a process
    # cannot raise its own priority: under a runner already at 10 or more (a
    # test run started by Navin's own exec) the command keeps the runner's.
    assert f"nice={min(19, max(os.nice(0), 10))}" in out
    assert "stopped" not in out


def test_tree_usage_follows_children_in_another_group() -> None:
    async def scenario() -> tuple[int, int, int]:
        process = await asyncio.create_subprocess_exec(
            "bash", "-c", f"setsid {sys.executable} -c 'import time; b = bytearray(80 * 1024 * 1024); time.sleep(5)' & wait",
            start_new_session=True,
        )
        try:
            await asyncio.sleep(1.0)
            used, pids = memory_guard.tree_usage(process.pid)
            return used, len(pids), process.pid
        finally:
            memory_guard._kill_tree(process.pid, memory_guard.tree_usage(process.pid)[1])
            await process.wait()

    used, count, _ = asyncio.run(scenario())
    assert count >= 2
    assert used >= 80 * 1024 * 1024


def test_machine_past_80_percent_stops_a_large_tree(monkeypatch) -> None:
    gib = 1024 ** 3
    monkeypatch.setattr(memory_guard, "_meminfo", lambda: (48 * gib, 8 * gib))
    monkeypatch.setattr(memory_guard, "tree_usage", lambda pid: (3 * gib, [pid]))
    killed: list[int] = []
    monkeypatch.setattr(memory_guard, "_kill_tree", lambda pid, pids: killed.append(pid))
    guard = MemoryGuard(pid=4242, limit=40 * gib)
    assert guard.check() is True
    assert killed == [4242]
    assert "80%" in guard.reason


def test_small_command_is_not_blamed_for_a_full_machine(monkeypatch) -> None:
    gib = 1024 ** 3
    monkeypatch.setattr(memory_guard, "_meminfo", lambda: (48 * gib, 2 * gib))
    monkeypatch.setattr(memory_guard, "tree_usage", lambda pid: (200 * 1024 * 1024, [pid]))
    monkeypatch.setattr(memory_guard, "_kill_tree", lambda pid, pids: pytest.fail("killed a small command"))
    assert MemoryGuard(pid=4242, limit=40 * gib).check() is False


def test_guard_can_be_turned_off(monkeypatch) -> None:
    monkeypatch.setenv("NAVIN_EXEC_MEMORY_LIMIT_MB", "0")
    assert memory_guard.default_limit() == 0


def test_default_cap_is_80_percent_of_the_machine(monkeypatch) -> None:
    monkeypatch.delenv("NAVIN_EXEC_MEMORY_LIMIT_MB", raising=False)
    monkeypatch.setattr(memory_guard, "_meminfo", lambda: (100, 50))
    assert memory_guard.default_limit() == 80
