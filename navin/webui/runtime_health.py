# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Machine CPU, available memory and workspace disk space.

Probes never sleep to measure load. Unavailable metrics stay unknown.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any

import psutil


def _memory_snapshot() -> tuple[float | None, float | None]:
    """(used_ratio, available_gb), accounting for reclaimable OS caches."""
    try:
        memory = psutil.virtual_memory()
        if memory.total <= 0:
            return None, None
        return max(0.0, min(1.0, 1 - memory.available / memory.total)), memory.available / 1024**3
    except (OSError, ValueError, psutil.Error):
        return None, None


class _CpuSampler:
    """Share one sampling window across HTTP workers and multiple clients."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._previous: tuple[float, float, float] | None = None
        self._ratio: float | None = None
        self._high_since: float | None = None

    def snapshot(self) -> tuple[float | None, bool]:
        with self._lock:
            now = time.monotonic()
            previous = self._previous
            if previous is not None and now - previous[0] < 1:
                return self._ratio, self._high_since is not None and now - self._high_since >= 30
            try:
                counters = psutil.cpu_times()
            except (OSError, ValueError, psutil.Error):
                self._previous = None
                self._ratio = None
                self._high_since = None
                return None, False
            # Linux includes guest time in user/nice already; I/O wait is idle.
            total = sum(counters) - getattr(counters, "guest", 0) - getattr(counters, "guest_nice", 0)
            idle = counters.idle + getattr(counters, "iowait", 0)
            self._previous = now, total, idle
            if previous is None or now - previous[0] > 120 or total <= previous[1] or idle < previous[2]:
                self._ratio = None
                self._high_since = None
                return None, False
            self._ratio = max(0.0, min(1.0, 1 - (idle - previous[2]) / (total - previous[1])))
            if self._ratio >= 0.9:
                if self._high_since is None:
                    self._high_since = previous[0]
            else:
                self._high_since = None
            return self._ratio, self._high_since is not None and now - self._high_since >= 30


_cpu_sampler = _CpuSampler()


def _disk_usage(path: str | None = None) -> tuple[float | None, float | None]:
    """Return (used_ratio, free_gb) for the workspace or home disk."""
    root = path or str(Path.home())
    try:
        usage = shutil.disk_usage(root)
        if usage.total <= 0:
            return None, None
        used_ratio = usage.used / usage.total
        return used_ratio, usage.free / (1024**3)
    except OSError:
        return None, None


def _pct(ratio: float | None) -> int | None:
    if ratio is None:
        return None
    return int(round(max(0.0, min(1.0, ratio)) * 100))


def runtime_health_payload(*, workspace: str | None = None) -> dict[str, Any]:
    """Build a small health snapshot for the IDE status bar and resource toast."""
    mem_ratio, mem_free_gb = _memory_snapshot()
    disk_ratio, disk_free_gb = _disk_usage(workspace)
    cpu_ratio, cpu_pressure = _cpu_sampler.snapshot()

    pressure = False
    level = "ok"
    reasons: list[str] = []

    if (mem_free_gb is not None and mem_free_gb < 0.5) or (
        mem_ratio is not None and mem_ratio >= 0.92 and (mem_free_gb is None or mem_free_gb < 1)
    ):
        pressure = True
        level = "critical"
        reasons.append("memory")
    elif mem_ratio is not None and mem_ratio >= 0.85 and (mem_free_gb is None or mem_free_gb < 2):
        pressure = True
        level = "warning" if level == "ok" else level
        reasons.append("memory")

    if disk_ratio is not None and disk_ratio >= 0.95 and (disk_free_gb is None or disk_free_gb < 5):
        pressure = True
        level = "critical"
        if "disk" not in reasons:
            reasons.append("disk")
    elif disk_ratio is not None and disk_ratio >= 0.90 and (disk_free_gb is None or disk_free_gb < 10):
        pressure = True
        level = "warning" if level == "ok" else level
        if "disk" not in reasons:
            reasons.append("disk")

    if disk_free_gb is not None and disk_free_gb < 1.0:
        pressure = True
        level = "critical"
        if "disk" not in reasons:
            reasons.append("disk")

    if cpu_pressure:
        pressure = True
        if level == "ok":
            level = "warning"
        reasons.append("cpu")

    message = None
    label = None
    mem_pct = _pct(mem_ratio)
    disk_pct = _pct(disk_ratio)
    if pressure:
        message = "High machine resource use" if len(reasons) > 1 else {
            "memory": "Low available machine memory",
            "disk": "Low workspace disk space",
            "cpu": "Sustained high machine CPU use",
        }[reasons[0]]
        label = " · ".join(
            f"{name} {pct}%" for key, name, pct in (
                ("memory", "RAM", mem_pct), ("disk", "Disk", disk_pct), ("cpu", "CPU", _pct(cpu_ratio)),
            ) if key in reasons and pct is not None
        )

    return {
        "ok": not pressure or level == "warning",
        "pressure": pressure,
        "level": level,
        "message": message,
        "label": label,
        "reasons": reasons,
        "scope": "machine",
        "cpu": {"usedRatio": cpu_ratio},
        "memory": {
            "usedRatio": mem_ratio,
            "availableGb": mem_free_gb,
        },
        "disk": {
            "usedRatio": disk_ratio,
            "freeGb": disk_free_gb,
            "path": workspace or str(Path.home()),
        },
        "pid": os.getpid(),
        "engine": _engine_identity(),
    }


def _engine_identity() -> dict[str, str]:
    """Which navin build is actually serving this endpoint.

    Desktop shells attach to any already-listening gateway, so a stale CLI or
    an orphaned older sidecar can serve a freshly installed app. Exposing the
    version and the running binary makes that mismatch diagnosable from the
    WebUI instead of looking like random chat/session bugs.
    """
    from navin import __version__ as navin_version

    return {
        "version": navin_version,
        "executable": sys.executable,
    }
