# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Completion prerequisites must not look like permission or Board failures."""

import asyncio

import pytest
from textual.widgets import Static

from navin.agent.hook import AgentHookContext
from navin.agent.runner import AgentLoopGuard, AgentRunner, AgentRunSpec
from navin.agent.tools.board import BoardTool
from navin.tui.widgets import ApprovalCard, AssistantMessage, ToolCall, Transcript
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.progress_events import build_tool_event_finish_payloads
from navin.utils.tool_hints import VALIDATION_FOR_USER
from tests.test_long_task_continuation import ScriptedProvider, registry, tool_call
from tests.test_tui_activity import ActivityHost


class _TwoTurnsHost(ActivityHost):
    """An earlier turn and the current one, in a real transcript."""

    def __init__(self) -> None:
        super().__init__()
        self.earlier = AssistantMessage("Navin")

    def compose(self):
        with Transcript():
            yield self.earlier
            yield self.block


@pytest.mark.parametrize("theme,width", [("navin", 110), ("navin-light", 48)])
@pytest.mark.parametrize("saved_message", [False, True])
def test_board_validation_feedback_and_later_success(tmp_path, theme, width, saved_message):
    async def run():
        tools = registry(tmp_path)
        tools.register(BoardTool(workspace=tmp_path))
        guard = AgentLoopGuard()
        guard.validation.edited({"src/service.py"}, require_tests=True)
        spec = AgentRunSpec(
            initial_messages=[], tools=tools, loop_guard=guard, max_iterations=2, max_tool_result_chars=4_000,
            runtime=LLMRuntime.capture(ScriptedProvider([]), "test-quality", context_window_tokens=128_000),
        )
        call = tool_call("board", action="move", task_id="task-1", status="done").tool_calls[0]
        result, event, fatal = await AgentRunner()._dispatch_tool_call(spec, call, {}, {})
        assert fatal is None
        assert "not a request for user permission" in result
        assert "request approval for that command" in result
        if saved_message:
            result = "Validation required before closing this work.\n" + guard.validation.missing()
        context = AgentHookContext(
            iteration=1, messages=[], tool_calls=[call], tool_results=[result], tool_events=[event],
        )
        payload = build_tool_event_finish_payloads(context)[0]
        app = ActivityHost(theme)
        async with app.run_test(size=(width, 34)) as pilot:
            await app.block.tool_event(
                payload["call_id"], payload["name"], payload["phase"], payload["arguments"],
                payload["result"], payload["error"], None,
            )
            await pilot.pause()
            row = app.block.query_one(ToolCall)
            assert "Validation pending" in row._head_text()
            # The row names its task: several refused closes are told apart.
            assert "task-1" in row._head_text()
            assert "Failed" not in row._head_text()
            assert "×" not in row.copy_text()
            assert "src/service.py" in row.copy_text()
            # The gate's instructions to the agent are not shown to the user as
            # a permission request no approval card ever followed.
            assert "request approval" not in row.copy_text()
            assert VALIDATION_FOR_USER in row.copy_text()
            assert row.has_class("-validation-pending")
            assert not row.has_class("-error")
            assert not app.query(ApprovalCard)
            await app.block.tool_event(
                "closed", "board", "end", call.arguments, "Task task-1 [done]", None, None,
            )
            await app.block.finish(latency_ms=1, model="test", preset=None)
            assert "failed" not in str(app.block.query_one(".assistant-foot", Static).content)
            assert not app.block._tools["closed"].validation_pending
            assert "[done]" in app.block._tools["closed"].copy_text()
    asyncio.run(run())


def test_a_close_that_passes_in_a_later_turn_settles_the_rows_still_waiting():
    """Seen live: six "! Validation pending" rows stayed after every task was closed."""
    async def run():
        app = _TwoTurnsHost()
        refused = (
            "Validation pending: task remains open.\n"
            "This is a validation prerequisite, not a request for user permission.\n"
            "Changed files: src/service.py\n"
            "Continue the required validation automatically. Retry closing only after it passes. "
            "If a required command actually needs permission, request approval for that command "
            "and explain why. User approval does not replace passing validation."
        )
        async with app.run_test(size=(100, 30)) as pilot:
            for task in ("t-1", "t-2"):
                await app.earlier.tool_event(
                    f"refused-{task}", "board", "error",
                    {"action": "move", "task_id": task, "status": "done"}, None, refused, None,
                )
            await app.earlier.set_text("Running the checks first.")
            await app.earlier.finish(latency_ms=1, model="test", preset=None)
            rows = {row.arguments["task_id"]: row for row in app.earlier.query(ToolCall)}
            assert rows["t-1"]._head_text() != rows["t-2"]._head_text()
            closing = {"action": "move", "task_id": "t-1", "status": "done"}
            await app.block.tool_event("closed", "board", "start", closing, None, None, None)
            await app.block.tool_event("closed", "board", "end", closing, "Task moved: t-1 [done]", None, None)
            await pilot.pause()
            assert rows["t-1"].phase == "end" and not rows["t-1"].validation_pending
            assert rows["t-1"]._head_text().startswith("✓ ")
            # The other task is still waiting on its checks.
            assert rows["t-2"].validation_pending
    asyncio.run(run())


def test_real_board_permission_error_shows_a_cross_and_a_short_reason():
    """A failed step gets a red cross and a short reason, never "Failed"."""
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 24)):
            await app.block.tool_event(
                "denied", "board", "error", {"action": "move", "status": "done"},
                None, "Permission denied while writing the board file.", None,
            )
            row = app.block.query_one(ToolCall)
            assert "Failed" not in row._head_text()
            assert row.display and row._head_text().startswith("× ")
            assert row._summary_text() == "permission denied"
            assert "Permission denied" in row.copy_text()
            assert row.has_class("-error")
            assert not row.validation_pending
            await app.block.finish(latency_ms=1, model="test", preset=None)
            assert "failed" not in str(app.block.query_one(".assistant-foot", Static).content)
    asyncio.run(run())
