# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""How many agents this machine can actually hold at once.

The concurrency limit used to be a fixed number: the same 200 on a laptop and
on a 64-core server, and the same 200 inside a container capped at one core and
one gigabyte. A limit that ignores the machine is either a waste of a big host
or a way to swap a small one to death.

Agents are almost entirely network waits, so cores are not really what they
consume: a core can carry many of them, which is why the CPU term here is
deliberately generous, like the blocking pool's threads-per-core. Memory is the
term that actually bites, because each agent holds its own conversation and
context for as long as it runs.

Stdlib only, matching :mod:`navin.webui.runtime_health`: no psutil. Every probe
degrades to "unknown" instead of raising, and an unknown measurement means the
configured ceiling applies, which is exactly the old behaviour.

All three desktop platforms are measured, since a governor that only understands
Linux would leave Windows and macOS sized on their core count alone, which says
nothing about the term that actually runs out. Linux reads ``MemAvailable``,
Windows calls ``GlobalMemoryStatusEx`` through ctypes, and macOS falls back to
total RAM discounted by an assumed free share, having no cheap equivalent.

The container case is the one worth spelling out. ``os.cpu_count()`` reports the
host's cores from inside a container, and ``/proc/meminfo`` reports the host's
RAM, so both have to be crossed with the cgroup limits to get the numbers this
process is actually allowed to use.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Agents per usable core. High on purpose, for the same reason the blocking pool
# runs 16 threads per core: an agent waiting on the model API is not holding a
# core, so cores are a poor proxy for how many can run.
AGENTS_PER_CORE = 32
# What one running agent is assumed to cost in RAM: its conversation, its
# context window and the tool results it is carrying. A deliberate
# over-estimate, since being wrong low here means swapping.
MEMORY_PER_AGENT_MB = 96
# Share of the measured resources agents may take. The rest is for the gateway
# itself, the webui, the language servers and whatever else the user is running.
DEFAULT_RESERVE_RATIO = 0.70
# Never govern below this: a machine so loaded that the maths says zero still
# has to be able to run something, and a limit of zero would deadlock a turn
# that is waiting on a subagent.
MIN_AGENTS = 1
# Cores assumed when the count cannot be read, mirroring blocking_pool.
_FALLBACK_CORES = 4

_CGROUP_ROOT = Path("/sys/fs/cgroup")
# cgroup v1 writes a huge sentinel rather than omitting the limit. The exact
# value varies with the page size, so treat anything absurd as "no limit".
_NO_LIMIT_FLOOR = 1 << 62


def _read_first_line(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError, UnicodeDecodeError):
        return None


def _cgroup_cpu_quota(root: Path = _CGROUP_ROOT) -> float | None:
    """Cores this cgroup may use, or None when unlimited or unreadable."""
    # v2: a single "cpu.max" file holding "<quota> <period>", quota "max" when
    # unrestricted.
    raw = _read_first_line(root / "cpu.max")
    if raw:
        parts = raw.split()
        if parts and parts[0] != "max":
            try:
                quota = float(parts[0])
                period = float(parts[1]) if len(parts) > 1 else 100_000.0
                if quota > 0 and period > 0:
                    return quota / period
            except ValueError:
                pass
        if parts and parts[0] == "max":
            return None

    # v1: quota and period live in two files, quota -1 when unrestricted.
    quota_raw = _read_first_line(root / "cpu" / "cpu.cfs_quota_us")
    period_raw = _read_first_line(root / "cpu" / "cpu.cfs_period_us")
    if quota_raw and period_raw:
        try:
            quota = float(quota_raw)
            period = float(period_raw)
            if quota > 0 and period > 0:
                return quota / period
        except ValueError:
            return None
    return None


def _cgroup_memory_limit(root: Path = _CGROUP_ROOT) -> int | None:
    """Bytes this cgroup may use, or None when unlimited or unreadable."""
    raw = _read_first_line(root / "memory.max")  # v2
    if raw is None:
        raw = _read_first_line(root / "memory" / "memory.limit_in_bytes")  # v1
    if not raw or raw == "max":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0 or value >= _NO_LIMIT_FLOOR:
        return None
    return value


def _affinity_cores() -> int | None:
    """Cores this process is actually scheduled on (honours --cpuset-cpus)."""
    getaffinity = getattr(os, "sched_getaffinity", None)
    if getaffinity is None:  # Windows, macOS
        return None
    try:
        return len(getaffinity(0))
    except OSError:
        return None


def _meminfo_available_bytes() -> int | None:
    """MemAvailable from /proc/meminfo, in bytes. Linux only."""
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "MemAvailable:":
                return int(parts[1]) * 1024
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return None


def _windows_phys_bytes() -> tuple[int, int] | None:
    """(available, total) physical RAM via GlobalMemoryStatusEx. Windows only."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        available = int(status.ullAvailPhys)
        total = int(status.ullTotalPhys)
        if available < 0 or total <= 0:
            return None
        return available, total
    except Exception:  # noqa: BLE001 - a probe may never break a spawn
        return None


def _windows_available_bytes() -> int | None:
    """Free physical RAM via GlobalMemoryStatusEx. Windows only, no psutil."""
    pair = _windows_phys_bytes()
    if not pair:
        return None
    return pair[0] or None


def _sysconf_total_bytes() -> int | None:
    """Total physical RAM via sysconf. Used on macOS.

    Total, not free: macOS has no cheap equivalent of MemAvailable (the honest
    answer lives behind ``vm_stat``, a subprocess). Since this is the whole
    machine rather than what is spare, it is discounted before use, so a Mac
    with other apps open is not sized as if it were idle.
    """
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        return None
    if not pages or not page_size or pages < 0 or page_size < 0:
        return None
    return int(pages) * int(page_size)


# What share of a Mac's total RAM is assumed to be spare. Deliberately
# pessimistic: the alternative to guessing is not measuring at all, and
# over-estimating free memory is the one error that makes a machine swap.
_MACOS_ASSUMED_FREE_SHARE = 0.50


def _platform_available_bytes() -> int | None:
    """Spare RAM on this OS, or None when it cannot be known.

    Windows and macOS have no /proc, so an agent count sized on Linux
    measurements alone would leave both platforms governed by their core count
    only, which says nothing about the term that actually runs out.
    """
    if sys.platform == "win32":
        return _windows_available_bytes()
    available = _meminfo_available_bytes()  # Linux, WSL
    if available:
        return available
    if sys.platform == "darwin":
        total = _sysconf_total_bytes()
        if total:
            return int(total * _MACOS_ASSUMED_FREE_SHARE)
    return None


def usable_cores(*, cgroup_root: Path = _CGROUP_ROOT) -> int:
    """Cores this process may really use.

    The smallest of what the OS reports, what the scheduler allows and what the
    container was given. Any of the three can be the real wall.
    """
    try:
        return _usable_cores(cgroup_root)
    except Exception:  # noqa: BLE001 - the contract is "unknown", never a raise
        return _FALLBACK_CORES


def _usable_cores(cgroup_root: Path) -> int:
    candidates: list[float] = []
    reported = os.cpu_count()
    if reported and reported > 0:
        candidates.append(float(reported))
    affinity = _affinity_cores()
    if affinity and affinity > 0:
        candidates.append(float(affinity))
    quota = _cgroup_cpu_quota(cgroup_root)
    if quota and quota > 0:
        candidates.append(quota)
    if not candidates:
        return _FALLBACK_CORES
    # A 0.25-core container still gets one: it may run slowly, never nothing.
    return max(1, int(min(candidates)))


def usable_memory_bytes(*, cgroup_root: Path = _CGROUP_ROOT) -> int | None:
    """Bytes of RAM this process may really use, or None if unmeasurable.

    Crossed with the cgroup limit because /proc/meminfo is not namespaced: a
    container sees the host's free memory, not its own allowance.
    """
    try:
        return _usable_memory_bytes(cgroup_root)
    except Exception:  # noqa: BLE001 - unmeasurable is an answer, a raise is not
        return None


def _usable_memory_bytes(cgroup_root: Path) -> int | None:
    candidates: list[int] = []
    available = _platform_available_bytes()
    if available and available > 0:
        candidates.append(available)
    limit = _cgroup_memory_limit(cgroup_root)
    if limit and limit > 0:
        candidates.append(limit)
    if not candidates:
        return None
    return min(candidates)


def governed_agent_count(
    *,
    cores: int | None = None,
    available_bytes: int | None = None,
    reserve_ratio: float = DEFAULT_RESERVE_RATIO,
    agents_per_core: int = AGENTS_PER_CORE,
    memory_per_agent_mb: int = MEMORY_PER_AGENT_MB,
    ceiling: int,
) -> int:
    """How many agents fit, never above ``ceiling``.

    Both measurements are optional so this stays a pure function: pass them in
    from tests, leave them out to have them probed. An unmeasurable machine
    yields the ceiling, which is the behaviour from before there was a
    governor.
    """
    ceiling = max(MIN_AGENTS, int(ceiling))
    if cores is None:
        cores = usable_cores()
    if available_bytes is None:
        available_bytes = usable_memory_bytes()

    allowed = ceiling
    if cores and cores > 0 and agents_per_core > 0:
        allowed = min(allowed, cores * agents_per_core)
    if available_bytes and available_bytes > 0 and memory_per_agent_mb > 0:
        budget = available_bytes * max(0.0, reserve_ratio)
        allowed = min(allowed, int(budget // (memory_per_agent_mb * 1024 * 1024)))
    return max(MIN_AGENTS, min(ceiling, allowed))


def describe_capacity(
    *,
    cores: int | None = None,
    available_bytes: int | None = None,
) -> str:
    """One-line summary for the log when the governed limit changes."""
    if cores is None:
        cores = usable_cores()
    if available_bytes is None:
        available_bytes = usable_memory_bytes()
    mem = (
        f"{available_bytes / (1024 ** 3):.1f} GB available"
        if available_bytes
        else "memory unknown"
    )
    return f"{cores} usable core(s), {mem}"
