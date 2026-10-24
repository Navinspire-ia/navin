# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The chat plan panel advances during a run without relying on the model alone.

Rules live in :mod:`navin.board.auto_advance` (R1-R4); these pin each one and
the gates that keep an auto-close honest: a step is only closed when work on
it really happened, and is left open whenever that is not certain.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from navin.agent.tool_demand import on_demand_tools_for_session
from navin.agent.tools.base import ToolResult
from navin.agent.tools.board import BoardTool, _with_note
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.board import auto_advance, session_focus
from navin.board.store import BoardError, ProjectBoardStore


def _work(name: str = "write_file", **fields: Any) -> dict[str, Any]:
    return {"name": name, "status": "ok", **fields}


class _Base(unittest.IsolatedAsyncioTestCase):
    composer_mode: str | None = None

    def setUp(self) -> None:
        session_focus.clear()
        auto_advance.reset_for_tests()
        self.addCleanup(session_focus.clear)
        self.addCleanup(auto_advance.reset_for_tests)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.tool = BoardTool(workspace=self.project)
        self.key = "websocket:c1"
        # The turn's ordered tool events, as the runner would report them.
        self.events: list[dict[str, Any]] = []
        metadata = {"composer_mode": self.composer_mode} if self.composer_mode else {}
        token = bind_request_context(
            RequestContext(channel="websocket", chat_id="c1", session_key=self.key, metadata=metadata)
        )
        self.addCleanup(lambda: reset_request_context(token))

    async def _run(self, **kwargs: Any) -> str:
        result = await self.tool.execute(**kwargs)
        self.events.append({"name": "board", "status": "ok"})
        return result if isinstance(result, str) else str(getattr(result, "content", result))

    def _did(self, *events: dict[str, Any]) -> None:
        self.events.extend(events or (_work(),))

    def _status(self, task_id: str) -> str:
        return ProjectBoardStore(self.project).get_task(task_id)["status"]

    async def _plan(self, *titles: str, **fields: Any) -> list[str]:
        for title in titles:
            await self._run(action="create", title=title, **fields)
        return session_focus.touched(self.key)

    def _finish(self, **overrides: Any) -> str | None:
        kwargs: dict[str, Any] = {
            "stop_reason": "completed",
            "final_content": "Done: the parser now handles empty input.",
            "tool_events": list(self.events),
        }
        kwargs.update(overrides)
        self.events = []
        return auto_advance.finish_turn(self.project, self.key, **kwargs)


class ToolRulesTest(_Base):
    async def test_r1_filing_a_plan_claims_its_first_step(self) -> None:
        a, b, c = await self._plan("One", "Two", "Three")
        self.assertEqual(
            [self._status(a), self._status(b), self._status(c)],
            ["in_progress", "backlog", "backlog"],
        )
        self.assertTrue(auto_advance.owned_tasks(self.key)[a]["auto"])

    async def test_r2_closing_a_step_claims_the_next(self) -> None:
        a, b, _c = await self._plan("One", "Two", "Three")
        out = await self._run(action="move", task_id=a, status="done")
        self.assertEqual(self._status(a), "done")
        self.assertEqual(self._status(b), "in_progress")
        self.assertIn(f"{b} claimed", out)
        self.assertNotIn(a, auto_advance.owned_tasks(self.key))

    async def test_r2_skips_a_step_whose_dependency_is_open(self) -> None:
        a, _b = await self._plan("One", "Two")
        store = ProjectBoardStore(self.project)
        blocker = store.create_task(title="Elsewhere", actor="human", actor_type="human")
        await self._run(action="create", title="Three")
        c = session_focus.touched(self.key)[-1]
        store.update_task(_b, fields={"depends_on": [blocker["id"]]}, actor="human", actor_type="human")
        await self._run(action="move", task_id=a, status="done")
        self.assertEqual(self._status(_b), "backlog")
        self.assertEqual(self._status(c), "in_progress")

    async def test_r2_skips_a_step_assigned_to_a_human(self) -> None:
        a, b, c = await self._plan("One", "Two", "Three")
        store = ProjectBoardStore(self.project)
        store.update_task(
            b, fields={"assignee": {"type": "human", "name": "bob"}}, actor="bob", actor_type="human",
        )
        await self._run(action="move", task_id=a, status="done")
        task = store.get_task(b)
        self.assertEqual(task["status"], "backlog")
        self.assertEqual(task["assignee"]["name"], "bob")
        self.assertEqual(self._status(c), "in_progress")

    async def test_r1_never_claims_a_task_the_session_only_commented_on(self) -> None:
        store = ProjectBoardStore(self.project)
        h = store.create_task(title="Human idea", actor="bob", actor_type="human")["id"]
        await self._run(action="comment", task_id=h, text="looks good")
        await self._run(action="create", title="Mine")
        mine = session_focus.touched(self.key)[-1]
        self.assertEqual(self._status(h), "backlog")
        self.assertEqual(self._status(mine), "in_progress")

    async def test_r3_moving_on_closes_the_step_the_model_worked_on(self) -> None:
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did()
        out = await self._run(action="claim", task_id=b)
        self.assertIn(f"{a} is still in_progress", out)
        self.assertIsNone(self._finish())
        self.assertEqual(self._status(a), "done")
        # b was claimed with nothing done after it: it stays open.
        self.assertEqual(self._status(b), "in_progress")

    async def test_r3_a_model_claimed_step_without_work_stays_open(self) -> None:
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        await self._run(action="claim", task_id=b)
        self._did()
        self._finish()
        self.assertEqual(self._status(a), "in_progress")

    async def test_r3_an_auto_claimed_step_the_model_skipped_goes_back(self) -> None:
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=b)
        self._did()
        self._finish()
        self.assertEqual(self._status(a), "planned")
        # b was claimed by the model and worked on: R4 closes it.
        self.assertEqual(self._status(b), "done")

    async def test_r3_an_auto_claimed_step_the_model_worked_on_is_closed(self) -> None:
        # Scenario C: R2 claims b, the model works on it, then claims c.
        a, b, c = await self._plan("One", "Two", "Three")
        await self._run(action="move", task_id=a, status="done")
        self._did(_work("edit_file"))
        await self._run(action="claim", task_id=c)
        self._finish()
        self.assertEqual(self._status(b), "done")
        self.assertEqual(self._status(c), "in_progress")

    async def test_r3_work_from_an_earlier_turn_counts(self) -> None:
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did()
        self._finish(final_content="Which format do you want?")
        self.assertEqual(self._status(a), "in_progress")
        await self._run(action="claim", task_id=b)
        self._finish()
        self.assertEqual(self._status(a), "done")

    async def test_r3_respects_pending_validation(self) -> None:
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did()
        await self._run(action="claim", task_id=b)
        self._finish(validation_is_pending=True)
        self.assertEqual(self._status(a), "in_progress")

    async def test_r3_needs_a_recorded_green_run_for_acceptance_steps(self) -> None:
        a, b = await self._plan("One", "Two", acceptance="parser returns [] on empty input")
        await self._run(action="claim", task_id=a)
        self._did()
        await self._run(action="claim", task_id=b)
        self._finish()
        self.assertEqual(self._status(a), "in_progress")

        from navin.quality.verification_log import record_verification

        self._did()
        a2 = (await self._plan("Three", acceptance="x"))[-1]
        record_verification(self.project, ok=True, source="verify", summary="12 passed")
        await self._run(action="claim", task_id=a2)
        self._finish()
        task = ProjectBoardStore(self.project).get_task(b)
        self.assertEqual(task["status"], "done")
        self.assertIn("12 passed", task["evidence"])

    async def test_r3_never_closes_a_step_in_review(self) -> None:
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did()
        await self._run(action="move", task_id=a, status="review")
        await self._run(action="claim", task_id=b)
        self._did()
        self._finish()
        self.assertEqual(self._status(a), "review")

    async def test_r3_leaves_a_foreign_task_alone(self) -> None:
        # Scenario E: a human's in-progress task the session commented on.
        store = ProjectBoardStore(self.project)
        h = store.create_task(title="Human work", actor="bob", actor_type="human")["id"]
        store.claim_task(h, actor="bob", actor_type="human")
        await self._run(action="comment", task_id=h, text="fyi")
        x = (await self._plan("Mine"))[-1]
        self._did()
        out = await self._run(action="claim", task_id=x)
        self.assertNotIn(h, out.splitlines()[-1])
        self._did()
        self._finish()
        self.assertEqual(self._status(h), "in_progress")
        self.assertEqual(store.get_task(h)["assignee"]["name"], "bob")

    async def test_subagents_do_not_drive_the_parent_plan(self) -> None:
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did()
        await self._run(action="claim", task_id=b, actor_type="subagent")
        # The turn ends on a question, so only a (wrong) R3 could close a.
        self._finish(final_content="Which option do you want?")
        self.assertEqual(self._status(a), "in_progress")

    async def test_a_subagent_context_without_actor_type_is_ignored(self) -> None:
        # Scenario G: the subagent's own RequestContext carries the marker.
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did()
        token = bind_request_context(
            RequestContext(
                channel="websocket", chat_id="c1", session_key=self.key, metadata={"subagent": True},
            )
        )
        try:
            # Runs in the subagent's own runner: not one of the parent's events.
            out = await self.tool.execute(action="claim", task_id=b)
        finally:
            reset_request_context(token)
        self.assertNotIn("Plan auto-advance", out)
        # The step the subagent took is no longer the parent's to close.
        self.assertNotIn(b, auto_advance.owned_tasks(self.key))
        # The parent asks a question, so only a (wrong) R3 could close a.
        self._finish(final_content="Which option do you want?")
        self.assertEqual(self._status(a), "in_progress")

    async def test_the_note_survives_a_tool_result(self) -> None:
        result = ToolResult("Task moved", made_progress=True)
        noted = _with_note(result, "Plan auto-advance: x.")
        self.assertIsInstance(noted, ToolResult)
        self.assertTrue(noted.endswith("Plan auto-advance: x."))
        self.assertTrue(noted.made_progress)
        self.assertEqual(_with_note("ok", "n"), "ok\nn")


class PlanModeTest(_Base):
    composer_mode = "plan"

    async def test_plan_mode_files_without_claiming(self) -> None:
        a, _b = await self._plan("One", "Two")
        self.assertEqual(self._status(a), "backlog")
        # Still this session's plan once the user switches to Agent.
        self.assertIn(a, auto_advance.owned_tasks(self.key))


class TurnEndTest(_Base):
    async def test_r4_closes_a_step_the_model_claimed_and_worked_on(self) -> None:
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did()
        self.assertEqual(self._finish(), a)
        self.assertEqual(self._status(a), "done")
        # Nothing runs after the turn, so the next step is not claimed.
        self.assertEqual(self._status(b), "backlog")

    async def test_r4_never_closes_an_auto_claimed_step(self) -> None:
        # Scenario A: tests ran, the model closed a, R2 claimed b, turn ends.
        a, b, c = await self._plan("One", "Two", "Three")
        self._did(_work("exec", exit_code="0"))
        await self._run(action="move", task_id=a, status="done")
        self.assertIsNone(self._finish(final_content="One is done. Next: Two."))
        self.assertEqual([self._status(a), self._status(b), self._status(c)], ["done", "in_progress", "backlog"])

    async def test_r4_the_turn_that_files_the_plan_closes_nothing(self) -> None:
        # Scenario B.
        a, b = await self._plan("One", "Two")
        self._did(_work("read_file"))
        self.assertIsNone(self._finish(final_content="Plan filed. I will start with One next."))
        self.assertEqual(self._status(a), "in_progress")
        self.assertEqual(self._status(b), "backlog")

    async def test_reading_during_the_plan_turn_is_not_work_on_step_one(self) -> None:
        a, b = await self._plan("One", "Two")
        self._did(_work("read_file"), _work("grep"))
        self._finish(final_content="Plan filed.")
        self.assertFalse(auto_advance.owned_tasks(self.key)[a]["worked"])
        # Next turn the model goes straight to Two: One was never worked.
        await self._run(action="claim", task_id=b)
        self._finish(final_content="Starting Two.")
        self.assertEqual(self._status(a), "planned")

    async def test_r4_reading_alone_does_not_close_a_step(self) -> None:
        a, _b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did(_work("read_file"))
        self.assertIsNone(self._finish(final_content="One needs a schema change; I will do it next."))
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_needs_work_after_the_step_became_active(self) -> None:
        a, _b = await self._plan("One", "Two")
        self._did()
        await self._run(action="claim", task_id=a)
        self.assertIsNone(self._finish())
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_claiming_the_next_step_at_the_end_does_not_close_it(self) -> None:
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did()
        await self._run(action="claim", task_id=b)
        self.assertIsNone(self._finish())
        self.assertEqual(self._status(a), "done")
        self.assertEqual(self._status(b), "in_progress")

    async def test_r4_a_step_claimed_in_an_earlier_turn(self) -> None:
        a, _b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._finish(final_content="Which format do you want?")
        self._did()
        self.assertEqual(self._finish(), a)

    async def _worked_turn(self, task_id: str) -> None:
        """A turn where the model (re)claims *task_id* and works on it."""
        await self._run(action="claim", task_id=task_id)
        self._did()

    async def test_r4_needs_completed_stop(self) -> None:
        a, _b = await self._plan("One", "Two")
        for reason in ("max_iterations", "tool_error", "validation_failed", "error", "no_progress"):
            await self._worked_turn(a)
            self.assertIsNone(self._finish(stop_reason=reason))
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_needs_non_board_work(self) -> None:
        a, _b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did({"name": "message", "status": "ok"})
        self.assertIsNone(self._finish())
        self.assertIsNone(self._finish(tool_events=[]))
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_refuses_when_the_last_tool_failed(self) -> None:
        a, _b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did(_work(), {"name": "exec", "status": "error"})
        self.assertIsNone(self._finish())
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_a_non_zero_exec_is_a_failure(self) -> None:
        # Scenario F.
        a, _b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did(_work("exec", exit_code="1"))
        self.assertIsNone(self._finish(final_content="Ran the suite."))
        self.assertEqual(self._status(a), "in_progress")
        # A failing exec is not work either.
        await self._run(action="claim", task_id=a)
        self._did(_work("exec", exit_code="2"), _work("read_file", status="error"))
        self.assertIsNone(self._finish())
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_an_answer_that_reports_failure_closes_nothing(self) -> None:
        a, _b = await self._plan("One", "Two")
        for text in (
            "Tests still fail in parser.py.",
            "I couldn't reproduce the bug.",
            "Je n'ai pas pu lancer les tests.",
            "Le build est en échec.",
        ):
            await self._worked_turn(a)
            self.assertIsNone(self._finish(final_content=text))
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_respects_pending_validation_and_modes(self) -> None:
        a, _b = await self._plan("One", "Two")
        for extra in ({"validation_is_pending": True}, {"composer_mode": "plan"}, {"read_only": True}):
            await self._worked_turn(a)
            self.assertIsNone(self._finish(**extra))
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_skips_unrelated_turns(self) -> None:
        a, _b = await self._plan("One", "Two")
        for extra in (
            {"metadata": {"_cron_trigger": {"job_id": "j1"}}},
            {"metadata": {"heartbeat": True}},
            {"metadata": {"subagent": True}},
            {"sender_id": "subagent"},
            {"sender_id": "cron"},
        ):
            await self._worked_turn(a)
            self.assertIsNone(self._finish(**extra))
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_unknown_event_positions_close_nothing(self) -> None:
        a, _b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        # The events do not line up with the board calls of the turn.
        self.assertIsNone(self._finish(tool_events=[_work()]))
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_a_question_parks_the_auto_claimed_step(self) -> None:
        a, _b = await self._plan("One", "Two")
        self.assertIsNone(self._finish(final_content="Plan filed. Shall I start?"))
        self.assertEqual(self._status(a), "planned")

    async def test_r4_a_question_before_options_counts(self) -> None:
        # Scenario H.
        a, _b = await self._plan("One", "Two")
        for text in (
            "Which DB do you prefer?\n1. Postgres\n2. SQLite",
            "Which DB do you prefer?\n\n1. Postgres\n2. SQLite",
            "Two options are possible.\n\nLet me know which one you want.",
            "Deux options.\n\nDis-moi laquelle tu preferes.",
        ):
            await self._worked_turn(a)
            self.assertIsNone(self._finish(final_content=text))
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_a_question_leaves_a_model_claimed_step_alone(self) -> None:
        a, _b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self.assertIsNone(self._finish(final_content="Which database should I use?"))
        self.assertEqual(self._status(a), "in_progress")

    async def test_r4_publishes_a_board_update(self) -> None:
        a = (await self._plan("One"))[0]
        await self._run(action="claim", task_id=a)
        self._did()
        bus = mock.Mock()
        self._finish(bus=bus)
        bus.outbound.put_nowait.assert_called_once()

    async def test_r4_async_runs_off_the_loop_and_publishes(self) -> None:
        a = (await self._plan("One"))[0]
        await self._run(action="claim", task_id=a)
        self._did()
        bus = mock.Mock()
        closed = await auto_advance.finish_turn_async(
            self.project, self.key, stop_reason="completed",
            final_content="Done.", tool_events=list(self.events), bus=bus,
        )
        self.assertEqual(closed, a)
        bus.outbound.put_nowait.assert_called_once()

    async def test_r4_without_a_board_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            self.assertIsNone(
                auto_advance.finish_turn(
                    other, self.key, stop_reason="completed", final_content="ok",
                    tool_events=[_work()],
                )
            )


class OwnershipTest(_Base):
    def _restart(self) -> None:
        auto_advance._owned.clear()
        auto_advance._turns.clear()
        auto_advance._owned_loaded = False

    async def test_ownership_survives_a_restart(self) -> None:
        a, b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=b)  # a was auto-claimed
        self._restart()
        self.assertEqual(
            auto_advance.owned_tasks(self.key),
            {a: {"auto": True, "worked": False}, b: {"auto": False, "worked": False}},
        )

    async def test_unknown_provenance_is_never_closed(self) -> None:
        a, _b = await self._plan("One", "Two")
        await self._run(action="claim", task_id=a)
        self._did()
        # Ownership lost (e.g. a registry written by an older build).
        auto_advance._owned.clear()
        auto_advance._persist()
        self.assertIsNone(self._finish())
        self.assertEqual(self._status(a), "in_progress")

    async def test_forgetting_the_session_drops_its_ownership(self) -> None:
        await self._plan("One", "Two")
        session_focus.forget(self.key)
        self.assertEqual(auto_advance.owned_tasks(self.key), {})

    async def test_store_refuses_a_stale_status(self) -> None:
        store = ProjectBoardStore(self.project)
        t = store.create_task(title="x", actor="a", actor_type="agent")
        store.claim_task(t["id"], actor="a", actor_type="agent")
        with self.assertRaises(BoardError):
            store.update_task(t["id"], fields={"status": "done"}, actor="a", actor_type="agent",
                              expected_status="review")
        with self.assertRaises(BoardError):
            store.claim_task(t["id"], actor="a", actor_type="agent", expected_status="backlog")
        self.assertEqual(store.get_task(t["id"])["status"], "in_progress")


class TextSignalsTest(unittest.TestCase):
    def test_asks_user(self) -> None:
        self.assertTrue(auto_advance.asks_user("Done.\n\nShould I also update the docs?"))
        self.assertTrue(auto_advance.asks_user("Tu veux que je continue ?"))
        self.assertTrue(auto_advance.asks_user("هل تريد المتابعة؟"))
        self.assertFalse(auto_advance.asks_user("See https://x.test/?q=1 for details."))
        self.assertFalse(auto_advance.asks_user("Fixed the parser; all 12 tests pass."))

    def test_reports_failure(self) -> None:
        self.assertTrue(auto_advance.reports_failure("The build failed twice."))
        self.assertFalse(auto_advance.reports_failure("All green: 12 passed."))


class ToolDemandTest(unittest.TestCase):
    def setUp(self) -> None:
        session_focus.clear()
        self.addCleanup(session_focus.clear)

    def test_a_session_with_a_plan_keeps_the_board_tool(self) -> None:
        self.assertEqual(on_demand_tools_for_session("websocket:c1"), frozenset())
        session_focus.remember("websocket:c1", ["t-1"])
        self.assertEqual(on_demand_tools_for_session("websocket:c1"), frozenset({"board"}))
        self.assertEqual(on_demand_tools_for_session(None), frozenset())

    def test_code_turn_keeps_board_once_a_plan_exists(self) -> None:
        from navin.agent.loop import AgentLoop

        code = {"product_module": "code"}
        text = "fix the failing parser test"
        with tempfile.TemporaryDirectory() as ws:
            self.assertIn(
                "board",
                AgentLoop._denied_tools(None, code, text, None, workspace=ws, session_key="websocket:c1"),
            )
            session_focus.remember("websocket:c1", ["t-1"])
            self.assertNotIn(
                "board",
                AgentLoop._denied_tools(None, code, text, None, workspace=ws, session_key="websocket:c1"),
            )


if __name__ == "__main__":
    unittest.main()


class OpenPlanStepsTest(_Base):
    async def test_lists_workable_steps_only_while_the_turn_drives_the_plan(self) -> None:
        a, b, c = await self._plan("One", "Two", "Three")
        steps = auto_advance.open_plan_steps(self.project, self.key)
        self.assertEqual([step["id"] for step in steps], [a, b, c])
        self.assertEqual(steps[0]["status"], "in_progress")

        await self._run(action="move", task_id=a, status="done")
        self.assertEqual(
            [step["id"] for step in auto_advance.open_plan_steps(self.project, self.key)], [b, c],
        )
        message = auto_advance.build_open_plan_message(
            auto_advance.open_plan_steps(self.project, self.key),
        )
        self.assertIn(f"- {b} [in_progress] Two", message)
        self.assertIn("claim_next=true", message)
        self.assertNotIn("\u2014", message)

        # Once the turn is over, a later turn about something else is not
        # pushed back onto this plan.
        self._finish()
        self.assertEqual(auto_advance.open_plan_steps(self.project, self.key), [])

    def test_no_session_or_no_board_call_means_no_steps(self) -> None:
        self.assertEqual(auto_advance.open_plan_steps(self.project, None), [])
        self.assertEqual(auto_advance.open_plan_steps(None, self.key), [])
        self.assertEqual(auto_advance.open_plan_steps(self.project, "cli:never-used"), [])

    def test_repeat_reminder_demands_a_tool_call(self) -> None:
        steps = [{"id": "t-1", "status": "in_progress", "title": "One"}]
        first = auto_advance.build_open_plan_message(steps)
        again = auto_advance.build_open_plan_message(steps, repeat=True)
        self.assertNotIn("changed nothing", first)
        self.assertIn("changed nothing", again)
        self.assertIn("- t-1 [in_progress] One", again)
        self.assertIn("claim_next=true", again)
        self.assertNotIn("\u2014", again)


class StayOnThePlanTest(_Base):
    async def test_closing_a_step_never_auto_claims_an_older_card_of_the_session(self) -> None:
        from tests.test_board_batching import _age

        await self._plan("Old audit")
        store = ProjectBoardStore(self.project)
        ids = {t["title"]: t["id"] for t in store.read_tasks()}
        await self._run(action="move", task_id=ids["Old audit"], status="planned")
        # Filed by an earlier request, hours before the plan below.
        _age(self.project, ids["Old audit"])
        await self._plan("Analyse", "Deliver")
        ids = {t["title"]: t["id"] for t in store.read_tasks()}
        old, a, b = ids["Old audit"], ids["Analyse"], ids["Deliver"]
        # Filing the new plan claims its own first step, not the older card.
        self.assertEqual(store.get_task(a)["status"], "in_progress")
        self.assertEqual(store.get_task(old)["status"], "planned")

        await self._run(action="move", task_id=a, status="done")
        self.assertEqual(store.get_task(b)["status"], "in_progress")
        await self._run(action="move", task_id=b, status="done")
        # The plan is finished: the older card stays where the user left it.
        self.assertEqual(store.get_task(old)["status"], "planned")

    def test_plan_members_chain_close_filings_and_dependencies(self) -> None:
        from navin.board.plan import plan_members

        tasks = [
            {"id": "old", "created_at": "2026-10-22T20:00:00Z"},
            {"id": "a", "created_at": "2026-10-23T01:00:00Z"},
            # Filed one by one, a few minutes apart: still the same request.
            {"id": "b", "created_at": "2026-10-23T01:04:00Z"},
            {"id": "c", "created_at": "2026-10-23T01:12:00Z"},
            # Filed later but linked to the plan by depends_on.
            {"id": "fix", "created_at": "2026-10-23T03:00:00Z", "depends_on": ["a"]},
            {"id": "other", "created_at": "2026-10-23T05:00:00Z"},
        ]
        self.assertEqual(plan_members(tasks, "a"), {"a", "b", "c", "fix"})
        self.assertEqual(plan_members(tasks, "old"), {"old"})
        self.assertEqual(plan_members(tasks, "missing"), {"missing"})
