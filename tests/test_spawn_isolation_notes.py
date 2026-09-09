# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Isolation that silently fails is worse than isolation that never existed.

When a workspace cannot be isolated - not a git repository, or one with no
commit - the subagent still runs, in the shared tree. That fallback is fine;
what is not fine is the parent never hearing about it: it asked for isolation,
so it believes the edits are parked in a checkout waiting to be merged, when
they already landed in the user's tree. These assert the completion
announcement says so.
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
    """A runner that finishes immediately; only the announcement matters here."""

    async def run(self, spec) -> AgentRunResult:
        return AgentRunResult(final_content="did the work", messages=[])


class IsolationFallbackNoteTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_the_note_says_the_edits_are_in_the_users_tree(self) -> None:
        # The whole point: the parent must not go looking for a checkout to
        # merge, because there is none.
        announcement = await self._run(isolate=True)
        self.assertIn("already in the user's tree", announcement)

    async def test_the_result_itself_is_still_delivered(self) -> None:
        announcement = await self._run(isolate=True)
        self.assertIn("did the work", announcement)

    async def test_a_run_that_never_asked_for_isolation_gets_no_note(self) -> None:
        announcement = await self._run(isolate=False)
        self.assertNotIn("isolation was requested", announcement)


if __name__ == "__main__":
    unittest.main()
