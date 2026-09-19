# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Completion prerequisites must not look like permission or Board failures."""

import asyncio

import pytest
from textual.widgets import Static

from navin.agent.hook import AgentHookContext
from navin.agent.runner import AgentLoopGuard, AgentRunner, AgentRunSpec
from navin.agent.tools.board import BoardTool
from navin.tui.widgets import ApprovalCard, ToolCall
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.progress_events import build_tool_event_finish_payloads
from tests.test_long_task_continuation import ScriptedProvider, registry, tool_call
from tests.test_tui_activity import ActivityHost


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
            assert "Failed" not in row._head_text()
            assert "×" not in row.copy_text()
            assert "src/service.py" in row.copy_text()
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


def test_real_board_permission_error_remains_an_error():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 24)):
            await app.block.tool_event(
                "denied", "board", "error", {"action": "move", "status": "done"},
                None, "Permission denied while writing the board file.", None,
            )
            row = app.block.query_one(ToolCall)
            assert "Failed: Board move" in row._head_text()
            assert "Permission denied" in row.copy_text()
            assert row.has_class("-error")
            assert not row.validation_pending
            await app.block.finish(latency_ms=1, model="test", preset=None)
            assert "1 failed" in str(app.block.query_one(".assistant-foot", Static).content)
    asyncio.run(run())
