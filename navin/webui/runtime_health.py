# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Machine CPU, available memory and workspace disk space.

Probes never sleep to measure load. Unavailable metrics stay unknown.

psutil gives the precise numbers. A build where it did not load (a macOS
bundle once showed "CPU / RAM unavailable") still measures: memory from the
system (``/proc/meminfo``, or ``sysctl`` + ``vm_stat`` on macOS) and CPU from
the load average. Why psutil is missing goes to the log, once.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any

from loguru import logger

from navin.utils.proc import no_window_kwargs

_PSUTIL_ERROR = ""
try:
    import psutil
except ImportError as _exc:  # a frozen build that did not ship (or load) its native module
    psutil = None
    _PSUTIL_ERROR = f"{type(_exc).__name__}: {_exc}"
_psutil_reported = False


def _report_missing_psutil() -> None:
    global _psutil_reported
    if psutil is None and not _psutil_reported:
        _psutil_reported = True
        logger.warning("psutil unavailable ({}); CPU and memory come from the system instead", _PSUTIL_ERROR)


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=2, check=False, **no_window_kwargs()
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _memory_from_system() -> tuple[float | None, float | None]:
    """(used_ratio, available_gb) without psutil: Linux /proc, macOS sysctl + vm_stat."""
    total = available = 0
    if sys.platform.startswith("linux"):
        try:
            fields = dict(
                line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line
            )
            total = int(fields["MemTotal"].split()[0]) * 1024
            available = int(fields["MemAvailable"].split()[0]) * 1024
        except (OSError, KeyError, ValueError, IndexError):
            return None, None
    elif sys.platform == "darwin":
        try:
            total = int(_run(["sysctl", "-n", "hw.memsize"]).strip() or 0)
        except ValueError:
            return None, None
        stats = _run(["vm_stat"])
        page = re.search(r"page size of (\d+) bytes", stats)
        pages = {key.strip().lower(): int(value.strip().rstrip(".") or 0)
                 for key, value in re.findall(r"^([^:\n]+):\s+(\d+)\.?$", stats, re.M)}
        if not page or not pages:
            return None, None
        # psutil's macOS "available": free plus inactive pages.
        available = (pages.get("pages free", 0) + pages.get("pages inactive", 0)) * int(page.group(1))
    if total <= 0:
        return None, None
    return max(0.0, min(1.0, 1 - available / total)), available / 1024**3


def _cpu_from_load() -> float | None:
    """Load average over the cores: close to psutil's busy share, no sampling needed."""
    try:
        load = os.getloadavg()[0]
    except (AttributeError, OSError):
        return None
    cores = os.cpu_count() or 1
    return max(0.0, min(1.0, load / cores))


def _memory_snapshot() -> tuple[float | None, float | None]:
    """(used_ratio, available_gb), accounting for reclaimable OS caches."""
    if psutil is None:
        return _memory_from_system()
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
            if psutil is None:
                return self._from_load(now)
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


    def _from_load(self, now: float) -> tuple[float | None, bool]:
        self._previous = now, 0.0, 0.0
        self._ratio = _cpu_from_load()
        if self._ratio is not None and self._ratio >= 0.9:
            if self._high_since is None:
                self._high_since = now
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
    _report_missing_psutil()
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
