"""Tests for the plan the chat shows while a run is in flight.

The board already held the plan and re-injected it into the prompt every turn,
but the conversation never showed it: a long run said "Thinking" and nothing
else, and reading the plan meant leaving the chat for the kanban tab. These cover
the two pieces that close that gap - remembering which tasks a run touched, and
narrowing the board down to them.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from navin.agent.tools.board import BoardTool
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.board import session_focus
from navin.board.plan import plan_quality, session_plan
from navin.board.store import ProjectBoardStore
from navin.webui.board_api import board_payload, board_update_payload


class _FakeScope:
    """Only ``project_path`` is read by the board payload builders."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path


def _task(
    task_id: str,
    *,
    title: str | None = None,
    status: str = "backlog",
    priority: str = "medium",
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": title or f"Task {task_id}",
        "status": status,
        "priority": priority,
        "depends_on": depends_on or [],
    }


class SessionFocusTest(unittest.TestCase):
    def setUp(self) -> None:
        session_focus.clear()
        self.addCleanup(session_focus.clear)

    def test_a_session_starts_with_nothing(self) -> None:
        self.assertEqual(session_focus.touched("websocket:c1"), [])

    def test_touch_order_is_kept(self) -> None:
        session_focus.remember("websocket:c1", "t-1")
        session_focus.remember("websocket:c1", ["t-2", "t-3"])
        self.assertEqual(session_focus.touched("websocket:c1"), ["t-1", "t-2", "t-3"])

    def test_touching_a_task_again_does_not_move_it(self) -> None:
        """The panel reads top to bottom, so an update must not reorder the plan."""
        session_focus.remember("websocket:c1", ["t-1", "t-2"])
        session_focus.remember("websocket:c1", "t-1")
        self.assertEqual(session_focus.touched("websocket:c1"), ["t-1", "t-2"])

    def test_sessions_do_not_see_each_other(self) -> None:
        session_focus.remember("websocket:c1", "t-1")
        session_focus.remember("websocket:c2", "t-2")
        self.assertEqual(session_focus.touched("websocket:c1"), ["t-1"])
        self.assertEqual(session_focus.touched("websocket:c2"), ["t-2"])

    def test_a_missing_session_key_is_ignored(self) -> None:
        """Not every caller runs inside a request, and that is not an error."""
        session_focus.remember(None, "t-1")
        session_focus.remember("  ", "t-1")
        self.assertEqual(session_focus.touched(None), [])

    def test_junk_ids_are_dropped(self) -> None:
        session_focus.remember("websocket:c1", [None, 3, "t-1", ""])  # type: ignore[list-item]
        self.assertEqual(session_focus.touched("websocket:c1"), ["t-1"])

    def test_forget_drops_one_session_only(self) -> None:
        session_focus.remember("websocket:c1", "t-1")
        session_focus.remember("websocket:c2", "t-2")
        session_focus.forget("websocket:c1")
        self.assertEqual(session_focus.touched("websocket:c1"), [])
        self.assertEqual(session_focus.touched("websocket:c2"), ["t-2"])

    def test_a_long_run_cannot_grow_without_bound(self) -> None:
        session_focus.remember("websocket:c1", [f"t-{i}" for i in range(200)])
        kept = session_focus.touched("websocket:c1")
        self.assertEqual(len(kept), 40)
        self.assertEqual(kept[-1], "t-199")

    def test_a_long_lived_gateway_cannot_grow_without_bound(self) -> None:
        for i in range(200):
            session_focus.remember(f"websocket:c{i}", "t-1")
        self.assertEqual(session_focus.touched("websocket:c0"), [])
        self.assertEqual(session_focus.touched("websocket:c199"), ["t-1"])

    def test_the_returned_list_is_a_copy(self) -> None:
        session_focus.remember("websocket:c1", "t-1")
        session_focus.touched("websocket:c1").append("t-hack")
        self.assertEqual(session_focus.touched("websocket:c1"), ["t-1"])


class SessionPlanTest(unittest.TestCase):
    def test_an_untouched_board_has_no_plan(self) -> None:
        """None rather than an empty plan: the panel should not render at all."""
        self.assertIsNone(session_plan([_task("t-1")], []))

    def test_only_touched_tasks_appear(self) -> None:
        tasks = [_task("t-1"), _task("t-2"), _task("t-3")]
        plan = session_plan(tasks, ["t-1", "t-3"])
        assert plan is not None
        self.assertEqual([item["id"] for item in plan["items"]], ["t-1", "t-3"])
        self.assertEqual(plan["total_count"], 2)

    def test_items_follow_touch_order_not_board_order(self) -> None:
        tasks = [_task("t-1"), _task("t-2")]
        plan = session_plan(tasks, ["t-2", "t-1"])
        assert plan is not None
        self.assertEqual([item["id"] for item in plan["items"]], ["t-2", "t-1"])

    def test_a_task_deleted_since_it_was_touched_is_dropped(self) -> None:
        plan = session_plan([_task("t-1")], ["t-1", "t-gone"])
        assert plan is not None
        self.assertEqual([item["id"] for item in plan["items"]], ["t-1"])

    def test_a_plan_of_only_deleted_tasks_is_no_plan(self) -> None:
        self.assertIsNone(session_plan([_task("t-1")], ["t-gone"]))

    def test_done_is_counted_and_marked(self) -> None:
        tasks = [_task("t-1", status="done"), _task("t-2")]
        plan = session_plan(tasks, ["t-1", "t-2"])
        assert plan is not None
        self.assertEqual(plan["done_count"], 1)
        self.assertEqual(plan["total_count"], 2)
        self.assertTrue(plan["items"][0]["done"])
        self.assertFalse(plan["items"][1]["done"])
        self.assertFalse(plan["complete"])

    def test_the_heading_names_the_task_in_flight(self) -> None:
        tasks = [
            _task("t-1", status="done"),
            _task("t-2", title="Wire the panel", status="in_progress"),
            _task("t-3"),
        ]
        plan = session_plan(tasks, ["t-1", "t-2", "t-3"])
        assert plan is not None
        self.assertEqual(plan["current_id"], "t-2")
        self.assertEqual(plan["title"], "Wire the panel")

    def test_review_and_audit_also_count_as_in_flight(self) -> None:
        for status in ("review", "audit"):
            plan = session_plan([_task("t-1", status=status)], ["t-1"])
            assert plan is not None
            self.assertTrue(plan["items"][0]["active"], status)
            self.assertEqual(plan["current_id"], "t-1")

    def test_with_nothing_in_flight_the_first_unfinished_task_leads(self) -> None:
        tasks = [_task("t-1", status="done"), _task("t-2"), _task("t-3")]
        plan = session_plan(tasks, ["t-1", "t-2", "t-3"])
        assert plan is not None
        self.assertEqual(plan["current_id"], "t-2")

    def test_a_finished_plan_says_so(self) -> None:
        tasks = [_task("t-1", status="done"), _task("t-2", status="done")]
        plan = session_plan(tasks, ["t-1", "t-2"])
        assert plan is not None
        self.assertTrue(plan["complete"])
        self.assertEqual(plan["done_count"], 2)
        # Nothing is in flight, so the heading falls back to the last item rather
        # than pointing at a task the run already closed.
        self.assertEqual(plan["current_id"], "t-2")

    def test_an_unmet_dependency_marks_the_task_blocked(self) -> None:
        tasks = [_task("t-1"), _task("t-2", depends_on=["t-1"])]
        plan = session_plan(tasks, ["t-1", "t-2"])
        assert plan is not None
        self.assertFalse(plan["items"][0]["blocked"])
        self.assertTrue(plan["items"][1]["blocked"])

    def test_a_parked_task_is_blocked_too(self) -> None:
        plan = session_plan([_task("t-1", status="blocked")], ["t-1"])
        assert plan is not None
        self.assertTrue(plan["items"][0]["blocked"])

    def test_a_dependency_met_outside_the_plan_does_not_block(self) -> None:
        """Blocking is judged against the whole board, not the focused subset.

        Only ``t-2`` is in the panel, but what it waits on was finished in an
        earlier session; reading the subset alone would call that dependency
        missing and show a ready task as blocked.
        """
        tasks = [_task("t-1", status="done"), _task("t-2", depends_on=["t-1"])]
        plan = session_plan(tasks, ["t-2"])
        assert plan is not None
        self.assertFalse(plan["items"][0]["blocked"])

    def test_a_dependency_on_nothing_still_blocks(self) -> None:
        """A dangling edge can never be satisfied, so the task never becomes ready."""
        plan = session_plan([_task("t-2", depends_on=["t-gone"])], ["t-2"])
        assert plan is not None
        self.assertTrue(plan["items"][0]["blocked"])


class PlanQualityTest(unittest.TestCase):
    """P2-7: the pre-Build judge names concrete gaps, or says the plan is ready."""

    @staticmethod
    def _entry(
        task_id: str = "t-1",
        *,
        acceptance: str = "tests pass",
        validation: str = "verify",
        done: bool = False,
        cancelled: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": task_id,
            "title": f"Task {task_id}",
            "acceptance": acceptance,
            "validation": validation,
            "done": done,
            "cancelled": cancelled,
        }

    @staticmethod
    def _mission(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "goal": "Ship the auth revamp",
            "acceptance_criteria": ["login works", "tests green"],
            "missing_info": [],
        }
        base.update(overrides)
        return base

    def test_a_complete_plan_is_ready(self) -> None:
        quality = plan_quality([self._entry()], self._mission())
        self.assertEqual(quality["status"], "ready")
        self.assertEqual(quality["gaps"], [])

    def test_a_task_without_acceptance_is_a_gap(self) -> None:
        quality = plan_quality([self._entry(acceptance="")], self._mission())
        self.assertEqual(quality["status"], "gaps")
        kinds = [gap["kind"] for gap in quality["gaps"]]
        self.assertIn("task_acceptance_missing", kinds)

    def test_a_task_without_validation_is_a_gap(self) -> None:
        quality = plan_quality([self._entry(validation="none")], self._mission())
        kinds = [gap["kind"] for gap in quality["gaps"]]
        self.assertIn("task_validation_missing", kinds)

    def test_a_missing_ledger_is_a_gap(self) -> None:
        quality = plan_quality([self._entry()], None)
        kinds = [gap["kind"] for gap in quality["gaps"]]
        self.assertIn("ledger_missing", kinds)

    def test_empty_acceptance_criteria_on_the_ledger_is_a_gap(self) -> None:
        """P2-7 acceptance: a plan without acceptance_criteria warns before Build."""
        quality = plan_quality(
            [self._entry()], self._mission(acceptance_criteria=[])
        )
        self.assertEqual(quality["status"], "gaps")
        kinds = [gap["kind"] for gap in quality["gaps"]]
        self.assertIn("acceptance_criteria_missing", kinds)

    def test_open_missing_info_is_a_gap(self) -> None:
        quality = plan_quality(
            [self._entry()], self._mission(missing_info=["which SSO provider?"])
        )
        kinds = [gap["kind"] for gap in quality["gaps"]]
        self.assertIn("missing_info_open", kinds)
        messages = " ".join(gap["message"] for gap in quality["gaps"])
        self.assertIn("which SSO provider?", messages)

    def test_done_and_cancelled_tasks_are_not_judged(self) -> None:
        quality = plan_quality(
            [
                self._entry("t-1", acceptance="", done=True),
                self._entry("t-2", acceptance="", cancelled=True),
            ],
            self._mission(),
        )
        self.assertEqual(quality["status"], "ready")

    def test_session_plan_carries_the_judge_verdict(self) -> None:
        tasks = [_task("t-1")]
        plan = session_plan(tasks, ["t-1"])
        assert plan is not None
        self.assertIn("quality", plan)
        # A bare task with no acceptance/validation and no ledger has gaps.
        self.assertEqual(plan["quality"]["status"], "gaps")

    def test_session_plan_exposes_mission_progress_and_budget(self) -> None:
        tasks = [_task("t-1")]
        mission = {
            "goal": "Ship it",
            "version": 4,
            "status": "paused",
            "pause_reason": "budget",
            "acceptance_criteria": ["Works"],
            "progress": {
                "stall_count": 2,
                "loop_detected": True,
                "replan_needed": False,
                "budget": {"token_budget": 1000, "tokens_used": 250},
            },
            "history": [{"version": 4, "reason": "paused: budget"}],
        }
        plan = session_plan(tasks, ["t-1"], mission=mission)
        assert plan is not None
        self.assertEqual(plan["goal"], "Ship it")
        self.assertEqual(plan["version"], 4)
        self.assertEqual(plan["ledger_status"], "paused")
        self.assertEqual(plan["stall_count"], 2)
        self.assertTrue(plan["loop_detected"])
        self.assertEqual(plan["pause_reason"], "budget")
        self.assertEqual(plan["token_budget"], 1000)
        self.assertEqual(plan["tokens_used"], 250)
        self.assertEqual(plan["last_change"]["reason"], "paused: budget")


class BoardToolSessionTest(unittest.IsolatedAsyncioTestCase):
    """The tool has to attribute its mutations, or the panel stays empty."""

    def setUp(self) -> None:
        session_focus.clear()
        self.addCleanup(session_focus.clear)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.tool = BoardTool(workspace=self.project)
        self.key = "websocket:c1"
        token = bind_request_context(
            RequestContext(channel="websocket", chat_id="c1", session_key=self.key)
        )
        self.addCleanup(lambda: reset_request_context(token))

    async def _run(self, **kwargs: Any) -> str:
        result = await self.tool.execute(**kwargs)
        return result if isinstance(result, str) else str(getattr(result, "content", result))

    async def test_creating_a_task_puts_it_in_the_session_plan(self) -> None:
        await self._run(action="create", title="Ship the panel")
        touched = session_focus.touched(self.key)
        self.assertEqual(len(touched), 1)
        plan = board_payload(self._scope(),  # type: ignore[arg-type]
                self.key)["session_plan"]
        self.assertEqual(plan["title"], "Ship the panel")
        self.assertEqual(plan["total_count"], 1)

    async def test_claiming_and_closing_move_the_panel_along(self) -> None:
        await self._run(action="create", title="Step one")
        task_id = session_focus.touched(self.key)[0]
        await self._run(action="claim", task_id=task_id)
        plan = board_payload(self._scope(),  # type: ignore[arg-type]
                self.key)["session_plan"]
        self.assertTrue(plan["items"][0]["active"])
        self.assertEqual(plan["done_count"], 0)

        await self._run(action="move", task_id=task_id, status="done")
        plan = board_payload(self._scope(),  # type: ignore[arg-type]
                self.key)["session_plan"]
        self.assertEqual(plan["done_count"], 1)
        self.assertTrue(plan["complete"])

    async def test_commenting_counts_as_touching(self) -> None:
        store = ProjectBoardStore(self.project)
        task = store.create_task(title="Filed elsewhere", actor="human", actor_type="human")
        await self._run(action="comment", task_id=task["id"], text="looked at it")
        self.assertEqual(session_focus.touched(self.key), [task["id"]])

    async def test_reading_the_board_does_not_claim_ownership(self) -> None:
        """`list` and `next` are how an agent looks around; that is not a plan."""
        ProjectBoardStore(self.project).create_task(
            title="Someone else's task", actor="human", actor_type="human"
        )
        await self._run(action="list")
        await self._run(action="next")
        await self._run(action="plan")
        self.assertEqual(session_focus.touched(self.key), [])
        self.assertIsNone(board_payload(self._scope(),  # type: ignore[arg-type]
                self.key)["session_plan"])

    async def test_a_failed_mutation_is_not_remembered(self) -> None:
        await self._run(action="claim", task_id="t-nope")
        self.assertEqual(session_focus.touched(self.key), [])

    async def test_another_conversation_sees_its_own_plan_only(self) -> None:
        await self._run(action="create", title="Mine")
        self.assertIsNone(board_payload(self._scope(),  # type: ignore[arg-type]
                "websocket:other")["session_plan"])

    async def test_a_task_the_agent_deletes_leaves_the_panel(self) -> None:
        await self._run(action="create", title="Wrong turn")
        task_id = session_focus.touched(self.key)[0]
        await self._run(action="delete", task_id=task_id)
        self.assertIsNone(board_payload(self._scope(),  # type: ignore[arg-type]
                self.key)["session_plan"])

    def _scope(self) -> _FakeScope:
        return _FakeScope(self.project)


class HumanEditTest(unittest.TestCase):
    """A human dragging a card refreshes the panel but does not join the plan."""

    def setUp(self) -> None:
        session_focus.clear()
        self.addCleanup(session_focus.clear)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.key = "websocket:c1"

    def _scope(self) -> _FakeScope:
        return _FakeScope(self.project)

    def test_a_human_created_task_does_not_enter_the_panel(self) -> None:
        payload = board_update_payload(
            self._scope(),  # type: ignore[arg-type]
            {"action": "create_task", "task": {"title": "From the kanban"}}, self.key
        )
        self.assertIsNone(payload["session_plan"])

    def test_a_human_closing_the_agent_s_task_shows_up_in_the_panel(self) -> None:
        store = ProjectBoardStore(self.project)
        task = store.create_task(title="Agent work", actor="agent", actor_type="agent")
        session_focus.remember(self.key, task["id"])

        payload = board_update_payload(
            self._scope(),
            {"action": "update_task", "task_id": task["id"], "fields": {"status": "done"}},
            self.key,
        )
        self.assertEqual(payload["session_plan"]["done_count"], 1)
        self.assertTrue(payload["session_plan"]["complete"])


class TrackedBriefTest(unittest.TestCase):
    """Every Code-module action must ask for a plan, updates and a report."""

    # A concrete focus with a build verb: actionable for strict (/forge,
    # /cruise) and scoped workflows alike. Greetings must NOT arm the
    # tracked/board clause (that clause forces tool use on a plain hello).
    _FOCUS = "fix the auth module"

    def _brief(self, command: str, focus: str | None = None) -> str:
        import asyncio
        from types import SimpleNamespace

        from navin.command.builtin import _workflow_handler

        args = self._FOCUS if focus is None else focus
        raw = f"{command} {args}".strip()
        msg = SimpleNamespace(content="", metadata={})
        ctx = SimpleNamespace(args=args, raw=raw, msg=msg)
        asyncio.run(_workflow_handler(command)(ctx))  # type: ignore[arg-type]
        return msg.content

    def test_an_audit_is_told_to_plan_track_and_report(self) -> None:
        brief = self._brief("/fortify")
        self.assertIn("board", brief)
        self.assertIn("claim", brief)
        self.assertIn("report", brief)

    def test_every_tracked_command_carries_the_clause(self) -> None:
        from navin.command.builtin import _TRACKED_RUN_CLAUSE, _TRACKED_WORKFLOWS

        for command in sorted(_TRACKED_WORKFLOWS):
            self.assertIn(_TRACKED_RUN_CLAUSE, self._brief(command), command)

    def test_greeting_focus_does_not_arm_the_clause(self) -> None:
        from navin.command.builtin import _TRACKED_RUN_CLAUSE, _TRACKED_WORKFLOWS

        for command in sorted(_TRACKED_WORKFLOWS):
            for focus in ("", "hello", "salut"):
                self.assertNotIn(
                    _TRACKED_RUN_CLAUSE,
                    self._brief(command, focus),
                    f"{command} {focus!r}",
                )

    def test_every_tracked_command_loads_the_board_skill(self) -> None:
        """The clause asks for a plan; the skill says what a good one looks like."""
        from navin.command.builtin import _TRACKED_WORKFLOWS

        for command in sorted(_TRACKED_WORKFLOWS):
            self.assertIn("project-board", self._brief(command), command)

    def test_tracked_commands_all_exist(self) -> None:
        from navin.command.builtin import _TRACKED_WORKFLOWS, _WORKFLOW_BRIEFS

        self.assertEqual(_TRACKED_WORKFLOWS - set(_WORKFLOW_BRIEFS), set())

    def test_plan_only_and_reporting_modes_stay_out(self) -> None:
        """/blueprint files a plan it must not start executing; /board is the board."""
        from navin.command.builtin import _TRACKED_RUN_CLAUSE, _TRACKED_WORKFLOWS

        for command in ("/blueprint", "/board", "/report"):
            self.assertNotIn(_TRACKED_RUN_CLAUSE, self._brief(command), command)
        self.assertNotIn("/blueprint", _TRACKED_WORKFLOWS)


if __name__ == "__main__":
    unittest.main()
