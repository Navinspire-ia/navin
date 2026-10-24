# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The input budget of a long session, with the numbers that went wrong.

A Kimi K3 session had a 200k window (its fallback list holds a 200k model) and a
128k output ceiling. The whole ceiling was taken out of the window: 71k of input
budget, so old tool results were blanked from 15.6k tokens on, on every request.
The budget is now 90 % of the window for every model: 180k of 200k, 900k of 1M.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from navin.agent.context_governance import ContextGovernanceConfig, ContextGovernor
from navin.agent.hook import AgentHook
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.token_budget import (
    OUTPUT_RESERVE,
    SAFETY_BUFFER,
    SOFT_CLEAR_FLOOR,
    input_budget_for,
    output_reserve,
    room_for_output,
    soft_clear_limit,
)
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.config.schema import ToolResultClearing
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime


class InputBudgetTest(unittest.TestCase):
    def test_the_budget_is_90_percent_of_the_window_for_every_size(self) -> None:
        for window, expected in (
            (1_000_000, 900_000), (500_000, 450_000), (300_000, 270_000),
            (200_000, 180_000), (128_000, 115_200),
        ):
            self.assertEqual(input_budget_for(window, 128_000), expected, window)
            self.assertEqual(input_budget_for(window, 32_000), expected, window)

    def test_the_output_ceiling_of_the_model_no_longer_eats_the_input_budget(self) -> None:
        # 128k of output reserved out of a 200k window left 71k.
        self.assertGreater(input_budget_for(200_000, 128_000), 170_000)

    def test_a_small_window_keeps_room_for_the_answer(self) -> None:
        self.assertEqual(input_budget_for(64_000, 32_000), 64_000 - OUTPUT_RESERVE - SAFETY_BUFFER)
        self.assertEqual(input_budget_for(8_192, 4_096), 8_192 - 4_096 - SAFETY_BUFFER)

    def test_a_small_output_is_kept_and_bad_values_fall_back(self) -> None:
        self.assertEqual(output_reserve(4_096, 200_000), 4_096)
        self.assertEqual(output_reserve(None, 200_000), 4_096)
        self.assertEqual(output_reserve("not a number", 200_000), 4_096)
        self.assertEqual(output_reserve(0, 200_000), 1)
        self.assertEqual(output_reserve(128_000, 200_000), OUTPUT_RESERVE)

    def test_no_window_means_no_budget(self) -> None:
        self.assertEqual(input_budget_for(0, 128_000), 0)
        self.assertEqual(input_budget_for(2_000, 4_096), 0)


class SoftClearLimitTest(unittest.TestCase):
    def test_nothing_is_blanked_early_by_default(self) -> None:
        self.assertEqual(ToolResultClearing().soft_clear_ratio, 0)
        self.assertEqual(soft_clear_limit(180_000, ToolResultClearing().soft_clear_ratio), 0)

    def test_a_configured_share_on_a_large_budget_has_a_floor(self) -> None:
        self.assertEqual(soft_clear_limit(180_000, 0.22), SOFT_CLEAR_FLOOR)

    def test_a_big_window_keeps_the_ratio(self) -> None:
        self.assertEqual(soft_clear_limit(900_000, 0.5), 450_000)

    def test_a_small_budget_keeps_the_configured_share(self) -> None:
        self.assertEqual(soft_clear_limit(20_000, 0.22), 4_400)
        self.assertEqual(soft_clear_limit(10_000, 0.45), 4_500)
        self.assertEqual(soft_clear_limit(SOFT_CLEAR_FLOOR, 0.22), int(SOFT_CLEAR_FLOOR * 0.22))

    def test_zero_means_only_on_overflow(self) -> None:
        self.assertEqual(soft_clear_limit(100_000, 0), 0)
        self.assertEqual(soft_clear_limit(0, 0.22), 0)


class RoomForOutputTest(unittest.TestCase):
    def test_nothing_changes_far_from_the_window(self) -> None:
        self.assertEqual(room_for_output(128_000, 1_000_000, 100_000), 128_000)
        self.assertEqual(room_for_output(32_000, 200_000, 60_000), 32_000)

    def test_the_output_shrinks_as_the_prompt_nears_the_window(self) -> None:
        self.assertEqual(room_for_output(128_000, 200_000, 100_000), 200_000 - 100_000 - 8_000 - 1_024)

    def test_it_never_drops_below_a_usable_floor(self) -> None:
        self.assertEqual(room_for_output(128_000, 200_000, 199_000), 4_096)

    def test_unknown_sizes_leave_the_request_alone(self) -> None:
        self.assertEqual(room_for_output(128_000, 200_000, 0), 128_000)
        self.assertEqual(room_for_output(128_000, 0, 50_000), 128_000)
        self.assertIsNone(room_for_output(None, 200_000, 50_000))


class _NoTools:
    def get_definitions(self):
        return []


class _Provider:
    generation = type("G", (), {"max_tokens": 128_000})()

    def estimate_prompt_tokens(self, messages, tools, model):  # noqa: ARG002
        return 0, "stub"


class GovernorBudgetTest(unittest.TestCase):
    def _config(self, **overrides) -> ContextGovernanceConfig:
        values = dict(
            provider=_Provider(), model="m", tools=_NoTools(), workspace=None, session_key="s",
            max_tool_result_chars=16_000, context_window_tokens=200_000, max_tokens=128_000,
            clearing=ToolResultClearing(),
        )
        values.update(overrides)
        return ContextGovernanceConfig(**values)

    def test_a_128k_output_ceiling_no_longer_eats_the_input_budget(self) -> None:
        self.assertEqual(ContextGovernor.input_budget(self._config()), 180_000)

    def test_every_model_gets_90_percent_of_its_window(self) -> None:
        for model in ("anthropic/claude-sonnet-5.5", "x-ai/grok-4.6", "moonshotai/kimi-k3"):
            config = self._config(context_window_tokens=1_000_000, model=model)
            self.assertEqual(ContextGovernor.input_budget(config), 900_000, model)
        grok = self._config(context_window_tokens=500_000, model="x-ai/grok-4.6")
        self.assertEqual(ContextGovernor.input_budget(grok), 450_000)

    def test_an_explicit_block_limit_still_wins(self) -> None:
        self.assertEqual(ContextGovernor.input_budget(self._config(context_block_limit=50_000)), 50_000)


class _Recorder:
    """A provider that answers from a script and keeps the arguments it was called with."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.requests.append(kwargs)
        return self._responses[min(len(self.requests) - 1, len(self._responses) - 1)]

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat_with_retry(**kwargs)


class _Look(Tool):
    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "list"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


class RequestedOutputTest(unittest.TestCase):
    def _requests(self, first_prompt_tokens: int, window: int) -> list[dict[str, Any]]:
        call = LLMResponse(
            content="", finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id="1", name="list_dir", arguments={})],
            usage={"prompt_tokens": first_prompt_tokens, "completion_tokens": 20,
                   "total_tokens": first_prompt_tokens + 20},
        )
        done = LLMResponse(content="done", finish_reason="stop",
                           usage={"prompt_tokens": first_prompt_tokens + 500, "completion_tokens": 5,
                                  "total_tokens": first_prompt_tokens + 505})
        provider = _Recorder([call, done])
        tools = ToolRegistry()
        tools.register(_Look())
        asyncio.run(AgentRunner().run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "look"}],
            tools=tools,
            runtime=LLMRuntime(provider=provider, model="m",
                               generation=GenerationSettings(max_tokens=128_000),
                               context_window_tokens=window),
            max_iterations=5, max_tool_result_chars=2000, hook=AgentHook(),
        )))
        return provider.requests

    def test_the_requested_output_shrinks_once_the_prompt_is_large(self) -> None:
        requests = self._requests(first_prompt_tokens=150_000, window=200_000)
        self.assertEqual(requests[0]["max_tokens"], 128_000)
        self.assertEqual(requests[1]["max_tokens"], 200_000 - 150_000 - 8_000 - 1_024)

    def test_the_requested_output_is_untouched_when_there_is_room(self) -> None:
        requests = self._requests(first_prompt_tokens=40_000, window=1_000_000)
        self.assertEqual([r["max_tokens"] for r in requests], [128_000, 128_000])


if __name__ == "__main__":
    unittest.main()
