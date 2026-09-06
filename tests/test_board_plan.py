"""Tests for the board planning layer: ready queue, blocking, critical path."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from navin.agent.tools.board import BoardTool
from navin.board.ledger import MissionLedgerStore
from navin.board.plan import (
    blocking_dependencies,
    critical_path,
    dependency_issues,
    find_cycles,
    milestone_progress,
    plan_summary,
)
from navin.board.store import ProjectBoardStore


def _task(
    task_id: str,
    *,
    status: str = "backlog",
    priority: str = "medium",
    depends_on: list[str] | None = None,
    milestone_id: str | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "status": status,
        "priority": priority,
        "depends_on": depends_on or [],
        "milestone_id": milestone_id,
        "created_at": created_at,
    }


class BlockingTest(unittest.TestCase):
    def test_unfinished_dependency_blocks(self):
        tasks = [_task("a"), _task("b", depends_on=["a"])]
        self.assertEqual(blocking_dependencies(tasks), {"a": [], "b": ["a"]})

    def test_done_dependency_stops_blocking(self):
        tasks = [_task("a", status="done"), _task("b", depends_on=["a"])]
        self.assertEqual(blocking_dependencies(tasks)["b"], [])

    def test_missing_dependency_blocks_and_is_reported(self):
        tasks = [_task("b", depends_on=["ghost"])]
        self.assertEqual(blocking_dependencies(tasks)["b"], ["ghost"])
        self.assertEqual(dependency_issues(tasks), {"b": ["ghost"]})

    def test_closed_task_is_never_blocked(self):
        tasks = [_task("a"), _task("b", status="done", depends_on=["a"])]
        self.assertEqual(blocking_dependencies(tasks)["b"], [])

    def test_cancelled_dependency_does_not_unblock(self):
        tasks = [_task("a", status="cancelled"), _task("b", depends_on=["a"])]
        self.assertEqual(blocking_dependencies(tasks)["b"], ["a"])


class ReadyQueueTest(unittest.TestCase):
    def test_only_unblocked_open_tasks_are_ready(self):
        tasks = [_task("a"), _task("b", depends_on=["a"]), _task("c", status="done")]
        summary = plan_summary(tasks)
        self.assertEqual(summary["ready_queue"], ["a"])
        self.assertEqual([entry["id"] for entry in summary["blocked"]], ["b"])

    def test_parked_status_is_blocked_even_without_dependencies(self):
        summary = plan_summary([_task("a", status="blocked")])
        self.assertEqual(summary["ready_queue"], [])
        self.assertEqual(summary["blocked"][0]["id"], "a")

    def test_critical_path_outranks_priority(self):
        # "leaf" is critical priority but helps nothing; "root" unblocks a chain.
        tasks = [
            _task("leaf", priority="critical"),
            _task("root"),
            _task("mid", depends_on=["root"]),
            _task("tip", depends_on=["mid"]),
        ]
        self.assertEqual(plan_summary(tasks)["ready_queue"][0], "root")

    def test_priority_breaks_ties_between_equivalent_tasks(self):
        tasks = [_task("low", priority="low"), _task("high", priority="high")]
        self.assertEqual(plan_summary(tasks)["ready_queue"], ["high", "low"])

    def test_creation_order_is_the_final_tiebreak(self):
        tasks = [
            _task("second", created_at="2026-02-01T00:00:00Z"),
            _task("first", created_at="2026-01-01T00:00:00Z"),
        ]
        self.assertEqual(plan_summary(tasks)["ready_queue"], ["first", "second"])

    def test_started_tasks_leave_the_queue(self):
        summary = plan_summary([_task("a", status="in_progress"), _task("b")])
        self.assertEqual(summary["in_progress"], ["a"])
        self.assertEqual(summary["ready_queue"], ["b"])

    def test_tasks_awaiting_review_are_not_offered_again(self):
        for status in ("review", "audit"):
            with self.subTest(status=status):
                summary = plan_summary([_task("a", status=status)])
                self.assertEqual(summary["ready_queue"], [])

    def test_fix_status_is_pickable_work(self):
        self.assertEqual(plan_summary([_task("a", status="fix")])["ready_queue"], ["a"])


class CriticalPathTest(unittest.TestCase):
    def test_longest_chain_is_returned_dependencies_first(self):
        tasks = [
            _task("a"),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["b"]),
            _task("solo"),
        ]
        self.assertEqual(critical_path(tasks), ["a", "b", "c"])

    def test_done_tasks_are_excluded_from_the_path(self):
        tasks = [
            _task("a", status="done"),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["b"]),
        ]
        self.assertEqual(critical_path(tasks), ["b", "c"])

    def test_independent_tasks_have_no_critical_path(self):
        # Nothing constrains the order, so there is no chain to shorten.
        self.assertEqual(critical_path([_task("a")]), [])
        self.assertEqual(critical_path([_task("a"), _task("b")]), [])

    def test_empty_board_has_no_path(self):
        self.assertEqual(critical_path([]), [])


class CycleTest(unittest.TestCase):
    def test_two_task_cycle_is_detected(self):
        tasks = [_task("a", depends_on=["b"]), _task("b", depends_on=["a"])]
        cycles = find_cycles(tasks)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(set(cycles[0]), {"a", "b"})

    def test_tasks_in_a_cycle_are_never_ready(self):
        tasks = [_task("a", depends_on=["b"]), _task("b", depends_on=["a"])]
        summary = plan_summary(tasks)
        self.assertEqual(summary["ready_queue"], [])
        self.assertEqual(len(summary["cycles"]), 1)

    def test_acyclic_graph_reports_no_cycle(self):
        tasks = [_task("a"), _task("b", depends_on=["a"])]
        self.assertEqual(find_cycles(tasks), [])

    def test_self_dependency_cannot_be_stored(self):
        # The store strips self-references on read, so plan never sees them.
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectBoardStore(Path(tmp))
            task = store.create_task(title="solo", actor="me", actor_type="human")
            store.update_task(
                task["id"], fields={"depends_on": [task["id"]]}, actor="me", actor_type="human",
            )
            self.assertEqual(store.get_task(task["id"])["depends_on"], [])


class MilestoneProgressTest(unittest.TestCase):
    def test_counts_done_open_and_blocked_per_milestone(self):
        tasks = [
            _task("a", status="done", milestone_id="m1"),
            _task("b", milestone_id="m1"),
            _task("c", depends_on=["b"], milestone_id="m1"),
            _task("d", milestone_id="m2"),
        ]
        milestones = [{"id": "m1"}, {"id": "m2"}]
        progress = milestone_progress(tasks, milestones)
        self.assertEqual(progress["m1"], {"total": 3, "done": 1, "open": 2, "blocked": 1})
        self.assertEqual(progress["m2"], {"total": 1, "done": 0, "open": 1, "blocked": 0})

    def test_tasks_without_a_milestone_are_ignored(self):
        progress = milestone_progress([_task("a")], [{"id": "m1"}])
        self.assertEqual(progress["m1"]["total"], 0)


class StorePayloadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ProjectBoardStore(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_payload_exposes_plan_and_milestone_progress(self):
        first = self.store.create_task(title="first", actor="me", actor_type="human")
        self.store.create_task(
            title="second",
            actor="me",
            actor_type="human",
            depends_on=[first["id"]],
        )
        payload = self.store.payload()
        self.assertEqual(payload["plan"]["ready_queue"], [first["id"]])
        self.assertEqual(len(payload["plan"]["blocked"]), 1)
        self.assertIn("milestone_progress", payload)

    def test_finishing_a_dependency_releases_its_dependent(self):
        first = self.store.create_task(title="first", actor="me", actor_type="human")
        second = self.store.create_task(
            title="second", actor="me", actor_type="human", depends_on=[first["id"]],
        )
        self.store.update_task(
            first["id"], fields={"status": "done"}, actor="me", actor_type="human",
        )
        self.assertEqual(self.store.payload()["plan"]["ready_queue"], [second["id"]])


class BoardToolPlanningTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        self.tool = BoardTool(workspace=self.project)

    def tearDown(self):
        self._tmp.cleanup()

    async def _run(self, **kwargs: Any) -> str:
        result = await self.tool.execute(**kwargs)
        return result if isinstance(result, str) else str(result)

    def test_next_explains_an_empty_board(self):
        import asyncio

        out = asyncio.run(self._run(action="next"))
        self.assertIn("Board is empty", out)

    def test_next_lists_ready_tasks_and_hides_blocked_ones(self):
        import asyncio

        async def scenario() -> str:
            await self.tool.execute(action="create", title="alpha", actor="a")
            store = ProjectBoardStore(self.project)
            base = store.read_tasks()[0]["id"]
            await self.tool.execute(
                action="create", title="omega", depends_on=[base], actor="a",
            )
            return await self._run(action="next")

        out = asyncio.run(scenario())
        self.assertIn("alpha", out)
        self.assertNotIn("omega", out)

    def test_next_reports_why_nothing_is_ready(self):
        import asyncio

        async def scenario() -> str:
            await self.tool.execute(action="create", title="parked", status="blocked", actor="a")
            return await self._run(action="next")

        out = asyncio.run(scenario())
        self.assertIn("blocked", out.lower())

    def test_plan_reports_critical_path_and_blocked_reasons(self):
        import asyncio

        async def scenario() -> str:
            await self.tool.execute(action="create", title="step one", actor="a")
            store = ProjectBoardStore(self.project)
            first = store.read_tasks()[0]["id"]
            await self.tool.execute(
                action="create", title="step two", depends_on=[first], actor="a",
            )
            return await self._run(action="plan")

        out = asyncio.run(scenario())
        self.assertIn("Critical path", out)
        self.assertIn("Blocked", out)
        self.assertIn("waiting on", out)

    def test_update_can_rewire_dependencies(self):
        import asyncio

        async def scenario() -> list[str]:
            await self.tool.execute(action="create", title="a", actor="x")
            await self.tool.execute(action="create", title="b", actor="x")
            store = ProjectBoardStore(self.project)
            tasks = store.read_tasks()
            await self.tool.execute(
                action="update", task_id=tasks[1]["id"], depends_on=[tasks[0]["id"]], actor="x",
            )
            return store.get_task(tasks[1]["id"])["depends_on"]

        self.assertEqual(len(asyncio.run(scenario())), 1)


class LocalReplanReadyQueueTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        self.tool = BoardTool(workspace=self.project)
        self.board = ProjectBoardStore(self.project)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_ready_queue_ignores_downstream_after_local_replan(self) -> None:
        first = await self.tool.execute(action="create", title="Done", status="done", actor="a")
        id1 = first.split(" ", 2)[2].split(" ", 1)[0]
        second = await self.tool.execute(
            action="create", title="Failed", status="in_progress", actor="a",
        )
        id2 = second.split(" ", 2)[2].split(" ", 1)[0]
        third = await self.tool.execute(
            action="create", title="Downstream", status="planned", actor="a",
        )
        id3 = third.split(" ", 2)[2].split(" ", 1)[0]
        await self.tool.execute(action="update", task_id=id2, depends_on=[id1], actor="a")
        await self.tool.execute(action="update", task_id=id3, depends_on=[id2], actor="a")
        await self.tool.execute(
            action="ledger_init",
            goal="Replan queue",
            step_ids=[id1, id2, id3],
            actor="a",
        )
        await self.tool.execute(
            action="ledger_replan", task_id=id2, reason="step stalled", actor="a",
        )
        summary = plan_summary(self.board.read_tasks())
        self.assertEqual(summary["ready_queue"], [id2])
        blocked_ids = [entry["id"] for entry in summary["blocked"]]
        self.assertIn(id3, blocked_ids)
        ledger = MissionLedgerStore(self.project).load()
        assert ledger is not None
        self.assertEqual(ledger["version"], 2)


if __name__ == "__main__":
    unittest.main()
