# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Board bookkeeping in as few model round trips as possible.

A measured /forge run spent 30 of 127 model steps on the board alone (one
create per task, a move, then a separate claim and ledger_progress): each a
5-8s round trip for a write that takes milliseconds.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from navin.agent.tools.board import BoardTool
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.board import session_focus
from navin.board.ledger import MissionLedgerStore
from navin.board.store import ProjectBoardStore


def _age(project: Path, task_id: str, created_at: str = "2020-01-01T00:00:00Z") -> None:
    """Backdate a card, as if an earlier request had filed it."""
    store = ProjectBoardStore(project)
    tasks = store.read_tasks()
    for task in tasks:
        if task["id"] == task_id:
            task["created_at"] = created_at
    store._write_tasks(tasks)


class BoardBatchingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        session_focus.clear()
        self.addCleanup(session_focus.clear)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        self.tool = BoardTool(workspace=self.project)
        token = bind_request_context(RequestContext(channel="websocket", chat_id="c1", session_key="websocket:c1"))
        self.addCleanup(lambda: reset_request_context(token))

    async def _run(self, **kwargs: Any) -> str:
        result = await self.tool.execute(actor="agent", **kwargs)
        return result if isinstance(result, str) else str(getattr(result, "content", result))

    async def test_one_create_files_the_whole_plan_with_dependencies(self) -> None:
        out = await self._run(action="create", tasks=[
            {"title": "Scaffold app"},
            {"title": "Invoice API", "depends_on": ["Scaffold app"]},
            {"title": "Invoice UI", "depends_on": ["Invoice API"], "acceptance": "list renders"},
        ])
        self.assertIn("3 tasks created", out)
        tasks = {t["title"]: t for t in ProjectBoardStore(self.project).read_tasks()}
        self.assertEqual(tasks["Invoice API"]["depends_on"], [tasks["Scaffold app"]["id"]])
        self.assertEqual(tasks["Invoice UI"]["depends_on"], [tasks["Invoice API"]["id"]])

    async def test_done_with_claim_next_starts_the_next_step_in_the_same_call(self) -> None:
        await self._run(action="create", tasks=[{"title": "A"}, {"title": "B", "depends_on": ["A"]}])
        by_title = {t["title"]: t for t in ProjectBoardStore(self.project).read_tasks()}
        await self._run(action="claim", task_id=by_title["A"]["id"])
        out = await self._run(action="move", task_id=by_title["A"]["id"], status="done",
                              evidence="scaffold builds", claim_next=True)
        self.assertIn("Next task claimed", out)
        self.assertIn(by_title["B"]["id"], out)

    async def test_claim_next_stays_on_the_plan_and_skips_unrelated_cards(self) -> None:
        await self._run(action="create", tasks=[{"title": "Old audit", "priority": "critical"}])
        old_id = ProjectBoardStore(self.project).read_tasks()[0]["id"]
        # Filing auto-claims the first step; the old card waits, ready, on the
        # board, filed by an earlier request hours before this plan.
        await self._run(action="move", task_id=old_id, status="planned")
        _age(self.project, old_id)
        await self._run(action="create", tasks=[
            {"title": "Analyse"},
            {"title": "Prompts", "depends_on": ["Analyse"]},
            {"title": "Gate", "depends_on": ["Analyse"]},
            {"title": "Deliver", "depends_on": ["Prompts", "Gate"]},
        ])
        ids = {t["title"]: t["id"] for t in ProjectBoardStore(self.project).read_tasks()}
        await self._run(action="claim", task_id=ids["Analyse"])
        out = await self._run(action="move", task_id=ids["Analyse"], status="done",
                              evidence="sources listed", claim_next=True)
        self.assertIn("Next task claimed", out)
        self.assertNotIn(ids["Old audit"], out)
        first = ids["Prompts"] if ids["Prompts"] in out else ids["Gate"]
        self.assertIn(first, out)

        second = ids["Gate"] if first == ids["Prompts"] else ids["Prompts"]
        out = await self._run(action="move", task_id=first, status="done",
                              evidence="tests pass", claim_next=True)
        self.assertIn(second, out)
        self.assertNotIn(ids["Old audit"], out.split("Next task claimed")[-1])

        await self._run(action="move", task_id=second, status="done", evidence="ok")
        await self._run(action="claim", task_id=ids["Deliver"])
        out = await self._run(action="move", task_id=ids["Deliver"], status="done",
                              evidence="build ok", claim_next=True)
        self.assertIn("No ready step left in this plan", out)
        self.assertIn(f"{ids['Old audit']} (not claimed", out)
        old = ProjectBoardStore(self.project).get_task(ids["Old audit"])
        self.assertNotEqual(old["status"], "in_progress")

    async def test_claim_next_follows_a_flat_plan_filed_in_one_call(self) -> None:
        await self._run(action="create", tasks=[{"title": "A"}, {"title": "B"}])
        ids = {t["title"]: t["id"] for t in ProjectBoardStore(self.project).read_tasks()}
        await self._run(action="claim", task_id=ids["A"])
        out = await self._run(action="move", task_id=ids["A"], status="done",
                              evidence="ok", claim_next=True)
        self.assertIn(f"Next task claimed: {ids['B']}", out)

    async def test_done_records_ledger_progress_without_a_separate_call(self) -> None:
        await self._run(action="ledger_init", goal="invoice app")
        await self._run(action="create", tasks=[{"title": "A"}])
        task_id = ProjectBoardStore(self.project).read_tasks()[0]["id"]
        out = await self._run(action="move", task_id=task_id, status="done", evidence="ok")
        self.assertIn("Ledger progress recorded", out)
        progress = MissionLedgerStore(self.project).load()["progress"]
        self.assertEqual(progress["current_step_id"], task_id)
        self.assertTrue(progress["last_result_ok"])


if __name__ == "__main__":
    unittest.main()


class VerifyBeforeDoneTest(BoardBatchingTest):
    async def _task_needing_verify(self) -> str:
        await self._run(action="create", tasks=[{"title": "A", "validation": "verify"}])
        return ProjectBoardStore(self.project).read_tasks()[0]["id"]

    async def test_done_runs_verify_itself_when_proof_is_missing(self) -> None:
        from unittest import mock

        from navin.agent.tools import quality
        from navin.quality import verification_log

        state = {"green": False, "ran": 0}

        async def fake_verify(self, action, **kwargs):
            state["ran"] += 1
            state["green"] = True
            return "PASS - no lint errors, tests green"

        def refusal(_path, require_tests=False):
            return "" if state["green"] else "no passing verify run recorded"

        task_id = await self._task_needing_verify()
        with mock.patch.object(quality.VerifyTool, "execute", fake_verify), \
             mock.patch.object(verification_log, "refusal_to_close_without_proof", refusal):
            out = await self._run(action="move", task_id=task_id, status="done", evidence="works")
        self.assertEqual(state["ran"], 1)
        self.assertIn("Verified before closing", out)
        self.assertEqual(ProjectBoardStore(self.project).get_task(task_id)["status"], "done")

    async def test_a_red_verify_keeps_the_task_open(self) -> None:
        from unittest import mock

        from navin.agent.tools import quality
        from navin.quality import verification_log

        async def fake_verify(self, action, **kwargs):
            return "FAIL - 2 type errors"

        task_id = await self._task_needing_verify()
        with mock.patch.object(quality.VerifyTool, "execute", fake_verify), \
             mock.patch.object(verification_log, "refusal_to_close_without_proof",
                               lambda *_a, **_k: "last verify failed"):
            out = await self._run(action="move", task_id=task_id, status="done", evidence="works")
        self.assertIn("not green", out)
        self.assertNotEqual(ProjectBoardStore(self.project).get_task(task_id)["status"], "done")
