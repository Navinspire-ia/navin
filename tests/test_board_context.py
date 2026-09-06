"""The agent has to see its own plan without being asked to look it up.

The board survived on disk but was never re-read, so between two turns the plan
existed only in whatever the model still remembered. These cover the digest that
now rides along with every turn: what it says, and just as importantly when it
says nothing, because a block that appears on every turn with no news is a block
the model learns to skip.
"""

from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from typing import Any

from navin.agent.tools.context import RequestContext
from navin.board.context import (
    board_context_provider,
    board_digest,
    board_summary_lines,
)
from navin.board.store import ProjectBoardStore


def _task(
    task_id: str,
    *,
    title: str | None = None,
    status: str = "backlog",
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": title or f"Task {task_id}",
        "status": status,
        "priority": "medium",
        "depends_on": depends_on or [],
        "milestone_id": None,
        "created_at": "2026-01-01T00:00:00Z",
    }


def _text(lines: list[str]) -> str:
    return "\n".join(lines)


class SilenceTest(unittest.TestCase):
    """Nothing to report must produce nothing at all."""

    def test_an_empty_board_says_nothing(self) -> None:
        self.assertEqual(board_summary_lines([]), [])

    def test_a_finished_board_says_nothing(self) -> None:
        tasks = [_task("a", status="done"), _task("b", status="done")]
        self.assertEqual(board_summary_lines(tasks), [])

    def test_a_cancelled_board_says_nothing(self) -> None:
        self.assertEqual(board_summary_lines([_task("a", status="done")]), [])


class DigestTest(unittest.TestCase):
    def test_open_and_done_counts_are_reported(self) -> None:
        tasks = [_task("a", status="done"), _task("b"), _task("c")]
        self.assertIn("2 open, 1 done", _text(board_summary_lines(tasks)))

    def test_running_work_is_named(self) -> None:
        tasks = [_task("a", title="Wire the parser", status="in_progress")]
        self.assertIn("In progress: a (Wire the parser)", _text(board_summary_lines(tasks)))

    def test_the_next_task_is_named(self) -> None:
        tasks = [_task("a", title="Wire the parser")]
        self.assertIn("Ready next: a (Wire the parser)", _text(board_summary_lines(tasks)))

    def test_a_long_queue_is_summarised_not_dumped(self) -> None:
        """A digest that lists forty tasks is the board, not a digest."""
        tasks = [_task(f"t{i:02d}") for i in range(40)]
        text = _text(board_summary_lines(tasks))
        self.assertIn("more", text)
        self.assertLess(len(text), 400)

    def test_a_long_title_is_truncated(self) -> None:
        tasks = [_task("a", title="x" * 300)]
        for line in board_summary_lines(tasks):
            self.assertLess(len(line), 200)

    def test_blocked_work_is_counted(self) -> None:
        tasks = [_task("a"), _task("b", depends_on=["a"]), _task("c", depends_on=["a"])]
        self.assertIn("Blocked: 2.", _text(board_summary_lines(tasks)))

    def test_a_fully_stalled_board_says_so(self) -> None:
        """Open work that nobody can start is the one state the queue cannot fix."""
        tasks = [_task("a", depends_on=["ghost"])]
        text = _text(board_summary_lines(tasks))
        self.assertIn("Nothing is ready to start", text)

    def test_a_cycle_is_escalated_not_just_counted(self) -> None:
        tasks = [_task("a", depends_on=["b"]), _task("b", depends_on=["a"])]
        text = _text(board_summary_lines(tasks))
        self.assertIn("cycle", text)
        self.assertIn("never become ready", text)

    def test_a_missing_dependency_is_escalated(self) -> None:
        tasks = [_task("a"), _task("b", depends_on=["ghost"])]
        self.assertIn("missing task", _text(board_summary_lines(tasks)))

    def test_running_work_does_not_trigger_the_stalled_warning(self) -> None:
        tasks = [
            _task("a", status="in_progress"),
            _task("b", depends_on=["a"]),
        ]
        self.assertNotIn("Nothing is ready to start", _text(board_summary_lines(tasks)))


class FactsOnlyTest(unittest.TestCase):
    """The digest travels under a marker that says "metadata, not instructions".

    An imperative written here is one the model has been told it may ignore, so
    what to do about a stalled queue lives in the tool contract instead. These
    catch guidance drifting back into the digest.
    """

    def test_the_digest_states_facts_and_does_not_instruct(self) -> None:
        tasks = [
            _task("a", depends_on=["b"]),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["ghost"]),
        ]
        text = _text(board_summary_lines(tasks)).lower()
        for phrasing in ("use `board", "you should", "ask which", "before opening"):
            self.assertNotIn(phrasing, text)


class RegistrationTest(unittest.TestCase):
    """A digest nothing calls is worse than no digest: it looks done.

    The provider is wired inline in ``AgentLoop.__init__``, which no test builds
    cheaply, so this reads the source rather than an instance. Weaker than calling
    the loop, and still enough for the failure it exists to catch: dropping or
    renaming that one line while every other board test stays green.
    """

    def test_the_provider_is_wired_into_the_agent_loop(self) -> None:
        from navin.agent import loop as agent_loop

        self.assertIn(
            "board_context_provider",
            inspect.getsource(agent_loop.AgentLoop.__init__),
        )
        self.assertIs(agent_loop.board_context_provider, board_context_provider)


class _StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()

    def _seed(self, tasks: list[dict[str, Any]]) -> None:
        store = ProjectBoardStore(self.project)
        for task in tasks:
            store.create_task(
                title=task["title"],
                status=task["status"],
                depends_on=task["depends_on"],
                actor="navin",
                actor_type="agent",
            )


class ReadFromDiskTest(_StoreTest):
    def test_a_project_with_no_board_yields_nothing(self) -> None:
        self.assertEqual(board_digest(self.project), [])

    def test_a_seeded_board_is_read_back(self) -> None:
        self._seed([_task("a", title="Ship the thing")])
        self.assertIn("Ship the thing", _text(board_digest(self.project)))

    def test_a_corrupt_board_does_not_break_the_turn(self) -> None:
        """A malformed plan file must cost the agent a hint, never the turn."""
        board_dir = self.project / ".navin" / "board"
        board_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(board_digest(self.project), [])

    def test_an_unreadable_project_path_does_not_break_the_turn(self) -> None:
        self.assertEqual(board_digest(self.project / "absent" / "deeper"), [])


class ProviderTest(_StoreTest):
    def _resolve(self, workspace: Path | None):
        request = RequestContext(
            channel="cli",
            chat_id="direct",
            message_id="m1",
            session_key="s1",
            original_user_text="go",
            workspace=workspace,
        )
        return asyncio.run(board_context_provider(request))

    def test_no_workspace_means_no_block(self) -> None:
        self.assertIsNone(self._resolve(None))

    def test_an_empty_board_produces_no_block(self) -> None:
        self.assertIsNone(self._resolve(self.project))

    def test_an_active_board_produces_a_tagged_block(self) -> None:
        self._seed([_task("a", title="Ship the thing")])
        block = self._resolve(self.project)
        self.assertIsNotNone(block)
        assert block is not None
        self.assertEqual(block.source, "board")
        self.assertIn("Ship the thing", block.content)
        self.assertIn("Runtime Context", block.content)


if __name__ == "__main__":
    unittest.main()
