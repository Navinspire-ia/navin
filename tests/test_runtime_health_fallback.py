# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""CPU and memory still show when psutil did not load (a macOS build once said "Unavailable")."""

from __future__ import annotations

import sys

from navin.webui import runtime_health as rh

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               100000.
Pages active:                             300000.
Pages inactive:                           150000.
Pages speculative:                         20000.
Pages wired down:                         200000.
"""


def test_memory_and_cpu_without_psutil_on_linux(monkeypatch):
    if not sys.platform.startswith("linux"):
        return
    monkeypatch.setattr(rh, "psutil", None)
    ratio, free_gb = rh._memory_snapshot()
    assert ratio is not None and 0 <= ratio <= 1 and free_gb and free_gb > 0
    cpu, _pressure = rh._CpuSampler().snapshot()
    assert cpu is not None and 0 <= cpu <= 1
    payload = rh.runtime_health_payload()
    assert payload  # the widget gets numbers, not "Unavailable"


def test_memory_without_psutil_on_macos(monkeypatch):
    monkeypatch.setattr(rh, "psutil", None)
    monkeypatch.setattr(rh.sys, "platform", "darwin")
    total = 16 * 1024**3
    outputs = {"sysctl": f"{total}\n", "vm_stat": VM_STAT}
    monkeypatch.setattr(rh, "_run", lambda command: outputs[command[0]])
    ratio, free_gb = rh._memory_snapshot()
    available = (100000 + 150000) * 16384
    assert round(free_gb, 3) == round(available / 1024**3, 3)
    assert round(ratio, 4) == round(1 - available / total, 4)
