# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Magentic-style mission Task/Progress Ledger."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.board import BoardTool
from navin.board.ledger import (
    MissionLedgerStore,
    action_fingerprint,
    empty_ledger,
    normalize_ledger,
    validation_requires_evidence,
)
from navin.board.plan import session_plan
from navin.board.store import BoardError, ProjectBoardStore


class MissionLedgerCoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = MissionLedgerStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_and_load(self) -> None:
        ledger = self.store.create(
            goal="Add GitHub auth",
            constraints=["Keep Google Auth"],
            facts=["Supabase is used"],
            missing_info=["GitHub OAuth app id"],
            acceptance_criteria=["Login works"],
            steps=[
                {
                    "id": "t-1",
                    "title": "Explore auth",
                    "status": "planned",
                    "depends_on": [],
                    "validation": "verify",
                    "acceptance": "Architecture identified",
                }
            ],
            actor="tester",
        )
        self.assertEqual(ledger["version"], 1)
        self.assertEqual(ledger["status"], "draft")
        self.assertTrue(self.store.path.is_file())
        loaded = self.store.load()
        assert loaded is not None
        self.assertEqual(loaded["goal"], "Add GitHub auth")
        self.assertEqual(len(loaded["steps"]), 1)
        self.assertEqual(loaded["history"][0]["actor"], "tester")

    def test_bump_version_history(self) -> None:
        ledger = self.store.create(goal="Ship feature", actor="a")
        ledger = self.store.bump_version(
            ledger, reason="refined steps", changes=["steps"], actor="a"
        )
        self.store.save(ledger)
        loaded = self.store.load()
        assert loaded is not None
        self.assertEqual(loaded["version"], 2)
        self.assertEqual(loaded["history"][-1]["reason"], "refined steps")

    def test_record_step_result_and_stall(self) -> None:
        ledger = self.store.create(
            goal="G",
            steps=[{"id": "t-1", "title": "One", "status": "planned"}],
        )
        ledger = self.store.record_step_result(
            ledger, "t-1", ok=False, evidence="fail", progress=False, fingerprint="aaaa"
        )
        self.assertEqual(ledger["progress"]["stall_count"], 1)
        self.assertFalse(ledger["progress"]["last_result_ok"])
        ledger = self.store.record_step_result(
            ledger, "t-1", ok=True, evidence="ok", progress=True, fingerprint="bbbb"
        )
        self.assertEqual(ledger["progress"]["stall_count"], 0)
        self.assertEqual(ledger["status"], "running")

    def test_detect_loop(self) -> None:
        fps = ["x"] * 3
        self.assertTrue(MissionLedgerStore.detect_loop(fps, repeats=3))
        self.assertFalse(MissionLedgerStore.detect_loop(["a", "b", "c"], repeats=3))

    def test_local_replan_keeps_upstream_done(self) -> None:
        ledger = self.store.create(
            goal="G",
            steps=[
                {
                    "id": "t-1",
                    "title": "Done step",
                    "status": "completed",
                    "depends_on": [],
                },
                {
                    "id": "t-2",
                    "title": "Failed",
                    "status": "planned",
                    "depends_on": ["t-1"],
                    "retry_count": 2,
                    "evidence": "old",
                },
                {
                    "id": "t-3",
                    "title": "Downstream",
                    "status": "planned",
                    "depends_on": ["t-2"],
                },
            ],
        )
        ledger = self.store.local_replan(
            ledger, "t-2", reason="step-2 stalled", actor="agent"
        )
        self.store.save(ledger)
        by_id = {s["id"]: s for s in ledger["steps"]}
        self.assertEqual(by_id["t-1"]["status"], "completed")
        self.assertEqual(by_id["t-2"]["status"], "planned")
        self.assertEqual(by_id["t-2"]["retry_count"], 0)
        self.assertEqual(by_id["t-2"]["evidence"], "")
        self.assertEqual(ledger["version"], 2)
        self.assertIn("replan", ledger["history"][-1]["reason"].lower() + "replan")
        self.assertIn("t-2", ledger["progress"].get("last_invalidated") or [])
        self.assertIn("t-3", ledger["progress"].get("last_invalidated") or [])

    def test_pause_resume_budget(self) -> None:
        ledger = self.store.create(goal="G")
        ledger["progress"]["budget"]["token_budget"] = 100
        ledger = self.store.consume_budget(ledger, tokens=50)
        self.assertEqual(ledger["status"], "draft")
        ledger = self.store.consume_budget(ledger, tokens=60)
        self.assertEqual(ledger["status"], "paused")
        self.assertEqual(ledger["pause_reason"], "budget")
        ledger = self.store.resume(ledger, actor="human")
        self.assertEqual(ledger["status"], "running")
        self.assertEqual(ledger["pause_reason"], "")

    def test_apply_manual_edit(self) -> None:
        ledger = self.store.create(goal="Old")
        ledger = self.store.apply_manual_edit(
            ledger,
            {"goal": "New goal", "constraints": ["No secrets"]},
            actor="human",
        )
        self.store.save(ledger)
        self.assertEqual(ledger["goal"], "New goal")
        self.assertEqual(ledger["history"][-1]["actor"], "human")

    def test_runtime_lines(self) -> None:
        self.store.create(goal="Ship auth", acceptance_criteria=["Works"])
        lines = self.store.runtime_lines()
        self.assertTrue(any("Ship auth" in line for line in lines))
        self.assertTrue(any("Orchestrator protocol" in line for line in lines))

    def test_normalize_and_helpers(self) -> None:
        raw = empty_ledger(goal="x")
        cleaned = normalize_ledger(raw)
        assert cleaned is not None
        self.assertEqual(cleaned["schema_version"], 1)
        self.assertTrue(validation_requires_evidence("test"))
        self.assertFalse(validation_requires_evidence("manual"))
        self.assertTrue(action_fingerprint("a", "b"))


class BoardValidationGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.board = ProjectBoardStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_done_without_evidence_rejected(self) -> None:
        task = self.board.create_task(
            title="Run tests",
            actor="agent",
            actor_type="agent",
            status="planned",
            validation="test",
        )
        with self.assertRaises(BoardError) as ctx:
            self.board.update_task(
                task["id"],
                fields={"status": "done"},
                actor="agent",
                actor_type="agent",
            )
        self.assertIn("evidence", ctx.exception.message)

    def test_done_with_evidence_and_a_real_green_run_ok(self) -> None:
        from navin.quality.verification_log import record_verification

        task = self.board.create_task(
            title="Run tests",
            actor="agent",
            actor_type="agent",
            status="planned",
            validation="test",
        )
        # Evidence prose alone is no longer enough: a real recorded run
        # (what the verify/test_run tools write) must back it.
        record_verification(
            self.board.project_path,
            source="test_run",
            ok=True,
            tests_ran=True,
            summary="tests: 12 passed, 0 failed",
        )
        updated = self.board.update_task(
            task["id"],
            fields={"status": "done", "evidence": "pytest: 12 passed"},
            actor="agent",
            actor_type="agent",
        )
        self.assertEqual(updated["status"], "done")
        self.assertIn("pytest", updated["evidence"])

    def test_acceptance_fields_persist(self) -> None:
        task = self.board.create_task(
            title="Step",
            actor="agent",
            actor_type="agent",
            acceptance="API returns 200",
            validation="verify",
            agent="implementer",
        )
        loaded = self.board.get_task(task["id"])
        self.assertEqual(loaded["acceptance"], "API returns 200")
        self.assertEqual(loaded["validation"], "verify")
        self.assertEqual(loaded["agent"], "implementer")


class SessionPlanMissionTest(unittest.TestCase):
    def test_session_plan_includes_mission_fields(self) -> None:
        tasks = [
            {
                "id": "t-1",
                "title": "A",
                "description": "",
                "status": "planned",
                "priority": "medium",
                "depends_on": [],
                "acceptance": "ok",
                "validation": "none",
                "retry_count": 0,
            }
        ]
        mission = {
            "goal": "Ship it",
            "version": 3,
            "status": "running",
            "acceptance_criteria": ["Works"],
            "pause_reason": "",
            "progress": {
                "stall_count": 2,
                "loop_detected": False,
                "replan_needed": True,
            },
            "history": [{"version": 3, "reason": "local replan"}],
        }
        plan = session_plan(tasks, ["t-1"], mission=mission)
        assert plan is not None
        self.assertEqual(plan["goal"], "Ship it")
        self.assertEqual(plan["version"], 3)
        self.assertEqual(plan["stall_count"], 2)
        self.assertTrue(plan["replan_needed"])
        self.assertEqual(plan["last_change"]["reason"], "local replan")
        self.assertEqual(plan["items"][0]["acceptance"], "ok")

    def test_session_plan_without_mission_stays_compatible(self) -> None:
        tasks = [
            {
                "id": "t-1",
                "title": "A",
                "description": "desc",
                "status": "planned",
                "priority": "medium",
                "depends_on": [],
            }
        ]
        plan = session_plan(tasks, ["t-1"])
        assert plan is not None
        self.assertNotIn("goal", plan)
        self.assertEqual(plan["total_count"], 1)


class BoardToolLedgerIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        self.tool = BoardTool(workspace=self.project)
        self.board = ProjectBoardStore(self.project)
        self.ledger = MissionLedgerStore(self.project)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_blueprint_then_forge_happy_path(self) -> None:
        t1 = await self.tool.execute(
            action="create",
            title="Explore auth",
            status="planned",
            validation="verify",
            acceptance="Architecture identified",
            actor="planner",
        )
        t2 = await self.tool.execute(
            action="create",
            title="Implement provider",
            status="planned",
            validation="test",
            acceptance="Tests pass",
            actor="planner",
        )
        id1 = t1.split(" ", 2)[2].split(" ", 1)[0]
        id2 = t2.split(" ", 2)[2].split(" ", 1)[0]
        await self.tool.execute(
            action="update",
            task_id=id2,
            depends_on=[id1],
            actor="planner",
        )
        init = await self.tool.execute(
            action="ledger_init",
            goal="Add GitHub auth",
            constraints=["Keep Google Auth"],
            facts=["Supabase auth in use"],
            missing_info=[],
            acceptance_criteria=["GitHub login works", "Profile created"],
            step_ids=[id1, id2],
            actor="planner",
        )
        self.assertIn("Mission ledger created", init)
        self.assertEqual(self.ledger.load()["status"], "draft")

        await self.tool.execute(action="claim", task_id=id1, actor="forge")
        await self.tool.execute(
            action="move",
            task_id=id1,
            status="done",
            evidence="Architecture: supabase.auth + existing Google provider",
            actor="forge",
        )
        prog1 = await self.tool.execute(
            action="ledger_progress",
            task_id=id1,
            ok=True,
            progress=True,
            evidence="Architecture identified",
            actor="forge",
        )
        self.assertIn("ok=True", prog1)

        await self.tool.execute(action="claim", task_id=id2, actor="forge")
        # Gate: done without evidence must fail for validation=test
        blocked = await self.tool.execute(
            action="move", task_id=id2, status="done", actor="forge",
        )
        self.assertTrue(getattr(blocked, "is_error", False))

        await self.tool.execute(
            action="move",
            task_id=id2,
            status="done",
            evidence="pytest: 8 passed",
            actor="forge",
        )
        prog2 = await self.tool.execute(
            action="ledger_progress",
            task_id=id2,
            ok=True,
            progress=True,
            evidence="pytest: 8 passed",
            actor="forge",
        )
        self.assertIn("ok=True", prog2)
        ledger = self.ledger.load()
        assert ledger is not None
        self.assertEqual(ledger["progress"]["stall_count"], 0)
        get = await self.tool.execute(action="ledger_get", actor="forge")
        self.assertIn("Add GitHub auth", get)
        self.assertIn("Orchestrator protocol", get)

    async def test_stall_loop_then_local_replan(self) -> None:
        created = await self.tool.execute(
            action="create", title="Flaky step", status="planned", actor="a",
        )
        step_id = created.split(" ", 2)[2].split(" ", 1)[0]
        await self.tool.execute(
            action="ledger_init",
            goal="Fix flaky",
            step_ids=[step_id],
            actor="a",
        )
        # Force a low stall budget so replan_needed trips quickly.
        ledger = self.ledger.load()
        assert ledger is not None
        ledger["progress"]["budget"]["max_stalls"] = 2
        self.ledger.save(ledger)

        for _ in range(3):
            await self.tool.execute(
                action="ledger_progress",
                task_id=step_id,
                ok=False,
                progress=False,
                fingerprint="same-loop",
                evidence="still failing",
                actor="a",
            )
        ledger = self.ledger.load()
        assert ledger is not None
        self.assertTrue(ledger["progress"]["loop_detected"] or ledger["progress"]["replan_needed"])
        self.assertGreaterEqual(ledger["progress"]["stall_count"], 2)

        replan = await self.tool.execute(
            action="ledger_replan",
            task_id=step_id,
            reason="flaky approach stalled",
            facts=["Need different test fixture"],
            actor="a",
        )
        self.assertIn("Local replan", replan)
        ledger = self.ledger.load()
        assert ledger is not None
        self.assertGreaterEqual(ledger["version"], 2)
        self.assertFalse(ledger["progress"]["replan_needed"])
        reasons = " ".join(str(h.get("reason") or "") for h in ledger["history"]).lower()
        self.assertIn("flaky", reasons)
        self.assertTrue(
            any("invalidate:" in str(c) for h in ledger["history"] for c in (h.get("changes") or [])),
            ledger["history"],
        )

    async def test_budget_trip_pauses_mission(self) -> None:
        created = await self.tool.execute(
            action="create", title="Costly", status="planned", actor="a",
        )
        step_id = created.split(" ", 2)[2].split(" ", 1)[0]
        await self.tool.execute(
            action="ledger_init", goal="Budgeted work", step_ids=[step_id], actor="a",
        )
        ledger = self.ledger.load()
        assert ledger is not None
        ledger["progress"]["budget"]["token_budget"] = 100
        self.ledger.save(ledger)

        result = await self.tool.execute(
            action="ledger_progress",
            task_id=step_id,
            ok=True,
            progress=True,
            evidence="partial",
            tokens=150,
            actor="a",
        )
        self.assertIn("paused", result.lower() + " " + str(self.ledger.load()["status"]))
        ledger = self.ledger.load()
        assert ledger is not None
        self.assertEqual(ledger["status"], "paused")
        self.assertEqual(ledger["pause_reason"], "budget")

        resumed = await self.tool.execute(action="ledger_resume", actor="human")
        self.assertIn("resumed", resumed.lower())
        self.assertEqual(self.ledger.load()["status"], "running")

    async def test_manual_pause_and_update(self) -> None:
        await self.tool.execute(
            action="ledger_init", goal="Need human", actor="a",
        )
        await self.tool.execute(
            action="ledger_pause", reason="human", actor="a",
        )
        self.assertEqual(self.ledger.load()["pause_reason"], "human")
        updated = await self.tool.execute(
            action="ledger_update",
            constraints=["Do not touch billing"],
            missing_info=["Which OAuth app?"],
            actor="human",
        )
        self.assertIn("updated", updated.lower())
        ledger = self.ledger.load()
        assert ledger is not None
        self.assertIn("Do not touch billing", ledger["constraints"])
        self.assertEqual(ledger["history"][-1]["actor"], "human")

    async def test_plan_action_mentions_ledger(self) -> None:
        await self.tool.execute(
            action="create", title="Step", status="planned", actor="a",
        )
        await self.tool.execute(action="ledger_init", goal="Visible goal", actor="a")
        plan = await self.tool.execute(action="plan", actor="a")
        self.assertIn("Mission ledger", plan)
        self.assertIn("Visible goal", plan)


class OneMissionAtATimeTest(unittest.IsolatedAsyncioTestCase):
    """A one-off errand must not overwrite the mission in flight.

    There is one ledger per project and it is replayed into context every turn,
    so an unrelated request used to both destroy the running mission and inherit
    its goal: asking for a slide deck produced a plan titled "finish the CRM"
    with the deck's three steps hanging off it.
    """

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        self.tool = BoardTool(workspace=self.project)
        self.ledger = MissionLedgerStore(self.project)
        await self.tool.execute(
            action="ledger_init",
            goal="Finish the CRM",
            acceptance_criteria=["Backend answers on :8000"],
            facts=["No framer-motion installed"],
            actor="planner",
        )

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_a_second_mission_is_refused_while_one_is_open(self) -> None:
        result = await self.tool.execute(
            action="ledger_init", goal="Generate a deck", actor="a",
        )
        self.assertTrue(result.is_error)
        self.assertIn("already open", result)
        self.assertIn("Finish the CRM", result)

    async def test_the_open_mission_survives_the_refusal_untouched(self) -> None:
        await self.tool.execute(action="ledger_init", goal="Generate a deck", actor="a")
        ledger = self.ledger.load()
        assert ledger is not None
        self.assertEqual(ledger["goal"], "Finish the CRM")
        self.assertEqual(ledger["acceptance_criteria"], ["Backend answers on :8000"])
        self.assertEqual(ledger["facts"], ["No framer-motion installed"])

    async def test_the_refusal_says_what_to_do_instead(self) -> None:
        result = await self.tool.execute(
            action="ledger_init", goal="Generate a deck", actor="a",
        )
        for advice in ("ledger_update", "without a ledger", "replace=true"):
            with self.subTest(advice=advice):
                self.assertIn(advice, result)

    async def test_a_finished_mission_does_not_block_the_next_one(self) -> None:
        closed = await self.tool.execute(
            action="ledger_update", status="done", actor="a",
        )
        self.assertFalse(getattr(closed, "is_error", False))
        self.assertEqual(self.ledger.load()["status"], "done")
        opened = await self.tool.execute(
            action="ledger_init", goal="Generate a deck", actor="a",
        )
        self.assertIn("Mission ledger created", opened)
        self.assertEqual(self.ledger.load()["goal"], "Generate a deck")

    async def test_replace_is_the_deliberate_way_to_abandon_a_mission(self) -> None:
        opened = await self.tool.execute(
            action="ledger_init", goal="Generate a deck", replace=True, actor="a",
        )
        self.assertIn("Mission ledger created", opened)
        ledger = self.ledger.load()
        assert ledger is not None
        self.assertEqual(ledger["goal"], "Generate a deck")
        self.assertEqual(ledger["acceptance_criteria"], [])

    async def test_a_paused_mission_still_counts_as_open(self) -> None:
        await self.tool.execute(action="ledger_pause", reason="human", actor="a")
        result = await self.tool.execute(
            action="ledger_init", goal="Generate a deck", actor="a",
        )
        self.assertTrue(result.is_error)
        self.assertEqual(self.ledger.load()["goal"], "Finish the CRM")

    async def test_a_status_the_ledger_does_not_know_is_rejected(self) -> None:
        result = await self.tool.execute(
            action="ledger_update", status="finished", actor="a",
        )
        self.assertTrue(result.is_error)
        self.assertIn("unknown mission status", result)
        self.assertEqual(self.ledger.load()["status"], "draft")


class BoardApiMissionPayloadTest(unittest.TestCase):
    def test_payload_includes_mission_and_enriched_session_plan(self) -> None:
        from navin.board import session_focus
        from navin.webui.board_api import board_payload

        class _FakeScope:
            def __init__(self, project: Path) -> None:
                self.project_path = project

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = ProjectBoardStore(root)
            task = board.create_task(
                title="Step A",
                actor="a",
                actor_type="agent",
                status="planned",
                validation="lint",
                acceptance="Clean lint",
            )
            MissionLedgerStore(root).create(
                goal="Payload goal",
                acceptance_criteria=["Ship"],
                steps=[{"id": task["id"], "title": "Step A", "status": "planned"}],
                actor="a",
            )
            key = "websocket:test-mission"
            session_focus.remember(key, [task["id"]])
            payload = board_payload(_FakeScope(root), key)  # type: ignore[arg-type]
            self.assertIn("mission", payload)
            self.assertEqual(payload["mission"]["goal"], "Payload goal")
            plan = payload["session_plan"]
            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual(plan["goal"], "Payload goal")
            self.assertEqual(plan["version"], 1)
            self.assertEqual(plan["items"][0]["validation"], "lint")


class BoardDigestMissionTest(unittest.TestCase):
    def test_digest_includes_mission_runtime_lines(self) -> None:
        from navin.board.context import board_digest

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = ProjectBoardStore(root)
            board.create_task(
                title="Open work", actor="a", actor_type="agent", status="planned",
            )
            MissionLedgerStore(root).create(goal="Digest goal", actor="a")
            lines = board_digest(root)
            text = "\n".join(lines)
            self.assertIn("Open work", text)
            self.assertIn("Digest goal", text)
            self.assertIn("Orchestrator protocol", text)


class WorkflowCruiseMissionTest(unittest.TestCase):
    def test_briefs_and_routes(self) -> None:
        from navin.agent.model_routes import WORKFLOW_ROUTE_ROLES, workflow_role_for_content
        from navin.command.builtin import (
            _COMPOSER_MODE_BY_COMMAND,
            _TRACKED_WORKFLOWS,
            _WORKFLOW_BRIEFS,
            BUILTIN_COMMAND_SPECS,
        )

        self.assertIn("/cruise", _WORKFLOW_BRIEFS)
        self.assertIn("/mission", _WORKFLOW_BRIEFS)
        self.assertIn("mission-ledger", _WORKFLOW_BRIEFS["/blueprint"][1])
        self.assertIn("mission-ledger", _WORKFLOW_BRIEFS["/cruise"][1])
        self.assertEqual(_COMPOSER_MODE_BY_COMMAND["/cruise"], "agent")
        self.assertEqual(_COMPOSER_MODE_BY_COMMAND["/mission"], "agent")
        self.assertIn("/cruise", _TRACKED_WORKFLOWS)
        self.assertIn("/mission", _TRACKED_WORKFLOWS)
        self.assertEqual(WORKFLOW_ROUTE_ROLES["/cruise"], "dev")
        self.assertEqual(WORKFLOW_ROUTE_ROLES["/mission"], "deep")
        self.assertEqual(workflow_role_for_content("/cruise ship it"), "dev")
        self.assertEqual(workflow_role_for_content("/mission long goal"), "deep")
        commands = {spec.command for spec in BUILTIN_COMMAND_SPECS}
        self.assertIn("/cruise", commands)
        self.assertIn("/mission", commands)

        # Briefs must teach the Magentic loop, not just name the skill.
        forge_brief = _WORKFLOW_BRIEFS["/forge"][2]
        self.assertIn("ledger_progress", forge_brief)
        self.assertIn("ledger_replan", forge_brief)
        self.assertIn("INTENT GATE", forge_brief)
        self.assertIn("Do NOT call board", forge_brief)
        self.assertIn("SIMPLE TASKS", forge_brief)
        cruise_brief = _WORKFLOW_BRIEFS["/cruise"][2]
        self.assertIn("ledger_pause", cruise_brief)
        self.assertIn("INTENT GATE", cruise_brief)
        self.assertIn("SIMPLE TASKS", cruise_brief)
        mission_brief = _WORKFLOW_BRIEFS["/mission"][2]
        self.assertIn("create_goal", mission_brief)
        blueprint_brief = _WORKFLOW_BRIEFS["/blueprint"][2]
        self.assertIn("ledger_init", blueprint_brief)
        self.assertIn("Do NOT write or edit any code", blueprint_brief)
        self.assertIn("EXCEPTION (simple tasks)", blueprint_brief)

    def test_vague_forge_focus_triggers_intent_gate_not_infer(self) -> None:
        import asyncio
        from types import SimpleNamespace

        from navin.command.builtin import (
            _UNCLEAR_FOCUS_CLAUSE,
            _workflow_handler,
            workflow_focus_is_actionable,
        )

        self.assertFalse(workflow_focus_is_actionable(""))
        self.assertFalse(workflow_focus_is_actionable("salut"))
        self.assertFalse(workflow_focus_is_actionable("magnifique"))
        self.assertFalse(workflow_focus_is_actionable("ok cool"))
        # Multi-word greetings are still chit-chat, not a build target
        # (regression: "salut ca va" used to arm the delivery clause and the
        # agent lectured the user about missing deliverables).
        self.assertFalse(workflow_focus_is_actionable("salut ca va"))
        self.assertFalse(workflow_focus_is_actionable("salut ça va bien ou quoi"))
        self.assertFalse(workflow_focus_is_actionable("hello how are you doing"))
        self.assertFalse(workflow_focus_is_actionable("bah du coup on fait quoi"))
        self.assertTrue(workflow_focus_is_actionable("build a landing page"))
        self.assertTrue(workflow_focus_is_actionable("continue the pitch deck"))
        self.assertTrue(workflow_focus_is_actionable("site web pour restaurant"))
        self.assertTrue(workflow_focus_is_actionable("je veux un site vitrine"))
        # Other languages: the FR/EN word list is only a fast path. A task in
        # any script must count as a target (the LLM-side gate instruction
        # handles greetings the heuristic cannot know).
        self.assertTrue(workflow_focus_is_actionable("ابن لي موقع ويب لمطعم"))
        self.assertTrue(
            workflow_focus_is_actionable("レストランのウェブサイトを作ってください")
        )
        self.assertTrue(workflow_focus_is_actionable("사이트를 만들어 주세요"))
        self.assertFalse(workflow_focus_is_actionable("مرحبا"))
        self.assertFalse(workflow_focus_is_actionable("こんにちは"))

        from navin.command.modules import (
            REQUIRES_TOOL_DELIVERY_METADATA_KEY,
            REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY,
        )

        for args in ("", "magnifique", "salut", "hello"):
            msg = SimpleNamespace(content="", metadata={}, channel="cli")
            ctx = SimpleNamespace(args=args, raw="/forge", msg=msg, loop=None)
            asyncio.run(_workflow_handler("/forge")(ctx))  # type: ignore[arg-type]
            self.assertIn(_UNCLEAR_FOCUS_CLAUSE, msg.content, args)
            self.assertNotIn("infer the most useful scope", msg.content)
            # Greeting must not arm delivery/verify nudges (those force tools).
            self.assertFalse(
                msg.metadata.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY), args
            )
            self.assertFalse(
                msg.metadata.get(REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY), args
            )

        msg = SimpleNamespace(content="", metadata={}, channel="cli")
        ctx = SimpleNamespace(
            args="build a CRM dashboard", raw="/forge", msg=msg, loop=None
        )
        asyncio.run(_workflow_handler("/forge")(ctx))  # type: ignore[arg-type]
        self.assertIn(
            "Focus / target given by the user: build a CRM dashboard",
            msg.content,
        )
        self.assertNotIn(_UNCLEAR_FOCUS_CLAUSE, msg.content)
        self.assertTrue(msg.metadata.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY))
        self.assertTrue(msg.metadata.get(REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY))

    def test_simple_focus_starts_now_without_build_wait(self) -> None:
        import asyncio
        from types import SimpleNamespace

        from navin.command.builtin import (
            _SIMPLE_TASK_CLAUSE,
            _workflow_handler,
            workflow_focus_is_simple,
        )

        self.assertTrue(workflow_focus_is_simple("Pitch Navin en 10 slides"))
        self.assertTrue(workflow_focus_is_simple("Génère un mémo d'une page"))
        self.assertTrue(workflow_focus_is_simple("Écris un script qui liste les PDF"))
        self.assertTrue(workflow_focus_is_simple("Rename the helper file"))
        self.assertFalse(workflow_focus_is_simple("Add a login page"))
        self.assertFalse(workflow_focus_is_simple("Fais un plan pour un pitch"))
        self.assertFalse(workflow_focus_is_simple("Migrate the billing stack"))
        self.assertFalse(workflow_focus_is_simple(""))

        msg = SimpleNamespace(content="", metadata={}, channel="cli")
        ctx = SimpleNamespace(
            args="Pitch Navin en 10 slides", raw="/forge", msg=msg, loop=None
        )
        asyncio.run(_workflow_handler("/forge")(ctx))  # type: ignore[arg-type]
        self.assertIn(_SIMPLE_TASK_CLAUSE, msg.content)

        plan_msg = SimpleNamespace(content="", metadata={}, channel="cli")
        plan_ctx = SimpleNamespace(
            args="Add a login page", raw="/blueprint", msg=plan_msg, loop=None
        )
        asyncio.run(_workflow_handler("/blueprint")(plan_ctx))  # type: ignore[arg-type]
        self.assertNotIn(_SIMPLE_TASK_CLAUSE, plan_msg.content)

    def test_mission_ledger_skill_exists(self) -> None:
        from navin.agent.skills import BUILTIN_SKILLS_DIR
        from navin.command.builtin import _WORKFLOW_BRIEFS

        self.assertTrue((BUILTIN_SKILLS_DIR / "mission-ledger" / "SKILL.md").is_file())
        for command in ("/blueprint", "/forge", "/cruise", "/mission"):
            names = [
                name.strip()
                for name in _WORKFLOW_BRIEFS[command][1].split(",")
                if name.strip()
            ]
            self.assertIn("mission-ledger", names, command)

    def test_tracked_briefs_still_work_for_cruise_mission(self) -> None:
        import asyncio
        from types import SimpleNamespace

        from navin.command.builtin import _TRACKED_RUN_CLAUSE, _workflow_handler

        for command in ("/cruise", "/mission"):
            msg = SimpleNamespace(content="", metadata={}, channel="cli")
            ctx = SimpleNamespace(args="do the thing", raw=command, msg=msg, loop=None)
            asyncio.run(_workflow_handler(command)(ctx))  # type: ignore[arg-type]
            self.assertIn(_TRACKED_RUN_CLAUSE, msg.content, command)
            self.assertIn("project-board", msg.content, command)
            self.assertEqual(msg.metadata.get("composer_mode"), "agent")


if __name__ == "__main__":
    unittest.main()
