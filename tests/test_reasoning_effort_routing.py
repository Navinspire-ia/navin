# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Reasoning effort is routed per request, not forwarded from config verbatim.

Agent turns force thinking off (GLM/Grok treat low/medium/high as the same
on-switch). Plan turns think at least "high". Explicit xhigh/max/adaptive
survive the first call only. The integration tests drive the real runner.
"""

from __future__ import annotations

import unittest
from typing import Any

from navin.agent.hook import AgentHook
from navin.agent.reasoning_router import route_reasoning_effort
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime


class RoutingRulesTest(unittest.TestCase):
    def test_no_signals_turns_agent_thinking_off(self) -> None:
        self.assertEqual(route_reasoning_effort(None), "none")
        self.assertEqual(route_reasoning_effort("medium"), "none")
        self.assertEqual(route_reasoning_effort("high"), "none")

    def test_plan_mode_floors_at_high(self) -> None:
        self.assertEqual(route_reasoning_effort(None, composer_mode="plan"), "high")
        self.assertEqual(route_reasoning_effort("low", composer_mode="plan"), "high")
        self.assertEqual(route_reasoning_effort("high", composer_mode="plan"), "high")

    def test_agent_mode_ignores_configured_high(self) -> None:
        for mode in ("agent", "ask", "chat", None):
            self.assertEqual(route_reasoning_effort(None, composer_mode=mode), "none")
            self.assertEqual(route_reasoning_effort("low", composer_mode=mode), "none")
            self.assertEqual(route_reasoning_effort("high", composer_mode=mode), "none")

    def test_explicit_think_hard_survives_the_first_call(self) -> None:
        for choice in ("adaptive", "xhigh", "max"):
            self.assertEqual(
                route_reasoning_effort(choice, composer_mode="agent"),
                choice,
            )
            self.assertEqual(
                route_reasoning_effort(choice, composer_mode="agent", tool_followup=True),
                "none",
            )

    def test_plan_escalation_raises_one_step_capped_at_high(self) -> None:
        self.assertEqual(
            route_reasoning_effort("low", composer_mode="plan", escalations=1),
            "high",
        )

    def test_agent_escalation_does_not_turn_thinking_back_on(self) -> None:
        self.assertEqual(
            route_reasoning_effort("low", tool_followup=True, escalations=3),
            "none",
        )

    def test_tool_followup_turns_thinking_off(self) -> None:
        self.assertEqual(
            route_reasoning_effort("high", tool_followup=True),
            "none",
        )
        self.assertEqual(
            route_reasoning_effort("low", composer_mode="plan", tool_followup=True),
            "none",
        )
        self.assertEqual(route_reasoning_effort(None, tool_followup=True), "none")

    def test_verify_red_followup_does_not_think(self) -> None:
        self.assertEqual(
            route_reasoning_effort("low", tool_followup=True, escalations=1),
            "none",
        )

    def test_empty_retry_disables_thinking(self) -> None:
        self.assertEqual(route_reasoning_effort("high", empty_retry=True), "none")
        self.assertEqual(route_reasoning_effort(None, empty_retry=True), "none")
        self.assertEqual(
            route_reasoning_effort("xhigh", empty_retry=True),
            "xhigh",
        )

    def test_explicit_off_still_passes_through(self) -> None:
        self.assertEqual(
            route_reasoning_effort("none", tool_followup=True, escalations=3),
            "none",
        )


class _EffortCapturingProvider:
    """Replays a script and records the effort of every request."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.efforts: list[str | None] = []

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.efforts.append(kwargs.get("reasoning_effort"))
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        kwargs.pop("on_content_delta", None)
        kwargs.pop("on_thinking_delta", None)
        kwargs.pop("on_stream_recover", None)
        return await self.chat_with_retry(**kwargs)


def _runtime(provider: Any, effort: str | None = None) -> LLMRuntime:
    return LLMRuntime(
        provider=provider,
        model="test-model",
        generation=GenerationSettings(reasoning_effort=effort),
        context_window_tokens=128_000,
    )


class _NamedTool(Tool):
    def __init__(self, name: str, result: str | list[str]) -> None:
        self._tool_name = name
        self._result = result
        self.calls = 0

    @property
    def name(self) -> str:
        return self._tool_name

    @property
    def description(self) -> str:
        return self._tool_name

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, **kwargs: Any) -> str:
        self.calls += 1
        if isinstance(self._result, list):
            return self._result[min(self.calls - 1, len(self._result) - 1)]
        return self._result


def _spec(provider: Any, registry: ToolRegistry, **overrides: Any) -> AgentRunSpec:
    base: dict[str, Any] = dict(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=registry,
        runtime=_runtime(provider, overrides.pop("effort", None)),
        max_iterations=8,
        max_tool_result_chars=4000,
        hook=AgentHook(),
    )
    base.update(overrides)
    return AgentRunSpec(**base)


class RunnerRoutesEffortTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_plan_turn_requests_high_effort(self) -> None:
        provider = _EffortCapturingProvider([LLMResponse(content="plan ready")])
        runner = AgentRunner()
        result = await runner.run(_spec(
            provider,
            ToolRegistry(),
            composer_mode="plan",
            plan_read_only=True,
        ))
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(provider.efforts, ["high"])

    async def test_an_agent_turn_turns_thinking_off(self) -> None:
        provider = _EffortCapturingProvider([LLMResponse(content="done")])
        runner = AgentRunner()
        await runner.run(_spec(provider, ToolRegistry(), composer_mode="agent"))
        self.assertEqual(provider.efforts, ["none"])

    async def test_a_verify_red_retry_stays_fast(self) -> None:
        registry = ToolRegistry()
        registry.register(_NamedTool("edit_file", "edited"))
        verifier = _NamedTool("verify", ["FAIL - 2 tests failed", "PASS - tests green"])
        registry.register(verifier)
        provider = _EffortCapturingProvider([
            LLMResponse(content=None, tool_calls=[
                ToolCallRequest(id="1", name="edit_file", arguments={}),
                ToolCallRequest(id="2", name="verify", arguments={"action": "check"}),
            ]),
            LLMResponse(content="done"),
            LLMResponse(content=None, tool_calls=[
                ToolCallRequest(id="3", name="edit_file", arguments={}),
                ToolCallRequest(id="4", name="verify", arguments={"action": "check"}),
            ]),
            LLMResponse(content="done"),
        ])
        runner = AgentRunner()
        result = await runner.run(_spec(
            provider,
            registry,
            effort="low",
            requires_verify_before_done=True,
        ))

        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(verifier.calls, 2)
        self.assertTrue(any("Verification failed" in str(m.get("content")) for m in result.messages))
        self.assertTrue(all(effort == "none" for effort in provider.efforts))

    async def test_after_tools_the_next_call_stays_off(self) -> None:
        registry = ToolRegistry()
        registry.register(_NamedTool("read_file", "ok"))
        provider = _EffortCapturingProvider([
            LLMResponse(content=None, tool_calls=[
                ToolCallRequest(id="1", name="read_file", arguments={"path": "a.py"}),
            ]),
            LLMResponse(content="done"),
        ])
        await AgentRunner().run(_spec(provider, registry, effort="high"))
        self.assertEqual(provider.efforts, ["none", "none"])

    async def test_identical_reads_are_blocked_after_two_successes(self) -> None:
        registry = ToolRegistry()
        reader = _NamedTool("read_file", "contents of a.py")
        registry.register(reader)
        same = {"path": "a.py"}
        provider = _EffortCapturingProvider([
            LLMResponse(content=None, tool_calls=[
                ToolCallRequest(id="1", name="read_file", arguments=same),
            ]),
            LLMResponse(content=None, tool_calls=[
                ToolCallRequest(id="2", name="read_file", arguments=same),
            ]),
            LLMResponse(content=None, tool_calls=[
                ToolCallRequest(id="3", name="read_file", arguments=same),
            ]),
            LLMResponse(content="done from what I already have"),
        ])
        result = await AgentRunner().run(_spec(provider, registry, effort="low"))
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(reader.calls, 2)
        blocked = [
            event for event in result.tool_events
            if event.get("detail") == "repeated identical read blocked"
        ]
        self.assertEqual(len(blocked), 1)

    async def test_audit_of_distinct_files_can_finish_without_an_edit(self) -> None:
        registry = ToolRegistry()
        reader = _NamedTool("read_file", "ok")
        registry.register(reader)
        responses = [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id=str(i),
                        name="read_file",
                        arguments={"path": f"file-{i}.py"},
                    )
                ],
            )
            for i in range(1, 16)
        ]
        provider = _EffortCapturingProvider([
            *responses, LLMResponse(content="All 15 files audited; findings recorded."),
        ])
        result = await AgentRunner().run(_spec(
            provider, registry, effort="none", max_iterations=30,
        ))
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(reader.calls, 15)
        self.assertEqual(result.final_content, "All 15 files audited; findings recorded.")


if __name__ == "__main__":
    unittest.main()
