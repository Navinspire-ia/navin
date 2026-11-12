# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Machine-wide admission for heavy commands: builds, test runs, installs, image builds.

Ten to twelve agents each starting a build or a test suite at the same time took a 40 GB WSL VM past its
memory: the kernel swapped to its virtual swap disk until Windows took the disk offline ("sd 0:0:0:2: Device
offlined", "Write-error on swap-device", journal of 2026-11-11 15:45:48) and the whole VM died with every
terminal and every Navin session in it. The per-command guard (navin.utils.memory_guard) stops one runaway; it
cannot stop twelve legitimate builds of 1 to 3 GB each, nor what they start in other processes.

Two rules, shared by every Navin process of the machine (CLI, gateway, desktop, each subagent):

* **Slots**: at most ``NAVIN_HEAVY_JOBS`` heavy commands run at once (default: half the usable cores, never
  more than half the memory allows at ``NAVIN_HEAVY_JOB_MEMORY_MB`` each, at least 1). A slot is a lock on a
  file under ``<data dir>/run/heavy-jobs``: it is released by the operating system when its process dies, so a
  crash never leaks one.
* **Memory floor**: a heavy command does not start while available memory is under
  ``NAVIN_MIN_FREE_MEMORY_MB`` (default: 15% of the machine, at least one heavy job).

A heavy command that cannot be admitted waits, and says so, up to ``NAVIN_HEAVY_JOB_WAIT_S`` seconds (default
900), then fails with the reason instead of starting. Servers, watchers and every other command are never held.
``NAVIN_HEAVY_JOBS=0`` turns the admission off.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import re
import shlex
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MIB = 1024 * 1024
# Windows locks a byte range: the slot lock sits past the label, which other processes still read.
_WINDOWS_LOCK_OFFSET = 1 << 20

# Programs and subcommands that compile, bundle, test or install: what fills a machine when ten agents run them.
HEAVY_RE = re.compile(
    r"(?:^|[\s;&|(`])(?:"
    r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:build|test|ci|install|i|add|lint|typecheck|check|e2e|coverage)\b"
    r"|npx\s+(?:--yes\s+|-y\s+)?(?:vite\s+build|tsc|vitest|jest|webpack|next\s+build|playwright\s+test|eslint)\b"
    r"|vite\s+build\b|tsc\b|vitest\b|jest\b|webpack\b|next\s+build\b|playwright\s+test\b|eslint\b"
    r"|(?:python\d*(?:\.\d+)?\s+-m\s+)?pytest\b|tox\b|nox\b|mypy\b|pyright\b"
    r"|cargo\s+(?:build|test|check|clippy|bench|install)\b"
    r"|go\s+(?:build|test|vet|install)\b"
    r"|(?:\./)?(?:mvnw?|gradlew?)\b"
    r"|dotnet\s+(?:build|test|publish|restore)\b"
    r"|make\b|cmake\s+--build\b|ninja\b"
    r"|docker\s+(?:build|buildx\s+build|compose\s+(?:[^;&|]*\s)?(?:build|up))\b"
    r"|uv\s+(?:sync|pip\s+install)\b|(?:pip3?|python\d*\s+-m\s+pip)\s+install\b|poetry\s+install\b"
    r"|kind\s+create\s+cluster\b"
    r")",
    re.IGNORECASE,
)
# Servers and watchers live for hours: holding a slot that long would starve every build of the machine.
LONG_RUNNING_RE = re.compile(
    r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve|watch|preview)\b"
    r"|\b(?:next|nuxt|astro|remix|ng)\s+(?:dev|serve|start)\b|\bvite\b(?!\s+build)"
    r"|--watch\b|\bwatchexec\b|\bnodemon\b|\bcargo\s+watch\b|port-forward",
    re.IGNORECASE,
)


def is_heavy(command: str) -> bool:
    """True for a build, test, install or image build; False for servers, watchers and everything else."""
    text = command or ""
    return bool(HEAVY_RE.search(text)) and not LONG_RUNNING_RE.search(text)


def label(command: str) -> str:
    """A short name for a heavy command (``npm run build``, ``pytest``), never its arguments."""
    match = HEAVY_RE.search(command or "")
    words = (match.group(0) if match else command or "").strip(" ;&|(`").split()
    return " ".join(words[:3])[:40] or "command"


# -- measurements ---------------------------------------------------------------------------------------------------

def memory() -> tuple[int, int]:
    """(available, total) bytes usable by this process, (0, 0) when it cannot be measured."""
    total = available = 0
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    available = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        from navin.agent.resources import usable_memory_bytes

        return usable_memory_bytes() or 0, 0
    from navin.agent.resources import _cgroup_memory_limit

    limit = _cgroup_memory_limit()
    if limit:
        total = min(total, limit) if total else limit
        available = min(available, limit)
    return available, total


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw:
        with suppress(ValueError):
            return max(0, int(raw))
    return default


def job_memory_bytes() -> int:
    return max(256, _env_int("NAVIN_HEAVY_JOB_MEMORY_MB", 2048)) * _MIB


def slot_count() -> int:
    """How many heavy commands may run at once on this machine; 0 turns the admission off."""
    raw = os.environ.get("NAVIN_HEAVY_JOBS", "").strip()
    if raw:
        with suppress(ValueError):
            return max(0, min(64, int(raw)))
    from navin.agent.resources import usable_cores

    by_cores = max(1, usable_cores() // 2)
    _available, total = memory()
    if not total:
        return by_cores
    by_memory = max(1, int(total * 0.5) // job_memory_bytes())
    return max(1, min(by_cores, by_memory))


def memory_floor_bytes(total: int) -> int:
    configured = _env_int("NAVIN_MIN_FREE_MEMORY_MB", -1)
    if configured >= 0:
        return configured * _MIB
    return max(job_memory_bytes(), int(total * 0.15))


def max_wait_s() -> float:
    return float(max(1, _env_int("NAVIN_HEAVY_JOB_WAIT_S", 900)))


def slot_dir() -> Path:
    override = os.environ.get("NAVIN_HEAVY_JOBS_DIR", "").strip()
    if override:
        folder = Path(override)
    else:
        from navin.config.paths import get_runtime_subdir

        folder = get_runtime_subdir("run") / "heavy-jobs"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


# -- slots ----------------------------------------------------------------------------------------------------------

class HostBusyError(RuntimeError):
    """A heavy command was not started: no slot or not enough memory within the allowed wait."""


def _lock(fd: int) -> bool:
    try:
        import fcntl
    except ImportError:  # Windows
        import msvcrt

        # A byte past the content, so the label written at the start of the slot stays readable by others.
        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    try:
        import fcntl
    except ImportError:  # Windows: a lock must be released before its handle is closed
        import msvcrt

        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(fd, fcntl.LOCK_UN)


@dataclass
class Slot:
    """One admitted heavy command; released when its process ends, or by :meth:`release`."""

    index: int
    fd: int
    command: str
    waited_s: float = 0.0
    bound: bool = False
    _released: bool = False
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        with suppress(OSError):
            os.ftruncate(self.fd, 0)
        try:
            _unlock(self.fd)
        finally:
            os.close(self.fd)

    @property
    def released(self) -> bool:
        return self._released

    def bind(self, process: Any) -> None:
        """Keep the slot until ``process`` exits (foreground, handed to the background, or a session)."""
        self.bound = True

        async def release_on_exit() -> None:
            try:
                await process.wait()
            finally:
                self.release()

        task = asyncio.get_running_loop().create_task(release_on_exit(), name="navin-heavy-job-slot")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def note(self) -> str:
        if self.waited_s < 1:
            return ""
        return (f"[Waited {self.waited_s:.0f}s for a free slot: the machine runs at most {slot_count()} builds, "
                "tests or installs at once, so several agents never exhaust its memory.]\n")


def running() -> list[str]:
    """Labels of the heavy commands holding a slot right now, on the whole machine."""
    labels: list[str] = []
    for path in sorted(slot_dir().glob("slot-*.lock")):
        with suppress(OSError):
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                labels.append(text.split("\t", 2)[-1])
    return labels


def try_acquire(command: str) -> Slot | None:
    """A free slot right now, or None (all taken, or memory under the floor)."""
    count = slot_count()
    available, total = memory()
    if total and available < memory_floor_bytes(total):
        return None
    folder = slot_dir()
    for index in range(count):
        path = folder / f"slot-{index}.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except OSError:
            continue
        if not _lock(fd):
            os.close(fd)
            continue
        with suppress(OSError):
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\t{int(time.time())}\t{label(command)}\n".encode())
        return Slot(index=index, fd=fd, command=command)
    return None


def _busy_reason(count: int) -> str:
    available, total = memory()
    if total and available < memory_floor_bytes(total):
        return (f"only {available / (1024 ** 3):.1f} GB of memory is free (at least "
                f"{memory_floor_bytes(total) / (1024 ** 3):.1f} GB is kept free)")
    names = ", ".join(running()[:6]) or "other agents' commands"
    return f"{count} builds, tests or installs already run on this machine ({names})"


def _refusal(command: str, waited: float, count: int) -> HostBusyError:
    return HostBusyError(
        f"Error: '{label(command)}' was not started after waiting {waited:.0f}s: {_busy_reason(count)}. "
        "Starting it anyway could exhaust the machine's memory and stop every session. Retry when one of them "
        "has finished, or ask the person to raise NAVIN_HEAVY_JOBS if the machine has room."
    )


async def admit(
    command: str,
    *,
    on_wait: Callable[[str], Awaitable[None]] | None = None,
    poll_s: float = 0.5,
) -> Slot | None:
    """A slot for a heavy command (waiting for one), or None when ``command`` needs none.

    Raises :class:`HostBusyError` when no slot freed up within ``NAVIN_HEAVY_JOB_WAIT_S`` seconds.
    """
    count = slot_count()
    if count <= 0 or not is_heavy(command):
        return None
    started = time.monotonic()
    deadline = started + max_wait_s()
    told = 0.0
    while True:
        slot = await asyncio.to_thread(try_acquire, command)
        if slot is not None:
            slot.waited_s = time.monotonic() - started
            return slot
        now = time.monotonic()
        if now >= deadline:
            raise _refusal(command, now - started, count)
        if on_wait is not None and now - told >= 15:
            told = now
            await on_wait(f"Waiting to start {label(command)}: {_busy_reason(count)}")
        await asyncio.sleep(poll_s)


@contextmanager
def blocking_slot(command: str, *, poll_s: float = 0.5) -> Iterator[Slot | None]:
    """:func:`admit` for synchronous runners (the test runner runs in a worker thread)."""
    count = slot_count()
    if count <= 0 or not is_heavy(command):
        yield None
        return
    started = time.monotonic()
    deadline = started + max_wait_s()
    while True:
        slot = try_acquire(command)
        if slot is not None:
            slot.waited_s = time.monotonic() - started
            break
        if time.monotonic() >= deadline:
            raise _refusal(command, time.monotonic() - started, count)
        time.sleep(poll_s)
    try:
        yield slot
    finally:
        slot.release()


def argv_command(argv: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in argv)


# The slot admitted by ExecTool.execute, bound to the process ExecTool._spawn starts (foreground or session).
PENDING: contextvars.ContextVar[Slot | None] = contextvars.ContextVar("navin_heavy_job_slot", default=None)


def bind_pending(process: Any) -> None:
    slot = PENDING.get()
    if slot is not None and not slot.bound and not slot.released:
        slot.bind(process)
