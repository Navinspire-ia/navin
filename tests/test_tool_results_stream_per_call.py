# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A finished tool shows its result right away, not when its batch ends.

Measured in a desktop session: read_file and grep results sat behind an exec
in the same response, and behind the workspace snapshots around it, for 90 to
240 seconds before the UI saw any of them.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from navin.agent.hook import AgentHookContext
from navin.agent.progress_hook import AgentProgressHook


def _call(call_id: str, name: str = "read_file") -> Any:
    return SimpleNamespace(id=call_id, name=name, arguments={"path": "a.py"})


class PerCallFinishTest(unittest.TestCase):
    def test_each_result_goes_out_once(self) -> None:
        sent: list[list[dict]] = []

        async def on_progress(text: str, *, tool_hint: bool = False, tool_events=None, **_: Any) -> None:
            if tool_events:
                sent.append(tool_events)

        hook = AgentProgressHook(on_progress=on_progress)
        first, second = _call("c1"), _call("c2", "exec")
        context = AgentHookContext(iteration=0, messages=[])

        async def scenario() -> None:
            await hook.on_tool_done(context, first, "contents", {"name": "read_file", "status": "ok"})
            self.assertEqual([e["call_id"] for batch in sent for e in batch], ["c1"])
            context.tool_calls = [first, second]
            context.tool_results = ["contents", "done"]
            context.tool_events = [{"status": "ok"}, {"status": "ok"}]
            await hook.after_iteration(context)

        asyncio.run(scenario())
        self.assertEqual([e["call_id"] for batch in sent for e in batch], ["c1", "c2"])
        self.assertEqual(sent[0][0]["phase"], "end")
        self.assertEqual(sent[0][0]["result"], "contents")


class RunningLabelTest(unittest.TestCase):
    """The live strip says the call runs, not that it is still being written."""

    def test_start_replaces_the_drafting_line(self) -> None:
        labels: list[str] = []

        async def on_progress(text: str, *, agent_ui=None, **_: Any) -> None:
            if agent_ui and agent_ui.get("kind") == "task_progress":
                labels.append(agent_ui["data"]["label"])

        hook = AgentProgressHook(on_progress=on_progress)
        context = AgentHookContext(iteration=0, messages=[])
        context.tool_calls = [
            SimpleNamespace(id="g1", name="grep", arguments={"pattern": "DUPLICATE_INVOICE"}),
            _call("r1"),
        ]
        asyncio.run(hook.before_execute_tools(context))
        self.assertEqual(len(labels), 1)
        self.assertIn("DUPLICATE_INVOICE", labels[0])
        self.assertTrue(labels[0].endswith("(+1 more)"))
        self.assertNotIn("Preparing", labels[0])

    def test_a_multi_pattern_grep_names_its_first_pattern(self) -> None:
        calls = [SimpleNamespace(id="g1", name="grep", arguments={"patterns": ["OCR_PURCHASE", "celery"], "path": "backend"})]
        self.assertIn("OCR_PURCHASE", AgentProgressHook._running_label(calls))


if __name__ == "__main__":
    unittest.main()
