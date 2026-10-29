# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Stop an agent command before it eats the machine's memory.

A test stuck in a loop that grows a list (``for x in items: items.append(x)``)
took 16 GB in 40 seconds, about 430 MB a second. Under WSL the whole VM shares
one memory budget: once it is full Windows stops the VM, and every terminal and
every Navin session goes with it, not just the command. Worse, the next start
resumed the interrupted turn, which ran the same test and crashed again.

:class:`MemoryGuard` samples the resident memory of a command's whole process
tree once a second and kills the tree when it crosses either line:

- the machine is more than 80% full and the tree holds a real share of it
  (1 GB or more, so a small command is never blamed for a browser's memory);
- the tree alone holds more than 80% of the machine
  (``NAVIN_EXEC_MEMORY_LIMIT_MB`` changes that cap, ``0`` turns the guard off).

The tree is followed by parent links, not by process group: ``timeout`` and some
test runners move their child into a group of its own, which is exactly where
the runaway was. Linux only (it reads ``/proc``); elsewhere the guard does
nothing.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import suppress
from dataclasses import dataclass, field

_GIB = 1024 * 1024 * 1024
_MIB = 1024 * 1024
_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

# The machine is never let past this share in use by an agent command. Three
# sessions each running a 15 GB job fill a 48 GB VM without any one of them
# crossing a per-command cap: the line is on the machine, not on the command.
_MAX_USED_SHARE = 0.80
_LOW_MEMORY_TREE_MIN = 1 * _GIB
_INTERVAL_S = 1.0
_ATTRIBUTE = "_navin_memory_guard"


def supported() -> bool:
    return sys.platform.startswith("linux") and os.path.isdir("/proc")


def _meminfo() -> tuple[int, int]:
    """(total, available) bytes of the machine, (0, 0) when unreadable."""
    total = available = 0
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    available = int(line.split()[1]) * 1024
                if total and available:
                    break
    except (OSError, ValueError, IndexError):
        return 0, 0
    return total, available


def default_limit() -> int:
    """The per-command cap in bytes; 0 means the guard is off."""
    raw = os.environ.get("NAVIN_EXEC_MEMORY_LIMIT_MB", "").strip()
    if raw:
        with suppress(ValueError):
            return max(0, int(raw)) * _MIB
    total, _ = _meminfo()
    return int(total * _MAX_USED_SHARE) if total else 0


def _process_table() -> dict[int, tuple[int, int]]:
    """pid -> (ppid, rss bytes) for every process we can read."""
    table: dict[int, tuple[int, int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return table
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="ascii", errors="replace") as handle:
                stat = handle.read()
        except OSError:
            continue
        # The command name sits in parentheses and may hold spaces.
        fields = stat[stat.rfind(")") + 2:].split()
        try:
            table[int(entry)] = (int(fields[1]), int(fields[21]) * _PAGE)
        except (IndexError, ValueError):
            continue
    return table


def tree_usage(root_pid: int) -> tuple[int, list[int]]:
    """Resident bytes of ``root_pid`` and all its descendants, and their pids."""
    table = _process_table()
    if root_pid not in table:
        return 0, []
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    pids: list[int] = []
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        pids.append(pid)
        stack.extend(children.get(pid, ()))
    return sum(table[pid][1] for pid in pids), pids


def _kill_tree(root_pid: int, pids: list[int]) -> None:
    with suppress(OSError):
        os.killpg(root_pid, signal.SIGKILL)
    for pid in pids:
        with suppress(OSError):
            os.kill(pid, signal.SIGKILL)


_NICENESS = 10


def lower_priority(pid: int) -> None:
    """Run a command below the user's own work on the CPU.

    A build or a parallel test run at 100% CPU is normal and must not be
    killed, but it must not freeze the terminal or the desktop either: at
    niceness 10 the scheduler serves them first. Set right after the spawn,
    so what the shell starts next inherits it. ``NAVIN_EXEC_NICE=0`` keeps
    the normal priority.
    """
    raw = os.environ.get("NAVIN_EXEC_NICE", "").strip()
    niceness = _NICENESS
    if raw:
        with suppress(ValueError):
            niceness = max(0, min(19, int(raw)))
    if niceness <= 0 or not hasattr(os, "setpriority"):
        return
    with suppress(OSError):
        os.setpriority(os.PRIO_PROCESS, pid, niceness)


@dataclass
class MemoryGuard:
    """Watches one command; ``reason`` is set once it had to stop it."""

    pid: int
    limit: int = field(default_factory=default_limit)
    reason: str = ""
    gone: bool = False
    _task: asyncio.Task[None] | None = None

    def start(self) -> MemoryGuard:
        if self.limit > 0 and supported():
            self._task = asyncio.create_task(self._run(), name="navin-exec-memory-guard")
        return self

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def check(self) -> bool:
        """One sample; True when the tree was over a line and has been killed."""
        used, pids = tree_usage(self.pid)
        if not pids:
            self.gone = True
            return False
        total, available = _meminfo()
        if used > self.limit:
            self.reason = (
                f"used {used / _GIB:.1f} GB of memory, over the {self.limit / _GIB:.1f} GB "
                "limit for one command"
            )
        elif (
            total
            and total - available > total * _MAX_USED_SHARE
            and used >= _LOW_MEMORY_TREE_MIN
        ):
            self.reason = (
                f"used {used / _GIB:.1f} GB while the machine passed "
                f"{_MAX_USED_SHARE:.0%} of its memory in use"
            )
        else:
            return False
        _kill_tree(self.pid, pids)
        return True

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(_INTERVAL_S)
            if await asyncio.to_thread(self.check) or self.gone:
                return

    @classmethod
    def watch(cls, process: object) -> MemoryGuard:
        """Guard a just-spawned process for its whole life, foreground or not."""
        pid = int(getattr(process, "pid"))
        lower_priority(pid)
        guard = cls(pid).start()
        with suppress(AttributeError):
            setattr(process, _ATTRIBUTE, guard)
        return guard

    @staticmethod
    def of(process: object) -> MemoryGuard | None:
        return getattr(process, _ATTRIBUTE, None)

    def message(self) -> str:
        return (
            f"Error: the command was stopped because it {self.reason}. It was probably "
            "stuck in a loop that keeps allocating; left running it would have "
            "exhausted the machine's memory. Find the cause before running it again "
            "(run a single test, or cap it with `prlimit --as=<bytes>`)."
        )
