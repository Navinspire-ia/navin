# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Board dispatch lock scope: per board, and never held across forge I/O.

The gateway serves every session on one event loop. A single loop-wide board
lock plus an inline ``git push`` / PR call (up to minutes) meant one session
closing a task froze every other session's board moves. These tests pin that
other boards, and the same board, keep moving while a PR is being opened.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.agent.tools import board as board_tool
from navin.agent.tools.board import BoardTool
from navin.board.store import ProjectBoardStore


class BoardLockScopeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp_a = tempfile.TemporaryDirectory(prefix="navin-board-a-")
        self._tmp_b = tempfile.TemporaryDirectory(prefix="navin-board-b-")
        self.addCleanup(self._tmp_a.cleanup)
        self.addCleanup(self._tmp_b.cleanup)
        self.project_a = Path(self._tmp_a.name)
        self.project_b = Path(self._tmp_b.name)

    def test_locks_are_keyed_per_board(self) -> None:
        async def locks() -> tuple[asyncio.Lock, asyncio.Lock, asyncio.Lock]:
            a = board_tool._board_dispatch_lock(ProjectBoardStore(self.project_a))
            a2 = board_tool._board_dispatch_lock(ProjectBoardStore(self.project_a))
            b = board_tool._board_dispatch_lock(ProjectBoardStore(self.project_b))
            return a, a2, b

        a, a2, b = asyncio.run(locks())
        self.assertIs(a, a2)
        self.assertIsNot(a, b)

    async def test_pr_push_runs_outside_the_board_lock(self) -> None:
        store_a = ProjectBoardStore(self.project_a)
        task = store_a.create_task(title="ship it", actor="a", actor_type="agent")
        store_a.update_task(
            task["id"],
            fields={"branch": "navin/ship-it", "status": "in_progress"},
            actor="a",
            actor_type="agent",
        )

        entered = threading.Event()
        release = threading.Event()

        def slow_open_task_pr(project, task):  # noqa: ANN001 - test double
            entered.set()
            release.wait(10)
            return {"ok": True, "pr_url": "https://forge.example/pr/1"}

        tool_a = BoardTool(workspace=self.project_a)
        tool_b = BoardTool(workspace=self.project_b)
        with (
            patch.object(board_tool, "should_open_pr", return_value=True),
            patch.object(board_tool, "should_sync_issues", return_value=False),
            patch("navin.board.github_sync.open_task_pr", slow_open_task_pr),
        ):
            closing = asyncio.create_task(
                tool_a.execute(
                    action="move",
                    task_id=task["id"],
                    status="done",
                    evidence="tests passed",
                )
            )
            try:
                await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
                self.assertTrue(entered.is_set(), "PR hook never ran")
                # Another project's board is not blocked by the push...
                other = await asyncio.wait_for(
                    tool_b.execute(action="create", title="elsewhere"), 3
                )
                self.assertIn("Task created", str(other))
                # ...nor is this board: the lock was released before the push.
                same = await asyncio.wait_for(
                    tool_a.execute(action="create", title="next one"), 3
                )
                self.assertIn("Task created", str(same))
                self.assertFalse(closing.done())
            finally:
                release.set()
            result = await asyncio.wait_for(closing, 10)

        text = str(result)
        self.assertIn("Task moved", text)
        self.assertIn("https://forge.example/pr/1", text)
        refreshed = ProjectBoardStore(self.project_a).get_task(task["id"])
        self.assertEqual("https://forge.example/pr/1", refreshed.get("pr_url"))


if __name__ == "__main__":
    unittest.main()
