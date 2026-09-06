"""Scoped subagents: a big audit fans out into small missions, each with a
narrow scope, instead of streaming the whole repository through one context.

The scope is advisory (the subagent may still cross-check outside it) but the
prompt must name the paths and restate the never-invent evidence rules, so
splitting the work does not split the quality bar.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.subagent import SubagentManager
from navin.agent.tools.spawn import SpawnTool


class ScopedPromptTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = SubagentManager(
            workspace=Path(self._tmp.name),
            bus=mock.Mock(),
            max_tool_result_chars=4000,
        )

    def test_scoped_prompt_names_the_paths(self) -> None:
        prompt = self.manager._build_subagent_prompt(
            scope_paths=["navin/agent", "tests/test_runner.py"],
        )
        self.assertIn("Mission scope", prompt)
        self.assertIn("- navin/agent", prompt)
        self.assertIn("- tests/test_runner.py", prompt)

    def test_scoped_prompt_keeps_the_evidence_bar(self) -> None:
        # Fan-out must not become a quality loophole: every scoped mission
        # restates that unverified claims are not reportable.
        prompt = self.manager._build_subagent_prompt(scope_paths=["navin/agent"])
        self.assertIn("file path, line, and excerpt", prompt)
        self.assertIn("If you did not verify it, do not report it", prompt)

    def test_unscoped_prompt_has_no_scope_block(self) -> None:
        prompt = self.manager._build_subagent_prompt()
        self.assertNotIn("Mission scope", prompt)
        empty = self.manager._build_subagent_prompt(scope_paths=[])
        self.assertNotIn("Mission scope", empty)


class ParentContextPromptTest(unittest.TestCase):
    """P3-1: the subagent starts where the parent stands, not blind.

    The parent turn sees a context pack (open/attached files) and the board
    digest as runtime-context blocks; the subagent prompt now carries the same
    facts, so it inherits the parent's focus paths and the plan's current step.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.manager = SubagentManager(
            workspace=self.root,
            bus=mock.Mock(),
            max_tool_result_chars=4000,
        )

    def test_prompt_inherits_parent_focus_paths(self) -> None:
        prompt = self.manager._build_subagent_prompt(
            parent_metadata={"open_files": ["src/app.py", "src/api/routes.py"]},
        )
        self.assertIn("Parent context", prompt)
        self.assertIn("src/app.py", prompt)
        self.assertIn("src/api/routes.py", prompt)

    def test_prompt_names_the_boards_current_step(self) -> None:
        board_dir = self.root / ".navin" / "board"
        board_dir.mkdir(parents=True)
        (board_dir / "board.json").write_text(
            json.dumps({
                "tasks": [
                    {"id": "t1", "title": "Migrate the preview", "status": "in_progress"},
                    {"id": "t2", "title": "Polish the sidebar", "status": "backlog"},
                ]
            }),
            encoding="utf-8",
        )
        prompt = self.manager._build_subagent_prompt()
        self.assertIn("Parent context", prompt)
        self.assertIn("In progress", prompt)
        self.assertIn("t1", prompt)
        self.assertIn("Migrate the preview", prompt)

    def test_no_parent_context_block_without_facts(self) -> None:
        # Empty metadata, no board, clean non-git tree: nothing to inject, and
        # an empty header would only teach the model to skip the section.
        prompt = self.manager._build_subagent_prompt()
        self.assertNotIn("Parent context", prompt)


class SpawnToolScopeTest(unittest.IsolatedAsyncioTestCase):
    async def test_scope_paths_string_is_split_and_forwarded(self) -> None:
        manager = mock.Mock()
        manager.get_running_count_by_session.return_value = 0
        manager.max_concurrent_subagents = 5
        manager.spawn = mock.AsyncMock(return_value="started")
        tool = SpawnTool(manager)

        request_ctx = mock.Mock(
            channel="cli",
            chat_id="direct",
            session_key="cli:direct",
            message_id=None,
            runtime=mock.Mock(),
        )
        with mock.patch(
            "navin.agent.tools.spawn.current_request_context",
            return_value=request_ctx,
        ), mock.patch(
            "navin.agent.tools.spawn.current_workspace_scope",
            return_value=None,
        ):
            await tool.execute(
                task="audit the agent core",
                scope_paths="navin/agent, tests/test_runner.py ,,",
            )
            manager.spawn.assert_awaited_once()
            kwargs = manager.spawn.await_args.kwargs
            self.assertEqual(
                kwargs["scope_paths"],
                ["navin/agent", "tests/test_runner.py"],
            )

            manager.spawn.reset_mock()
            await tool.execute(task="no scope")
            self.assertIsNone(manager.spawn.await_args.kwargs["scope_paths"])

    async def test_parent_metadata_is_forwarded(self) -> None:
        manager = mock.Mock()
        manager.get_running_count_by_session.return_value = 0
        manager.max_concurrent_subagents = 5
        manager.spawn = mock.AsyncMock(return_value="started")
        tool = SpawnTool(manager)

        request_ctx = mock.Mock(
            channel="cli",
            chat_id="direct",
            session_key="cli:direct",
            message_id=None,
            runtime=mock.Mock(),
            metadata={"open_files": ["src/app.py"]},
        )
        with mock.patch(
            "navin.agent.tools.spawn.current_request_context",
            return_value=request_ctx,
        ), mock.patch(
            "navin.agent.tools.spawn.current_workspace_scope",
            return_value=None,
        ):
            await tool.execute(task="continue the migration")
            kwargs = manager.spawn.await_args.kwargs
            self.assertEqual(
                kwargs["parent_metadata"],
                {"open_files": ["src/app.py"]},
            )

            # Anything that is not a plain dict must not leak into the prompt.
            manager.spawn.reset_mock()
            request_ctx.metadata = None
            await tool.execute(task="no metadata")
            self.assertIsNone(manager.spawn.await_args.kwargs["parent_metadata"])


if __name__ == "__main__":
    unittest.main()
