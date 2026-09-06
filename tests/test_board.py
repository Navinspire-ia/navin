"""Tests for the shared project task board: store, HTTP payloads, agent tool."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.board import BoardTool
from navin.board.store import BoardError, ProjectBoardStore
from navin.webui.board_api import board_update_payload


class _StoreTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        self.store = ProjectBoardStore(self.project)

    def tearDown(self):
        self._tmp.cleanup()


class TaskCrudTest(_StoreTestBase):
    def test_create_and_read_roundtrip(self):
        task = self.store.create_task(
            title="Fix login", actor="agent", actor_type="agent", priority="high",
        )
        tasks = self.store.read_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], task["id"])
        self.assertEqual(tasks[0]["status"], "backlog")
        self.assertEqual(tasks[0]["priority"], "high")
        self.assertEqual(tasks[0]["created_by"], {"type": "agent", "name": "agent"})

    def test_title_is_required(self):
        with self.assertRaises(BoardError):
            self.store.create_task(title="   ", actor="a", actor_type="agent")

    def test_invalid_status_rejected(self):
        with self.assertRaises(BoardError):
            self.store.create_task(
                title="x", status="doing", actor="a", actor_type="agent",
            )
        task = self.store.create_task(title="x", actor="a", actor_type="agent")
        with self.assertRaises(BoardError):
            self.store.update_task(
                task["id"], fields={"status": "nope"}, actor="a", actor_type="agent",
            )

    def test_unknown_update_field_rejected(self):
        task = self.store.create_task(title="x", actor="a", actor_type="agent")
        with self.assertRaises(BoardError):
            self.store.update_task(
                task["id"], fields={"id": "t-hack"}, actor="a", actor_type="agent",
            )

    def test_move_records_transition_in_activity(self):
        task = self.store.create_task(title="x", actor="a", actor_type="agent")
        self.store.update_task(
            task["id"], fields={"status": "in_progress"}, actor="a", actor_type="agent",
        )
        moves = [e for e in self.store.read_activity() if e["kind"] == "task_moved"]
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["detail"], "backlog -> in_progress")

    def test_claim_assigns_and_starts(self):
        task = self.store.create_task(title="x", actor="a", actor_type="agent")
        claimed = self.store.claim_task(task["id"], actor="worker", actor_type="subagent")
        self.assertEqual(claimed["assignee"], {"type": "subagent", "name": "worker"})
        self.assertEqual(claimed["status"], "in_progress")

    def test_comment_appended_with_author(self):
        task = self.store.create_task(title="x", actor="a", actor_type="agent")
        self.store.comment_task(
            task["id"], text="found it", actor="aymen", actor_type="human",
        )
        stored = self.store.get_task(task["id"])
        self.assertEqual(len(stored["comments"]), 1)
        self.assertEqual(stored["comments"][0]["author"], "aymen")
        self.assertEqual(stored["comments"][0]["author_type"], "human")

    def test_delete_cleans_dependencies(self):
        first = self.store.create_task(title="a", actor="a", actor_type="agent")
        second = self.store.create_task(
            title="b", depends_on=[first["id"]], actor="a", actor_type="agent",
        )
        self.store.delete_task(first["id"], actor="a", actor_type="agent")
        remaining = self.store.read_tasks()
        self.assertEqual([t["id"] for t in remaining], [second["id"]])
        self.assertEqual(remaining[0]["depends_on"], [])

    def test_missing_task_raises_404(self):
        with self.assertRaises(BoardError) as ctx:
            self.store.get_task("t-missing")
        self.assertEqual(ctx.exception.status, 404)

    def test_corrupt_board_file_reads_as_empty(self):
        self.store.create_task(title="x", actor="a", actor_type="agent")
        self.store.board_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.read_tasks(), [])


class DoneGateTest(_StoreTestBase):
    """Closing a task must be earned: stated criteria demand recorded proof."""

    def _task(self, **fields):
        task = self.store.create_task(title="step", actor="a", actor_type="agent")
        if fields:
            task = self.store.update_task(
                task["id"], fields=fields, actor="a", actor_type="agent",
            )
        return task

    def test_validation_verify_without_evidence_refuses_done(self):
        task = self._task(validation="verify")
        with self.assertRaises(BoardError) as ctx:
            self.store.update_task(
                task["id"], fields={"status": "done"}, actor="a", actor_type="agent",
            )
        self.assertIn("requires evidence", str(ctx.exception))

    def test_acceptance_without_evidence_refuses_done_even_when_validation_none(self):
        task = self._task(acceptance="login page renders and submits")
        with self.assertRaises(BoardError) as ctx:
            self.store.update_task(
                task["id"], fields={"status": "done"}, actor="a", actor_type="agent",
            )
        self.assertIn("acceptance criteria", str(ctx.exception))

    def test_acceptance_with_evidence_closes(self):
        task = self._task(acceptance="login page renders")
        closed = self.store.update_task(
            task["id"],
            fields={"status": "done", "evidence": "verify passed; preview checked"},
            actor="a",
            actor_type="agent",
        )
        self.assertEqual(closed["status"], "done")

    def test_no_criteria_and_no_validation_closes_freely(self):
        task = self._task()
        closed = self.store.update_task(
            task["id"], fields={"status": "done"}, actor="a", actor_type="agent",
        )
        self.assertEqual(closed["status"], "done")


class RealRunGateTest(_StoreTestBase):
    """Prose evidence is not proof: agents also need a recorded green run.

    The evidence string is written by the model, so it can exist without any
    test having run. The second gate checks the verification log that only
    the quality tools write, Cursor-style: no green run, no done.
    """

    def _gated_task(self, validation: str = "verify"):
        task = self.store.create_task(title="step", actor="a", actor_type="agent")
        return self.store.update_task(
            task["id"],
            fields={"validation": validation, "evidence": "tests green (prose)"},
            actor="a",
            actor_type="agent",
        )

    def _close(self, task, actor_type: str = "agent"):
        return self.store.update_task(
            task["id"], fields={"status": "done"}, actor="a", actor_type=actor_type,
        )

    def test_prose_evidence_alone_is_rejected_for_agents(self):
        task = self._gated_task()
        with self.assertRaises(BoardError) as ctx:
            self._close(task)
        self.assertIn("no verification run is recorded", str(ctx.exception))

    def test_a_fresh_green_run_unlocks_done(self):
        from navin.quality.verification_log import record_verification

        record_verification(
            self.project, source="verify", ok=True, tests_ran=True, summary="clean",
        )
        closed = self._close(self._gated_task())
        self.assertEqual(closed["status"], "done")

    def test_a_red_run_blocks_done(self):
        from navin.quality.verification_log import record_verification

        record_verification(
            self.project, source="verify", ok=False, summary="2 failed",
        )
        with self.assertRaises(BoardError) as ctx:
            self._close(self._gated_task())
        self.assertIn("FAILED", str(ctx.exception))

    def test_a_stale_green_run_blocks_done(self):
        from navin.quality import verification_log

        verification_log.record_verification(
            self.project, source="verify", ok=True, tests_ran=True,
        )
        # Age the recorded run past the freshness window on disk.
        path = verification_log._log_path(self.project)
        entries = json.loads(path.read_text(encoding="utf-8"))
        entries[-1]["ts"] -= verification_log.DEFAULT_MAX_AGE_S + 60
        path.write_text(json.dumps(entries), encoding="utf-8")

        with self.assertRaises(BoardError) as ctx:
            self._close(self._gated_task())
        self.assertIn("min old", str(ctx.exception))

    def test_a_lint_only_pass_is_not_enough_for_validation_test(self):
        from navin.quality.verification_log import record_verification

        record_verification(
            self.project, source="verify", ok=True, tests_ran=False,
        )
        with self.assertRaises(BoardError) as ctx:
            self._close(self._gated_task(validation="test"))
        self.assertIn("did not run any tests", str(ctx.exception))

    def test_humans_close_cards_without_a_recorded_run(self):
        task = self._gated_task()
        closed = self._close(task, actor_type="human")
        self.assertEqual(closed["status"], "done")


class MilestoneTest(_StoreTestBase):
    def test_milestone_crud_and_task_detach(self):
        milestone = self.store.create_milestone(
            title="v1.0", target_date="2026-09-01", actor="a", actor_type="agent",
        )
        task = self.store.create_task(
            title="x", milestone_id=milestone["id"], actor="a", actor_type="agent",
        )
        self.assertEqual(self.store.get_task(task["id"])["milestone_id"], milestone["id"])
        self.store.update_milestone(
            milestone["id"], fields={"status": "active"}, actor="a", actor_type="agent",
        )
        self.assertEqual(self.store.read_milestones()[0]["status"], "active")
        self.store.delete_milestone(milestone["id"], actor="a", actor_type="agent")
        self.assertEqual(self.store.read_milestones(), [])
        self.assertIsNone(self.store.get_task(task["id"])["milestone_id"])


class ActivityTest(_StoreTestBase):
    def test_activity_newest_first(self):
        self.store.create_task(title="first", actor="a", actor_type="agent")
        self.store.create_task(title="second", actor="a", actor_type="agent")
        activity = self.store.read_activity()
        self.assertEqual(activity[0]["detail"], "second")
        self.assertEqual(activity[1]["detail"], "first")

    def test_payload_shape(self):
        payload = self.store.payload()
        for key in ("tasks", "milestones", "activity", "statuses", "priorities"):
            self.assertIn(key, payload)


class _FakeScope:
    def __init__(self, project_path: Path):
        self.project_path = project_path


class BoardApiTest(_StoreTestBase):
    def test_http_edits_are_always_human(self):
        payload = board_update_payload(
            _FakeScope(self.project),
            {
                "action": "create_task",
                "actor": "aymen",
                "task": {"title": "From UI", "status": "planned"},
            },
        )
        self.assertEqual(len(payload["tasks"]), 1)
        self.assertEqual(payload["tasks"][0]["created_by"], {"type": "human", "name": "aymen"})
        self.assertEqual(payload["activity"][0]["actor_type"], "human")

    def test_unknown_action_rejected(self):
        with self.assertRaises(BoardError):
            board_update_payload(_FakeScope(self.project), {"action": "drop_all"})

    def test_pause_resume_and_update_mission(self):
        from navin.board.ledger import MissionLedgerStore

        MissionLedgerStore(self.project).create(goal="Ship auth", actor="a")
        paused = board_update_payload(
            _FakeScope(self.project),
            {"action": "pause_mission", "reason": "human", "actor": "aymen"},
        )
        self.assertEqual(paused["mission"]["status"], "paused")
        self.assertEqual(paused["mission"]["pause_reason"], "human")
        edited = board_update_payload(
            _FakeScope(self.project),
            {
                "action": "update_mission",
                "actor": "aymen",
                "fields": {"constraints": ["Keep Google Auth"]},
            },
        )
        self.assertIn("Keep Google Auth", edited["mission"]["constraints"])
        self.assertEqual(edited["mission"]["history"][-1]["actor"], "human")
        resumed = board_update_payload(
            _FakeScope(self.project),
            {"action": "resume_mission", "actor": "aymen"},
        )
        self.assertEqual(resumed["mission"]["status"], "running")
        self.assertEqual(resumed["mission"]["pause_reason"], "")


class BoardToolTest(_StoreTestBase):
    def _run(self, coro):
        return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)

    def test_full_workflow(self):
        tool = BoardTool(workspace=self.project)

        async def scenario():
            created = await tool.execute(
                action="create", title="Audit auth", priority="high", actor="MainAgent",
            )
            task_id = created.split(" ")[2]
            await tool.execute(
                action="claim", task_id=task_id, actor="sec-worker", actor_type="subagent",
            )
            await tool.execute(
                action="comment", task_id=task_id, text="JWT alg none accepted",
                actor="sec-worker", actor_type="subagent",
            )
            return await tool.execute(action="move", task_id=task_id, status="review")

        result = self._run(scenario())
        self.assertIn("[review]", result)
        stored = self.store.read_tasks()[0]
        self.assertEqual(stored["assignee"], {"type": "subagent", "name": "sec-worker"})
        self.assertEqual(len(stored["comments"]), 1)

    def test_invalid_status_returns_error_result(self):
        tool = BoardTool(workspace=self.project)

        async def scenario():
            created = await tool.execute(action="create", title="x", actor="a")
            task_id = created.split(" ")[2]
            return await tool.execute(action="move", task_id=task_id, status="bogus")

        result = self._run(scenario())
        self.assertTrue(getattr(result, "is_error", False))

    def test_closing_a_step_twice_is_named_not_rewritten(self):
        """Measured on a real /forge: models re-closed done steps, one model
        step each time. The second close must say so in one line, so the
        model stops instead of finding a third phrasing."""
        tool = BoardTool(workspace=self.project)

        async def scenario():
            created = await tool.execute(action="create", title="x", actor="a")
            task_id = created.split(" ")[2]
            first = await tool.execute(
                action="move", task_id=task_id, status="done", evidence="tests green",
            )
            second = await tool.execute(
                action="move", task_id=task_id, status="done", evidence="tests still green",
            )
            return first, second, task_id

        first, second, task_id = self._run(scenario())
        self.assertIn("Task moved", first)
        self.assertIn("already done", second)
        self.assertIn("Report each step once", second)
        stored = self.store.get_task(task_id)
        self.assertEqual(stored["evidence"], "tests green")

    def test_a_done_step_without_evidence_still_accepts_it(self):
        tool = BoardTool(workspace=self.project)

        async def scenario():
            created = await tool.execute(action="create", title="x", actor="a")
            task_id = created.split(" ")[2]
            await tool.execute(action="move", task_id=task_id, status="done")
            return await tool.execute(
                action="move", task_id=task_id, status="done", evidence="12 tests OK",
            ), task_id

        result, task_id = self._run(scenario())
        self.assertIn("Task moved", result)
        self.assertEqual(self.store.get_task(task_id)["evidence"], "12 tests OK")

    def test_activity_file_is_jsonl(self):
        tool = BoardTool(workspace=self.project)

        async def scenario():
            await tool.execute(action="log", text="nightly audit done", actor="MainAgent")

        self._run(scenario())
        lines = self.store.activity_path.read_text(encoding="utf-8").strip().splitlines()
        entry = json.loads(lines[-1])
        self.assertEqual(entry["kind"], "note")
        self.assertEqual(entry["actor"], "MainAgent")


if __name__ == "__main__":
    unittest.main()
