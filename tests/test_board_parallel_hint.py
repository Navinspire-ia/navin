# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A plan with three or more independent ready tasks tells the agent to fan out."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from navin.agent.tools.board import BoardTool, _parallel_hint
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.board import session_focus


class ParallelHintTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        session_focus.clear()
        self.addCleanup(session_focus.clear)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tool = BoardTool(workspace=Path(tmp.name))
        token = bind_request_context(RequestContext(channel="websocket", chat_id="c1", session_key="websocket:c1"))
        self.addCleanup(lambda: reset_request_context(token))

    async def _run(self, actor_type: str = "agent", **kwargs: Any) -> str:
        result = await self.tool.execute(actor="a", actor_type=actor_type, **kwargs)
        return result if isinstance(result, str) else str(getattr(result, "content", result))

    def test_threshold(self) -> None:
        self.assertEqual(_parallel_hint(2), "")
        self.assertIn("two subagents", _parallel_hint(3))

    async def test_independent_plan_asks_for_subagents(self) -> None:
        tasks = [{"title": f"Task {i}"} for i in range(5)]
        out = await self._run(action="create", tasks=tasks)
        self.assertIn("PARALLEL: 5 tasks are ready", out)
        self.assertIn("PARALLEL", await self._run(action="next"))

    async def test_subagent_is_not_told_to_fan_out(self) -> None:
        tasks = [{"title": f"Task {i}"} for i in range(5)]
        out = await self._run(actor_type="subagent", action="create", tasks=tasks)
        self.assertNotIn("PARALLEL", out)

    async def test_two_ready_tasks_stay_quiet(self) -> None:
        out = await self._run(action="create", tasks=[{"title": "A"}, {"title": "B"}])
        self.assertNotIn("PARALLEL", out)


if __name__ == "__main__":
    unittest.main()
