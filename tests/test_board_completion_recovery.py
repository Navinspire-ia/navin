# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A missing test run is an actionable prerequisite, not a broken board."""

import asyncio

from navin.agent.runner import AgentLoopGuard, AgentRunner, AgentRunSpec
from navin.agent.tools.board import BoardTool
from navin.board.store import ProjectBoardStore
from navin.quality.verification_log import record_verification
from navin.tui.runtime import UiToolEvent
from navin.tui.widgets import ToolCall
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.tool_hints import is_validation_pending
from tests.test_long_task_continuation import ScriptedProvider, registry, tool_call
from tests.test_tui_queue import make_app

LEGACY_ERROR = (
    "Error: cannot mark t-f8289ada done: the last verification passed but did not run any tests, "
    "and this step declares validation=test. Run `test_run action=run` first."
)


def test_real_board_gate_does_not_throttle_retries_and_still_requires_tests(tmp_path):
    async def run():
        store = ProjectBoardStore(tmp_path)
        task = store.create_task(
            title="Required checks", actor="agent", actor_type="agent",
            validation="test", evidence="verification output",
        )
        record_verification(tmp_path, source="verify", ok=True, tests_ran=False)
        tools = registry(tmp_path)
        tools.register(BoardTool(workspace=tmp_path))
        spec = AgentRunSpec(
            initial_messages=[], tools=tools, loop_guard=AgentLoopGuard(),
            max_iterations=2, max_tool_result_chars=4000, fail_on_tool_error=True,
            runtime=LLMRuntime.capture(ScriptedProvider([]), "test-quality", context_window_tokens=128000),
        )
        call = tool_call("board", action="move", task_id=task["id"], status="done").tool_calls[0]
        failures = {}
        for _ in range(4):
            result, event, fatal = await AgentRunner()._dispatch_tool_call(spec, call, failures, {})
            assert fatal is None
            assert is_validation_pending("board", result)
            assert "test_run action=run" in result
            assert event["error_kind"] == "validation_required"
            assert event["progress"] == "none"
            assert store.get_task(task["id"])["status"] != "done"
        assert not failures
        record_verification(tmp_path, source="test_run", ok=True, tests_ran=True)
        result, event, fatal = await AgentRunner()._dispatch_tool_call(spec, call, failures, {})
        assert fatal is None
        assert event["status"] == "ok", result
        assert store.get_task(task["id"])["status"] == "done"
    asyncio.run(run())


def test_saved_board_prerequisite_and_retries_use_one_card(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            args = {"action": "move", "task_id": "t-f8289ada", "status": "done"}
            for index in range(4):
                call_id = f"attempt-{index}"
                await app._on_runtime_event(UiToolEvent(call_id, "board", "start", args))
                await app._on_runtime_event(UiToolEvent(call_id, "board", "error", args, error=LEGACY_ERROR))
            await pilot.pause()
            assert len(app.query(ToolCall)) == 1
            card = app.query_one(ToolCall)
            assert card.validation_pending
            assert "Validation pending" in card._head_text()
            assert "Failed" not in card._head_text()
            assert "test_run action=run" in card.copy_text()
            await app._on_runtime_event(UiToolEvent("success", "board", "start", args))
            await app._on_runtime_event(UiToolEvent("success", "board", "end", args, result="Task completed"))
            assert len(app.query(ToolCall)) == 1
            assert not card.validation_pending
            assert card.phase == "end"
    asyncio.run(run())


def test_board_permission_failure_is_never_a_validation_prerequisite():
    assert not is_validation_pending("board", "Error: cannot mark task done: Permission denied")
    assert not is_validation_pending("exec", LEGACY_ERROR)
