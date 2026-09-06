"""Local runtime pressure (RAM / disk) for the WebUI toast strip.

Stdlib only - no psutil dependency. Best-effort: missing metrics are omitted
rather than failing the request.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any


def _meminfo_linux() -> tuple[float | None, float | None]:
    """Return (used_ratio, available_gb) from /proc/meminfo, or (None, None)."""
    try:
        data: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].endswith(":"):
                data[parts[0][:-1]] = int(parts[1])
        total = data.get("MemTotal")
        available = data.get("MemAvailable")
        if not total or available is None:
            return None, None
        used_ratio = max(0.0, min(1.0, 1.0 - (available / total)))
        return used_ratio, available / (1024 * 1024)
    except OSError:
        return None, None


def _windows_mem() -> tuple[float | None, float | None]:
    """Return (used_ratio, available_gb) via GlobalMemoryStatusEx."""
    if sys.platform != "win32":
        return None, None
    try:
        from navin.agent.resources import _windows_phys_bytes

        pair = _windows_phys_bytes()
    except Exception:
        return None, None
    if not pair:
        return None, None
    available, total = pair
    if total <= 0:
        return None, None
    used_ratio = max(0.0, min(1.0, 1.0 - (available / total)))
    return used_ratio, available / (1024 ** 3)


def _memory_snapshot() -> tuple[float | None, float | None]:
    """(used_ratio, available_gb) on Linux or Windows."""
    mem_ratio, mem_free_gb = _meminfo_linux()
    if mem_ratio is not None:
        return mem_ratio, mem_free_gb
    return _windows_mem()


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
    """Build a small health snapshot for the IDE toast / reload affordance."""
    mem_ratio, mem_free_gb = _memory_snapshot()
    disk_ratio, disk_free_gb = _disk_usage(workspace)

    pressure = False
    level = "ok"
    reasons: list[str] = []

    if mem_ratio is not None and mem_ratio >= 0.92:
        pressure = True
        level = "critical"
        reasons.append("memory")
    elif mem_ratio is not None and mem_ratio >= 0.85:
        pressure = True
        level = "warning" if level == "ok" else level
        reasons.append("memory")

    if disk_ratio is not None and disk_ratio >= 0.95:
        pressure = True
        level = "critical"
        if "disk" not in reasons:
            reasons.append("disk")
    elif disk_ratio is not None and disk_ratio >= 0.90:
        pressure = True
        level = "warning" if level == "ok" else level
        if "disk" not in reasons:
            reasons.append("disk")

    if disk_free_gb is not None and disk_free_gb < 1.0:
        pressure = True
        level = "critical"
        if "disk" not in reasons:
            reasons.append("disk")

    message = None
    label = None
    mem_pct = _pct(mem_ratio)
    disk_pct = _pct(disk_ratio)
    if pressure:
        if "memory" in reasons and "disk" in reasons:
            message = "Low memory and disk space - reload recommended"
            label = f"RAM {mem_pct}% · disk {disk_pct}%"
        elif "memory" in reasons:
            message = "High memory use - reload recommended"
            label = f"RAM {mem_pct}%"
        else:
            message = "Low disk space - free space or reload"
            label = f"Disk {disk_pct}%"

    return {
        "ok": not pressure or level == "warning",
        "pressure": pressure,
        "level": level,
        "message": message,
        "label": label,
        "reasons": reasons,
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
    }
