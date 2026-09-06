"""Mid-turn injection drains must not block on running subagents.

The pending-queue drain used to wait up to 300s after every tool batch when a
subagent was still running, freezing exactly the flows spawn exists to
parallelise. The contract now: drains between tool batches and on error paths
pass wait=False to the injection callback; only the end-of-turn drain (after
the final response) keeps wait=True so background results still land in the
same turn.
"""

from __future__ import annotations

import unittest
from typing import Any

from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime


class _NoopTool(Tool):
    @property
    def name(self) -> str:
        return "noop"

    @property
    def description(self) -> str:
        return "does nothing"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


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


class DrainWaitFlagTest(unittest.IsolatedAsyncioTestCase):
    async def _run_with_callback(self, callback: Any) -> None:
        registry = ToolRegistry()
        registry.register(_NoopTool())
        provider = _ScriptedProvider([
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="c1", name="noop", arguments={})],
            ),
            LLMResponse(content="done"),
        ])
        await AgentRunner().run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=registry,
            runtime=_runtime(provider),
            max_iterations=4,
            max_tool_result_chars=10_000,
            injection_callback=callback,
        ))

    async def test_mid_turn_drain_is_non_blocking_and_final_drain_waits(self) -> None:
        seen: list[bool] = []

        async def callback(*, limit: int = 10, wait: bool = True) -> list[dict[str, Any]]:
            seen.append(wait)
            return []

        await self._run_with_callback(callback)
        # One drain after the tool batch (must not wait), one after the final
        # response (must wait for still-running subagents).
        self.assertIn(False, seen, "the post-tools drain should pass wait=False")
        self.assertTrue(seen[-1], "the end-of-turn drain should keep wait=True")

    async def test_a_callback_without_wait_still_works(self) -> None:
        calls: list[int] = []

        async def legacy_callback(*, limit: int = 10) -> list[dict[str, Any]]:
            calls.append(limit)
            return []

        await self._run_with_callback(legacy_callback)
        self.assertTrue(calls, "the legacy callback should still be invoked")

    async def test_a_var_keyword_callback_receives_both(self) -> None:
        received: list[dict[str, Any]] = []

        async def flexible_callback(**kwargs: Any) -> list[dict[str, Any]]:
            received.append(kwargs)
            return []

        await self._run_with_callback(flexible_callback)
        self.assertTrue(received)
        self.assertIn("limit", received[0])
        self.assertIn("wait", received[0])


if __name__ == "__main__":
    unittest.main()
