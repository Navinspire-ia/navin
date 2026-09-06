"""Sustained goals get a long finite LLM wall clock, never an infinite one.

With the wall clock disabled (the old 0.0), one hung request held the session
lock until the gateway restarted, and a goal session is exactly the one nobody
is watching. The cap is long (default 30 min) so legitimate deep turns pass,
and NAVIN_GOAL_LLM_TIMEOUT_S=0 keeps the explicit opt-out.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock

from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import GenerationSettings, LLMResponse
from navin.session.goal_state import (
    GOAL_STATE_KEY,
    runner_wall_llm_timeout_s,
)
from navin.utils.llm_runtime import LLMRuntime


class _FakeSessions:
    def get_or_create(self, key):  # pragma: no cover - not used with metadata=
        raise AssertionError("metadata was provided; the store must not be hit")


def _goal_metadata(active: bool = True) -> dict:
    return {GOAL_STATE_KEY: json.dumps({
        "status": "active" if active else "done",
        "objective": "keep the service green",
    })}


class GoalWallTimeoutTest(unittest.TestCase):
    def test_a_goal_turn_gets_a_finite_cap(self) -> None:
        cap = runner_wall_llm_timeout_s(
            _FakeSessions(), "s1", metadata=_goal_metadata(),
        )
        self.assertIsNotNone(cap)
        self.assertGreater(cap, 0)
        self.assertLessEqual(cap, 3600)

    def test_a_normal_turn_keeps_the_default_ladder(self) -> None:
        self.assertIsNone(runner_wall_llm_timeout_s(
            _FakeSessions(), "s1", metadata=_goal_metadata(active=False),
        ))

    def test_the_env_override_is_respected(self) -> None:
        with mock.patch.dict(os.environ, {"NAVIN_GOAL_LLM_TIMEOUT_S": "900"}):
            self.assertEqual(runner_wall_llm_timeout_s(
                _FakeSessions(), "s1", metadata=_goal_metadata(),
            ), 900.0)

    def test_zero_keeps_the_explicit_opt_out(self) -> None:
        with mock.patch.dict(os.environ, {"NAVIN_GOAL_LLM_TIMEOUT_S": "0"}):
            cap = runner_wall_llm_timeout_s(
                _FakeSessions(), "s1", metadata=_goal_metadata(),
            )
        # 0 flows to the runner, whose _wall_timeout_s turns <=0 into None
        # (no wait_for), the historical opt-out.
        self.assertEqual(cap, 0.0)


class _HangingProvider:
    """chat_with_retry never returns: the hung-gateway case."""

    async def chat_with_retry(self, **kwargs):
        await asyncio.sleep(3600)


class FinalizationTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_hung_finalization_request_times_out_as_an_error(self) -> None:
        spec = AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=ToolRegistry(),
            runtime=LLMRuntime(
                provider=_HangingProvider(),
                model="test-model",
                generation=GenerationSettings(temperature=0.2, max_tokens=128),
                context_window_tokens=100_000,
            ),
            max_iterations=2,
            max_tool_result_chars=4000,
            llm_timeout_s=0.05,
        )
        response = await AgentRunner()._request_no_tools(spec, list(spec.initial_messages))
        self.assertIsInstance(response, LLMResponse)
        self.assertEqual(response.finish_reason, "error")
        self.assertEqual(response.error_kind, "timeout")


if __name__ == "__main__":
    unittest.main()
