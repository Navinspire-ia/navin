# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Workspace watcher: signature semantics and the poll/notify loop.

The watcher is the reason the explorer and Git panel follow external edits
(another editor, git checkout, npm install) without manual refresh. These
tests pin the properties that make it safe to ship cross-platform: pruned
directories never wake it, the first scan is a baseline rather than a
change, notifications coalesce per workspace, and unsubscribing the last
chat stops the polling entirely.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path

from navin.webui.fs_watch import WorkspaceWatcherService, workspace_signature


def _touch(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _bump_mtime(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000))


class SignatureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="navin-fswatch-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_content_change_flips_the_signature(self) -> None:
        _touch(self.root / "src" / "main.py", "print(1)\n")
        before = workspace_signature(self.root)
        # Same byte length would keep st_size equal; the mtime still moves.
        time.sleep(0.01)
        _touch(self.root / "src" / "main.py", "print(2)\n")
        _bump_mtime(self.root / "src" / "main.py")
        self.assertNotEqual(before, workspace_signature(self.root))

    def test_new_and_deleted_files_flip_the_signature(self) -> None:
        _touch(self.root / "a.txt")
        base = workspace_signature(self.root)
        _touch(self.root / "b.txt")
        with_b = workspace_signature(self.root)
        self.assertNotEqual(base, with_b)
        (self.root / "b.txt").unlink()
        self.assertEqual(base, workspace_signature(self.root))

    def test_pruned_directories_are_invisible(self) -> None:
        _touch(self.root / "app.py")
        base = workspace_signature(self.root)
        # Churn that floods real projects: deps, caches, git plumbing.
        _touch(self.root / "node_modules" / "pkg" / "index.js")
        _touch(self.root / ".git" / "index")
        _touch(self.root / "__pycache__" / "app.cpython-312.pyc")
        _touch(self.root / ".venv" / "lib" / "site.py")
        self.assertEqual(base, workspace_signature(self.root))

    def test_signature_is_stable_across_scans(self) -> None:
        for i in range(20):
            _touch(self.root / f"dir{i % 3}" / f"f{i}.txt", str(i))
        first = workspace_signature(self.root)
        for _ in range(5):
            self.assertEqual(first, workspace_signature(self.root))

    def test_entry_cap_keeps_the_scan_bounded_and_deterministic(self) -> None:
        for i in range(30):
            _touch(self.root / f"{i:03d}.txt", str(i))
        capped = workspace_signature(self.root, max_entries=10)
        self.assertEqual(capped, workspace_signature(self.root, max_entries=10))
        # A change inside the covered (sorted-first) region is still seen.
        _bump_mtime(self.root / "000.txt")
        self.assertNotEqual(capped, workspace_signature(self.root, max_entries=10))

    def test_unreadable_root_returns_quietly(self) -> None:
        self.assertEqual(0, workspace_signature(self.root / "does-not-exist"))


class WatcherServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="navin-fswatch-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _touch(self.root / "main.py", "print('hi')\n")
        self.notifications: list[tuple[str, list[str]]] = []

    async def _notify(self, root: str, chat_ids: list[str]) -> None:
        self.notifications.append((root, chat_ids))

    def _service(self) -> WorkspaceWatcherService:
        return WorkspaceWatcherService(self._notify, interval_s=0.5)

    async def test_first_poll_is_a_baseline_not_a_change(self) -> None:
        service = self._service()
        service.watch("chat-1", str(self.root))
        self.assertEqual([], await service.poll_once())
        self.assertEqual([], self.notifications)

    async def test_change_notifies_every_subscribed_chat_once(self) -> None:
        service = self._service()
        service.watch("chat-1", str(self.root))
        service.watch("chat-2", str(self.root))
        await service.poll_once()

        _touch(self.root / "new.py", "pass\n")
        changed = await service.poll_once()
        self.assertEqual([str(self.root)], changed)
        self.assertEqual([(str(self.root), ["chat-1", "chat-2"])], self.notifications)

        # No further change: quiet.
        self.assertEqual([], await service.poll_once())
        self.assertEqual(1, len(self.notifications))

    async def test_unwatching_the_last_chat_stops_polling_that_root(self) -> None:
        service = self._service()
        service.watch("chat-1", str(self.root))
        await service.poll_once()
        service.unwatch_chat("chat-1")
        self.assertEqual([], service.watched_roots)

        _touch(self.root / "new.py")
        self.assertEqual([], await service.poll_once())
        self.assertEqual([], self.notifications)

    async def test_invalid_paths_are_ignored(self) -> None:
        service = self._service()
        service.watch("chat-1", None)
        service.watch("chat-1", "")
        service.watch("chat-1", str(self.root / "missing"))
        service.watch("", str(self.root))
        self.assertEqual([], service.watched_roots)

    async def test_a_path_object_is_accepted(self) -> None:
        service = self._service()
        service.watch("chat-1", self.root)
        self.assertEqual([str(self.root)], service.watched_roots)

    async def test_same_root_registered_twice_polls_once(self) -> None:
        service = self._service()
        # Same directory through two spellings (trailing separator).
        service.watch("chat-1", str(self.root))
        service.watch("chat-2", str(self.root) + os.sep)
        self.assertEqual(1, len(service.watched_roots))

    async def test_background_task_delivers_and_stop_cancels(self) -> None:
        service = WorkspaceWatcherService(self._notify, interval_s=0.05)
        service.watch("chat-1", str(self.root))
        service.start()
        try:
            await asyncio.sleep(0.15)  # baseline scan
            _touch(self.root / "later.py", "x = 1\n")
            deadline = time.monotonic() + 5.0
            while not self.notifications and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            self.assertTrue(self.notifications, "watcher never fired")
        finally:
            await service.stop()

    async def test_notify_errors_do_not_kill_the_poll(self) -> None:
        async def boom(root: str, chat_ids: list[str]) -> None:
            raise RuntimeError("transport down")

        service = WorkspaceWatcherService(boom, interval_s=0.5)
        service.watch("chat-1", str(self.root))
        await service.poll_once()
        _touch(self.root / "new.py")
        changed = await service.poll_once()
        self.assertEqual([str(self.root)], changed)


if __name__ == "__main__":
    unittest.main()
