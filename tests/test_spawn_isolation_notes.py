# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Isolation that silently fails is worse than isolation that never existed.

When a workspace cannot be isolated, the task must stop before tools or the
model can write into the shared tree. An explicitly shared run still works,
and every outcome tells the parent where the work is, without false success.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.runner import AgentRunResult
from navin.agent.subagent import SubagentManager


class _Bus:
    """Collects the announcements a real bus would deliver to the parent."""

    def __init__(self) -> None:
        self.published: list = []

    async def publish_inbound(self, msg) -> None:
        self.published.append(msg)


class _Runner:
    """A runner whose write proves whether task execution was allowed."""

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, spec) -> AgentRunResult:
        self.calls += 1
        Path(spec.workspace, "task-edit.txt").write_text("did the work\n")
        return AgentRunResult(final_content="did the work", messages=[])


class IsolationRequiredTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # A plain directory: worktree.create() has nothing to check out from.
        self.workspace = Path(self._tmp.name)
        self.bus = _Bus()
        self.manager = SubagentManager(
            workspace=self.workspace,
            bus=self.bus,
            max_tool_result_chars=4000,
        )
        self.manager.runner = _Runner()
        patcher = mock.patch("navin.agent.subagent._outcomes_dir", lambda: self.workspace / "history")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.manager.flush_journal()
        if self.manager._watchdog_task is not None:
            self.manager._watchdog_task.cancel()
            await asyncio.gather(self.manager._watchdog_task, return_exceptions=True)

    async def _run(self, isolate: bool) -> str:
        await self.manager.spawn(
            task="edit something",
            runtime=mock.Mock(),
            origin_channel="cli",
            origin_chat_id="local",
            session_key="cli:local",
            isolate=isolate,
        )
        await asyncio.gather(*list(self.manager._running_tasks.values()))
        self.assertEqual(len(self.bus.published), 1)
        return self.bus.published[0].content

    async def test_the_announcement_names_the_failed_isolation(self) -> None:
        announcement = await self._run(isolate=True)
        self.assertIn("isolation was requested", announcement)
        self.assertIn("shared working tree", announcement)

    async def test_failed_isolation_never_runs_or_writes_in_the_users_tree(self) -> None:
        announcement = await self._run(isolate=True)
        self.assertIn("No task tools or model were run", announcement)
        self.assertEqual(self.manager.runner.calls, 0)
        self.assertFalse((self.workspace / "task-edit.txt").exists())

    async def test_the_failure_is_delivered_instead_of_a_false_success(self) -> None:
        announcement = await self._run(isolate=True)
        self.assertIn("failed", announcement)
        self.assertIn("explicit new request", announcement)
        self.assertNotIn("completed successfully", announcement)
        self.assertNotIn("did the work", announcement)

    async def test_a_run_that_never_asked_for_isolation_gets_no_note(self) -> None:
        announcement = await self._run(isolate=False)
        self.assertNotIn("isolation was requested", announcement)
        self.assertIn("completed successfully", announcement)
        self.assertIn("did the work", announcement)
        self.assertEqual(self.manager.runner.calls, 1)
        self.assertEqual((self.workspace / "task-edit.txt").read_text(), "did the work\n")


if __name__ == "__main__":
    unittest.main()
