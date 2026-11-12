# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Machine-wide admission of heavy commands (navin/agent/host_jobs.py).

Twelve agents each starting a build at once swapped a 40 GB WSL VM to death. These tests hold the rules that
prevent it: a bounded number of builds, tests and installs on the whole machine, shared by every process, a
memory floor under which none starts, slots that a crash never leaks, and a clear refusal instead of a start.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from navin.agent import host_jobs


@pytest.fixture
def slots(tmp_path, monkeypatch):
    folder = tmp_path / "heavy-jobs"
    monkeypatch.setenv("NAVIN_HEAVY_JOBS_DIR", str(folder))
    monkeypatch.setenv("NAVIN_HEAVY_JOBS", "2")
    monkeypatch.setenv("NAVIN_HEAVY_JOB_WAIT_S", "1")
    monkeypatch.setenv("NAVIN_MIN_FREE_MEMORY_MB", "0")
    return folder


def _hold_in_other_process(folder: Path, index: int) -> subprocess.Popen[str]:
    """Another Navin process holding slot ``index`` (a lock the operating system drops when it dies)."""
    script = textwrap.dedent(f"""
        import os, time
        from navin.agent.host_jobs import _lock
        fd = os.open({str(folder / f"slot-{index}.lock")!r}, os.O_RDWR | os.O_CREAT, 0o600)
        assert _lock(fd)
        os.write(fd, b"1\\t1\\tnpm run build\\n")
        print("held", flush=True)
        time.sleep(60)
    """)
    folder.mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert child.stdout is not None and child.stdout.readline().strip() == "held"
    return child


@pytest.mark.parametrize("command", [
    "npm run build", "pnpm install", "npx vitest run src/a.test.ts", "python -m pytest -q tests/x.py",
    "cd webui && NODE_OPTIONS=--max-old-space-size=3072 npm run build", "cargo test", "go build ./...",
    "docker build -t x .", "uv sync", "pip install -e .", "make check", "./gradlew test", "tsc --noEmit -p .",
])
def test_builds_tests_and_installs_are_heavy(command):
    assert host_jobs.is_heavy(command)


@pytest.mark.parametrize("command", [
    "npm run dev", "vite --port 5180", "ls -la", "git status", "cat package.json", "pytest --watch",
    "npx vitest --watch", "kubectl port-forward svc/x 8080:80", "rg TODO navin",
])
def test_servers_watchers_and_plain_commands_are_never_held(command):
    assert not host_jobs.is_heavy(command)


def test_slots_are_shared_with_other_processes_and_freed_when_they_die(slots):
    child = _hold_in_other_process(slots, 0)
    try:
        mine = host_jobs.try_acquire("npm run build")
        assert mine is not None and mine.index == 1
        assert host_jobs.try_acquire("cargo build") is None, "both slots of the machine are taken"
        assert sorted(host_jobs.running()) == ["npm run build", "npm run build"]
        mine.release()
        again = host_jobs.try_acquire("cargo build")
        assert again is not None and again.index == 1
        again.release()
    finally:
        child.kill()
        child.wait()
    # The process died without releasing anything: its slot is free again.
    first = host_jobs.try_acquire("pytest")
    assert first is not None and first.index == 0
    first.release()


def test_no_heavy_command_starts_under_the_memory_floor(slots, monkeypatch):
    monkeypatch.setattr(host_jobs, "memory", lambda: (1 * 1024 ** 3, 40 * 1024 ** 3))
    monkeypatch.setenv("NAVIN_MIN_FREE_MEMORY_MB", "4096")
    assert host_jobs.try_acquire("npm run build") is None
    with pytest.raises(host_jobs.HostBusyError) as refused:
        asyncio.run(host_jobs.admit("npm run build", poll_s=0.05))
    message = str(refused.value)
    assert "'npm run build' was not started" in message
    assert "only 1.0 GB of memory is free" in message and "4.0 GB is kept free" in message
    assert asyncio.run(host_jobs.admit("ls -la")) is None, "a plain command is never held"


def test_default_budget_follows_cores_and_memory(monkeypatch):
    monkeypatch.delenv("NAVIN_HEAVY_JOBS", raising=False)
    monkeypatch.delenv("NAVIN_HEAVY_JOB_MEMORY_MB", raising=False)
    monkeypatch.delenv("NAVIN_MIN_FREE_MEMORY_MB", raising=False)
    monkeypatch.setattr("navin.agent.resources.usable_cores", lambda: 6)
    monkeypatch.setattr(host_jobs, "memory", lambda: (26 * 1024 ** 3, 40 * 1024 ** 3))
    assert host_jobs.slot_count() == 3, "half of 6 cores; 40 GB would allow 10 jobs of 2 GB"
    monkeypatch.setattr(host_jobs, "memory", lambda: (3 * 1024 ** 3, 8 * 1024 ** 3))
    assert host_jobs.slot_count() == 2, "8 GB: half of it holds two 2 GB builds"
    assert host_jobs.memory_floor_bytes(40 * 1024 ** 3) == int(40 * 1024 ** 3 * 0.15)
    monkeypatch.setenv("NAVIN_HEAVY_JOBS", "0")
    assert host_jobs.slot_count() == 0
    assert asyncio.run(host_jobs.admit("npm run build")) is None, "0 turns the admission off"


def test_exec_waits_then_refuses_and_holds_the_slot_while_the_command_runs(slots, tmp_path, monkeypatch):
    from navin.agent.tools.exec_session import ExecSessionManager
    from navin.agent.tools.shell import ExecTool

    monkeypatch.setenv("NAVIN_HEAVY_JOBS", "1")
    project = tmp_path / "project"
    project.mkdir()
    # A manager of its own: the background session of this test must not stay visible to later tests.
    tool = ExecTool(working_dir=str(project), session_manager=ExecSessionManager())
    # "-m pytest" makes it a heavy command; the shell is PowerShell on Windows, bash elsewhere.
    if os.name == "nt":
        python = f'& "{sys.executable}"'
        command = f"{python} -m pytest --version | Out-Null; Set-Content -Path out.txt -Value built"
        background = f"{python} -m pytest --version | Out-Null; Start-Sleep -Seconds 2"
    else:
        python = shlex.quote(sys.executable)
        command = f"{python} -m pytest --version >/dev/null 2>&1; echo built > out.txt"
        background = f"{python} -m pytest --version >/dev/null 2>&1; sleep 2"
    assert host_jobs.is_heavy(command) and host_jobs.is_heavy(background)
    child = _hold_in_other_process(slots, 0)
    try:
        refused = asyncio.run(tool.execute(command=command))
    finally:
        child.kill()
        child.wait()
    assert "was not started after waiting" in str(refused)
    assert "1 builds, tests or installs already run on this machine (npm run build)" in str(refused)
    assert not (project / "out.txt").exists()

    async def foreground_then_background():
        done = await tool.execute(command=command)
        assert "Exit code: 0" in str(done) and (project / "out.txt").read_text().strip() == "built"
        assert host_jobs.running() == [], "the slot was released when the command ended"
        session = await tool.execute(command=background, background=True)
        assert "session" in str(session).lower()
        assert host_jobs.try_acquire("npm run build") is None, "a background build keeps its slot"
        for _ in range(100):
            await asyncio.sleep(0.1)
            free = host_jobs.try_acquire("npm run build")
            if free is not None:
                free.release()
                return
        raise AssertionError("the slot of the finished background build was never released")

    asyncio.run(foreground_then_background())


def test_a_test_run_is_stopped_before_it_exhausts_the_machine(monkeypatch):
    from navin.quality.testing import _run_streaming
    from navin.utils import memory_guard

    monkeypatch.setenv("NAVIN_EXEC_MEMORY_LIMIT_MB", "1")
    argv = [sys.executable, "-c", "import time; time.sleep(4)"]
    started = time.monotonic()
    if memory_guard.supported():
        with pytest.raises(MemoryError) as stopped:
            _run_streaming(argv, on_output=lambda _text: None, timeout=60, cwd=os.getcwd())
        assert time.monotonic() - started < 4, "stopped by the guard, not at the end of the run"
        assert "over the 0.0 GB limit for one command" in str(stopped.value)
    else:
        # The guard reads /proc (Linux, WSL): elsewhere the run is not watched and ends normally.
        completed = _run_streaming(argv, on_output=lambda _text: None, timeout=60, cwd=os.getcwd())
        assert completed.returncode == 0
