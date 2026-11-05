# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One out-of-memory kill must not stop every Navin session at once.

On 2026-11-05 a full WSL VM made the kernel kill one browser process; systemd
then stopped ``init.scope``, where WSL keeps every terminal, and eight
navin-cli with it. These tests cover the scope a CLI or a gateway moves to,
the refusals that keep it where it is, the glibc memory tuning and the
doctor lines that name the fix.
"""

from __future__ import annotations

import os
import subprocess
import time
from types import SimpleNamespace
from typing import Any

import pytest

from navin import diagnostics
from navin.utils import native_memory, wsl_guard
from navin.utils import process_isolation as iso

_MB = 1024 * 1024
_MOVED = "/user.slice/user-1000.slice/user@1000.service/app.slice/{unit}"


class _Busctl:
    """Stands in for ``busctl``: records the call, answers like systemd."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        return subprocess.CompletedProcess(command, self.returncode, stdout="o \"/job/1\"\n", stderr=self.stderr)


@pytest.fixture
def systemd_host(monkeypatch: pytest.MonkeyPatch) -> _Busctl:
    """A Linux host with a lasting user manager; nothing reaches the real systemd."""
    busctl = _Busctl()
    monkeypatch.setattr(iso, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(iso.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(iso, "_user_bus_reachable", lambda: True)
    monkeypatch.setattr(iso, "user_manager_persists", lambda: True)
    monkeypatch.setattr(iso.subprocess, "run", busctl)
    monkeypatch.delenv("NAVIN_PROCESS_SCOPE", raising=False)
    return busctl


def test_scope_command_carries_the_pids_and_the_properties() -> None:
    command = iso.start_transient_scope_command(
        "busctl",
        "navin-x.scope",
        [("PIDs", "au", ("2", "10", "11")), ("OOMPolicy", "s", ("continue",)), ("MemoryMax", "t", ("1024",))],
    )

    assert command[:4] == ["busctl", "--user", "--timeout=5", "call"]
    assert command[7] == "StartTransientUnit"
    assert command[8:] == [
        "ssa(sv)a(sa(sv))", "navin-x.scope", "fail", "3",
        "PIDs", "au", "2", "10", "11",
        "OOMPolicy", "s", "continue",
        "MemoryMax", "t", "1024",
        "0",
    ]


def test_a_capped_scope_is_created_and_the_process_moves(
    systemd_host: _Busctl, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(iso, "cgroup_of", lambda pid="self": _MOVED.format(unit="navin-browser-1-1.scope"))

    result = iso.start_scope(
        "navin-browser-1-1", [70, 77], description="Navin browser", memory_max=512 * _MB, memory_swap_max=256 * _MB,
    )

    assert result == iso.ScopeResult(unit="navin-browser-1-1.scope")
    assert result.moved
    (command,) = systemd_host.calls
    assert command[9] == "navin-browser-1-1.scope"
    tail = command[11:]
    assert tail[:5] == ["6", "PIDs", "au", "2", "70"]
    assert ["OOMPolicy", "s", "continue"] == tail[6:9]
    assert ["MemoryMax", "t", str(512 * _MB)] == tail[-7:-4]
    assert ["MemorySwapMax", "t", str(256 * _MB)] == tail[-4:-1]


def test_a_refusal_keeps_the_process_where_it_was(systemd_host: _Busctl) -> None:
    systemd_host.returncode = 1
    systemd_host.stderr = "Call failed: Access denied"

    result = iso.start_scope("navin-cli-1", [1], description="Navin cli")

    assert not result.moved
    assert result.reason == "systemd refused the scope: Call failed: Access denied"


def test_no_move_without_a_lasting_user_manager(systemd_host: _Busctl, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iso, "cgroup_of", lambda pid="self": iso.INIT_SCOPE)
    monkeypatch.setattr(iso, "user_manager_persists", lambda: False)
    assert "lingering is off" in iso.isolate_current_process("cli").reason
    assert systemd_host.calls == []

    monkeypatch.setattr(iso, "user_manager_persists", lambda: True)
    monkeypatch.setenv("NAVIN_PROCESS_SCOPE", "0")
    assert iso.start_scope("navin-cli-1", [1], description="x").reason == "turned off (NAVIN_PROCESS_SCOPE=0)"
    assert systemd_host.calls == []


def test_a_browser_scope_does_not_need_lingering(systemd_host: _Busctl, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iso, "user_manager_persists", lambda: False)
    monkeypatch.setattr(iso, "cgroup_of", lambda pid="self": _MOVED.format(unit="navin-browser-1-2.scope"))

    result = iso.start_scope("navin-browser-1-2", [70], description="Navin browser")

    assert result.moved
    assert len(systemd_host.calls) == 1


def test_only_init_scope_is_left(systemd_host: _Busctl, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iso, "cgroup_of", lambda pid="self": "/user.slice/user-1000.slice/session-1.scope")
    result = iso.isolate_current_process("cli")
    assert result.reason == "already in /user.slice/user-1000.slice/session-1.scope"
    assert systemd_host.calls == []

    unit = f"navin-cli-{os.getpid()}.scope"
    monkeypatch.setattr(iso, "cgroup_of", lambda pid="self": iso.INIT_SCOPE if pid == "self" else _MOVED.format(unit=unit))
    result = iso.isolate_current_process("cli")
    assert result == iso.ScopeResult(unit=unit)
    assert iso.isolation_message(result) == f"moved to {unit} (OOMPolicy=continue)"


def test_prepare_process_reports_where_it_ended_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iso, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(iso, "isolate_current_process", lambda role: iso.ScopeResult(reason=f"{role} stays"))
    monkeypatch.setattr(native_memory, "tune_malloc", lambda: "glibc arenas capped at 2")
    monkeypatch.setattr(native_memory, "start_trimmer", lambda: None)
    guarded: list[str] = []
    monkeypatch.setattr(wsl_guard, "start_in_background", guarded.append)
    seen: list[str] = []

    thread = iso.prepare_process("cli", report=seen.append)

    assert thread is not None
    thread.join(5)
    assert seen == ["not isolated: cli stays; memory: glibc arenas capped at 2"]
    assert guarded == ["cli"]


def test_malloc_tuning_leaves_the_user_s_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MALLOC_ARENA_MAX", "4")
    assert native_memory.tune_malloc() == "MALLOC_ARENA_MAX=4 kept"

    monkeypatch.delenv("MALLOC_ARENA_MAX")
    monkeypatch.setenv("NAVIN_MALLOC_TUNING", "0")
    assert native_memory.tune_malloc() == "tuning turned off (NAVIN_MALLOC_TUNING=0)"
    assert native_memory.trim() is False
    assert native_memory.start_trimmer() is None


def test_malloc_tuning_on_this_c_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    monkeypatch.delenv("NAVIN_MALLOC_TUNING", raising=False)

    result = native_memory.tune_malloc(4)

    expected = "not glibc, left as is" if native_memory._glibc() is None else "glibc arenas capped at 4"
    assert result == expected
    assert isinstance(native_memory.trim(), bool)


def test_the_trimmer_trims_after_growth_and_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NAVIN_MALLOC_TUNING", raising=False)
    monkeypatch.setattr(native_memory, "_trimmer", None)
    monkeypatch.setattr(native_memory, "_glibc", lambda: object())
    trims: list[int] = []
    monkeypatch.setattr(native_memory, "trim", lambda: trims.append(1) is None)
    sizes = [100 * _MB, 170 * _MB, 110 * _MB]
    monkeypatch.setattr(native_memory, "resident_bytes", lambda: sizes.pop(0) if len(sizes) > 1 else sizes[0])

    stop = native_memory.start_trimmer(interval=0.01, growth=64 * _MB)
    try:
        assert stop is not None
        assert native_memory.start_trimmer() is None
        deadline = time.monotonic() + 5
        while not trims and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        if stop is not None:
            stop.set()
        thread = native_memory._trimmer
        if thread is not None:
            thread.join(5)
    assert trims == [1]


def test_doctor_names_the_wsl_fixes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iso, "wsl_oom_policy", lambda: "stop")
    monkeypatch.setattr(iso, "user_manager_persists", lambda: False)

    checks = {check.name: check for check in diagnostics.memory_checks()}

    assert "GB free of" in checks["memory"].detail
    assert not checks["wsl oom policy"].ok
    assert "stops every terminal" in checks["wsl oom policy"].detail
    assert iso.WSL_OOM_FIX in checks["wsl oom policy"].hint
    assert "navin doctor --install" in checks["wsl oom policy"].hint
    # Lingering is optional: an information line, never a MISS.
    assert checks["process isolation"].ok
    assert iso.LINGER_FIX in checks["process isolation"].detail

    monkeypatch.setattr(iso, "wsl_oom_policy", lambda: "continue")
    monkeypatch.setattr(iso, "user_manager_persists", lambda: True)
    checks = {check.name: check for check in diagnostics.memory_checks()}
    assert checks["wsl oom policy"].ok and checks["process isolation"].ok

    monkeypatch.setattr(iso, "wsl_oom_policy", lambda: None)
    assert [check.name for check in diagnostics.memory_checks()] == ["memory"]
