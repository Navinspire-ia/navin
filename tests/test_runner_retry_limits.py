# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""P3-4: the empty-response and length-recovery limits are per-run settings.

They were module constants, so a caller that wanted a more patient run (long
document generation) or a stricter one (cheap background job) had no lever.
``AgentRunSpec.max_empty_retries`` / ``max_length_recoveries`` now carry them,
defaulting to the historical values.
"""

from __future__ import annotations

import unittest
from typing import Any

from navin.agent.hook import AgentHook
from navin.agent.runner import (
    _MAX_EMPTY_RETRIES,
    _MAX_LENGTH_RECOVERIES,
    AgentRunner,
    AgentRunSpec,
)
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import GenerationSettings, LLMResponse
from navin.utils.llm_runtime import LLMRuntime


class _FakeProvider:
    """Replays a scripted response list; repeats the last one when exhausted."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat_with_retry(**kwargs)


def _runtime(provider: Any) -> LLMRuntime:
    return LLMRuntime(
        provider=provider,
        model="test-model",
        generation=GenerationSettings(),
        context_window_tokens=128_000,
    )


def _spec(provider: Any, **overrides: Any) -> AgentRunSpec:
    return AgentRunSpec(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=ToolRegistry(),
        runtime=_runtime(provider),
        max_iterations=12,
        max_tool_result_chars=2000,
        hook=AgentHook(),
        **overrides,
    )


class SpecDefaultsTest(unittest.TestCase):
    def test_defaults_match_the_historical_constants(self) -> None:
        spec = _spec(_FakeProvider([LLMResponse(content="ok", finish_reason="stop")]))
        self.assertEqual(spec.max_empty_retries, _MAX_EMPTY_RETRIES)
        self.assertEqual(spec.max_length_recoveries, _MAX_LENGTH_RECOVERIES)


class EmptyRetryLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_limit_bounds_the_provider_calls(self) -> None:
        # An always-blank provider: N normal attempts, then one finalization
        # retry, then the run gives up. The knob must move the total.
        for limit, expected_calls in ((1, 2), (4, 5)):
            provider = _FakeProvider([LLMResponse(content="", finish_reason="stop")])
            await AgentRunner().run(_spec(provider, max_empty_retries=limit))
            self.assertEqual(
                provider.calls,
                expected_calls,
                f"max_empty_retries={limit} should mean {expected_calls} calls",
            )


class LengthRecoveryLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_limit_bounds_the_continuations(self) -> None:
        # Every response is a truncated chunk: M continuations are attempted,
        # then the truncated output is accepted as final.
        for limit, expected_calls in ((1, 2), (3, 4)):
            provider = _FakeProvider(
                [LLMResponse(content="chunk", finish_reason="length")]
            )
            result = await AgentRunner().run(
                _spec(provider, max_length_recoveries=limit)
            )
            self.assertEqual(
                provider.calls,
                expected_calls,
                f"max_length_recoveries={limit} should mean {expected_calls} calls",
            )
            self.assertIn("chunk", result.final_content or "")


if __name__ == "__main__":
    unittest.main()
