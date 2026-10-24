# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Cross-platform workspace file watching for the IDE (stdlib polling).

The explorer tree and Git panel used to rely on manual refresh plus a 12s
git poll: external edits (another editor, npm install, git checkout, the
agent in a different chat) stayed invisible. Native watchers (inotify,
FSEvents, ReadDirectoryChangesW) each need per-OS handles and are exactly
what breaks on WSL and UNC paths - where the IDE hurts most. A pruned,
capped, sorted mtime scan behaves identically on Linux, macOS, Windows
native and ``\\wsl.localhost`` mounts, and is cheap enough because
dependency and build directories are pruned, roots without a subscribed
chat are dropped, and idle roots back off (5s up to 30s).

One scan produces an order-independent signature; a change in any file's
(path, mtime, size) flips it, and subscribers get a single coalesced
``fs_changed`` event per workspace per tick.
"""

from __future__ import annotations

import asyncio
import os
import time
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

# Directories whose churn is noise for the explorer (build output, caches,
# vendored deps). ``.git`` changes are surfaced by the Git panel's own state
# endpoint; watching it would fire on every index touch.
_PRUNE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".navin",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".cache",
        ".turbo",
        "coverage",
    }
)

# Beyond this many entries the scan stops: the signature still covers the
# first N sorted entries deterministically, so changes inside the covered
# region are detected and the poll stays bounded on monorepos.
_MAX_ENTRIES = 20_000

# Each scan is a pure-Python walk that holds the GIL in a pool thread, and the
# gateway's event loop (HTTP, websocket, every agent) competes with it. A 3s
# fixed cadence over several 20k-entry roots starved the loop as soon as a
# couple of chats were open. So: a calmer base cadence, exponential backoff
# per root while nothing changes, and a duty-cycle floor so a slow root (big
# monorepo, WSL/UNC mount) is scanned proportionally less often.
_DEFAULT_INTERVAL_S = 5.0
_DEFAULT_MAX_INTERVAL_S = 30.0
# A root whose scan took T seconds waits at least T * this before the next
# one, keeping the watcher under ~5% of one thread per root.
_SCAN_COST_FACTOR = 20.0


def workspace_signature(
    root: Path | str,
    *,
    max_entries: int = _MAX_ENTRIES,
) -> int:
    """Digest of (relative path, mtime_ns, size) over a pruned, sorted walk.

    Sorted traversal makes the covered region stable when the entry cap is
    hit, so the signature never flaps on scandir ordering.
    """
    # A WSL project seen from Windows: list and stat inside the distribution
    # (one process) instead of one 9P stat per entry, 11 s on navin's repo.
    from navin.utils import wsl_bulk

    bulk = wsl_bulk.tree_signature(root, _PRUNE_DIRS, max_entries=max_entries)
    if bulk is not None:
        return bulk
    base = Path(root)
    digest = 0
    seen = 0
    stack: list[Path] = [base]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as scan:
                entries = sorted(scan, key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if seen >= max_entries:
                return digest
            name = entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if name in _PRUNE_DIRS:
                        continue
                    stack.append(Path(entry.path))
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            seen += 1
            rel = os.path.relpath(entry.path, base)
            token = f"{rel}\x00{stat.st_mtime_ns}\x00{stat.st_size}"
            # XOR of per-entry CRCs: order-independent and O(1) memory.
            digest ^= zlib.crc32(token.encode("utf-8", "surrogatepass"))
    return digest


@dataclass
class _Watch:
    root: str
    signature: int | None = None
    chat_ids: set[str] = field(default_factory=set)
    # Current backoff delay and the monotonic time the next scan is due.
    delay_s: float = 0.0
    next_due: float = 0.0


class WorkspaceWatcherService:
    """Poll watched workspaces and notify subscribed chats on change.

    ``notify(project_path, chat_ids)`` is awaited once per changed workspace
    per tick - the coalescing lives here, not in the transport.
    """

    def __init__(
        self,
        notify: Callable[[str, list[str]], Awaitable[None]],
        *,
        interval_s: float = _DEFAULT_INTERVAL_S,
        max_interval_s: float = _DEFAULT_MAX_INTERVAL_S,
    ) -> None:
        self._notify = notify
        self._interval_s = max(0.5, float(interval_s))
        self._max_interval_s = max(self._interval_s, float(max_interval_s))
        self._watches: dict[str, _Watch] = {}
        self._task: asyncio.Task[None] | None = None

    # -- subscriptions -------------------------------------------------------

    def watch(self, chat_id: str, project_path: str | Path | None) -> None:
        """Subscribe *chat_id* to changes under *project_path*."""
        # The websocket channel passes a Path (workspace scope). ``.strip()``
        # on that object used to throw, and the attach path swallowed it, so
        # the explorer never received ``fs_changed`` after an agent write.
        path = str(project_path).strip() if project_path is not None else ""
        if not chat_id or not path or not Path(path).is_dir():
            return
        key = os.path.normcase(os.path.abspath(path))
        entry = self._watches.get(key)
        if entry is None:
            entry = _Watch(root=path, delay_s=self._interval_s)
            self._watches[key] = entry
        elif chat_id not in entry.chat_ids:
            # A newly attached chat expects a fresh view soon: drop any
            # accumulated backoff for this root.
            entry.delay_s = self._interval_s
            entry.next_due = 0.0
        entry.chat_ids.add(chat_id)

    def unwatch_chat(self, chat_id: str) -> None:
        """Drop *chat_id* everywhere; workspaces without chats stop polling."""
        stale = []
        for key, entry in self._watches.items():
            entry.chat_ids.discard(chat_id)
            if not entry.chat_ids:
                stale.append(key)
        for key in stale:
            self._watches.pop(key, None)

    @property
    def watched_roots(self) -> list[str]:
        return [entry.root for entry in self._watches.values()]

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(), name="navin-fs-watcher"
            )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            try:
                await self.poll_once(only_due=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("fs watcher poll failed: {}", exc)
            await asyncio.sleep(self._next_sleep())

    def _next_sleep(self) -> float:
        """Seconds until the earliest root is due (bounded to the base tick)."""
        if not self._watches:
            return self._interval_s
        now = time.monotonic()
        soonest = min(entry.next_due for entry in self._watches.values())
        return min(self._interval_s, max(0.05, soonest - now))

    def _reschedule(self, entry: _Watch, *, changed: bool, cost_s: float) -> None:
        if changed:
            entry.delay_s = self._interval_s
        else:
            entry.delay_s = min(
                self._max_interval_s, max(self._interval_s, entry.delay_s * 2)
            )
        delay = max(entry.delay_s, cost_s * _SCAN_COST_FACTOR)
        entry.next_due = time.monotonic() + delay

    async def poll_once(self, *, only_due: bool = False) -> list[str]:
        """Scan watched workspaces once; notify and return changed roots.

        ``only_due`` (the background loop) skips roots still inside their
        backoff window; a direct call scans every root.
        """
        changed: list[tuple[str, list[str]]] = []
        for entry in list(self._watches.values()):
            if only_due and time.monotonic() < entry.next_due:
                continue
            started = time.monotonic()
            signature = await asyncio.to_thread(workspace_signature, entry.root)
            cost_s = time.monotonic() - started
            if not entry.chat_ids:
                # Unsubscribed while the scan ran.
                continue
            if entry.signature is None:
                # First scan is the baseline, not a change.
                entry.signature = signature
                self._reschedule(entry, changed=False, cost_s=cost_s)
                continue
            is_changed = signature != entry.signature
            self._reschedule(entry, changed=is_changed, cost_s=cost_s)
            if is_changed:
                entry.signature = signature
                changed.append((entry.root, sorted(entry.chat_ids)))
        for root, chat_ids in changed:
            try:
                await self._notify(root, chat_ids)
            except Exception as exc:  # pragma: no cover - transport-level
                logger.debug("fs watcher notify failed for {}: {}", root, exc)
        return [root for root, _ in changed]
