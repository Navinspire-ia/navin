"""A hung tool costs one tool call, never the whole turn.

Only shell/MCP had their own timeouts; every other tool.execute was awaited
bare, so one unreachable server inside a tool hung the session forever. The
runner now wraps each execution in a wall clock (default 1h, per-tool
overridable) and reports the hit as a soft, actionable tool error.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.agent.tools.shell import ExecTool
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime


class _HangingTool(Tool):
    @property
    def name(self) -> str:
        return "hang"

    @property
    def description(self) -> str:
        return "never returns"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        await asyncio.sleep(3600)
        return "unreachable"


class _PatientTool(_HangingTool):
    """A tool that declares it may legitimately run forever."""

    wall_timeout_s = 0

    @property
    def name(self) -> str:
        return "patient"

    async def execute(self, **kwargs: Any) -> str:
        await asyncio.sleep(0.2)
        return "slow but fine"


class _ScriptedProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    async def chat(self, messages, tools=None, model=None, max_tokens=4096,
                   temperature=0.7, reasoning_effort=None, tool_choice=None):
        return self._responses.pop(0)

    async def chat_with_retry(self, **kwargs: Any):
        allowed = {
            "messages", "tools", "model", "max_tokens", "temperature",
            "reasoning_effort", "tool_choice",
        }
        return await self.chat(**{k: v for k, v in kwargs.items() if k in allowed})


def _runtime(provider: Any) -> LLMRuntime:
    return LLMRuntime(
        provider=provider,
        model="test-model",
        generation=GenerationSettings(temperature=0.2, max_tokens=512),
        context_window_tokens=100_000,
    )


def _spec(tool: Tool, *, tool_timeout_s: float | None) -> AgentRunSpec:
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider([
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="c1", name=tool.name, arguments={})],
        ),
        LLMResponse(content="done"),
    ])
    return AgentRunSpec(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=registry,
        runtime=_runtime(provider),
        max_iterations=4,
        max_tool_result_chars=10_000,
        tool_timeout_s=tool_timeout_s,
    )


class ToolWallTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_hung_tool_is_cut_and_the_turn_continues(self) -> None:
        result = await AgentRunner().run(_spec(_HangingTool(), tool_timeout_s=0.05))
        self.assertEqual(result.stop_reason, "completed")
        tool_messages = [m for m in result.messages if m.get("role") == "tool"]
        self.assertTrue(tool_messages)
        self.assertIn("cancelled after", str(tool_messages[0].get("content")))

    async def test_a_tool_that_opts_out_is_never_cut(self) -> None:
        result = await AgentRunner().run(_spec(_PatientTool(), tool_timeout_s=0.05))
        self.assertEqual(result.stop_reason, "completed")
        tool_messages = [m for m in result.messages if m.get("role") == "tool"]
        self.assertIn("slow but fine", str(tool_messages[0].get("content")))

    def test_exec_owns_its_own_clock(self) -> None:
        # exec manages its own timeout and child cleanup; the runner cap
        # must not cancel it from outside.
        spec = _spec(_HangingTool(), tool_timeout_s=None)
        self.assertIsNone(
            AgentRunner._tool_wall_timeout_s(spec, ExecTool(working_dir="."))
        )

    def test_the_default_cap_is_one_hour(self) -> None:
        spec = _spec(_HangingTool(), tool_timeout_s=None)
        self.assertEqual(
            AgentRunner._tool_wall_timeout_s(spec, _HangingTool()), 3600.0,
        )


if __name__ == "__main__":
    unittest.main()
