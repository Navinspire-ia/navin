# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Mid-turn injection drains must not block on running subagents.

The pending-queue drain used to wait up to 300s after every tool batch when a
subagent was still running, freezing exactly the flows spawn exists to
parallelise. The contract now: drains between tool batches and on error paths
pass wait=False to the injection callback; only the end-of-turn drain (after
the final response) keeps wait=True so background results still land in the
same turn.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from navin.agent.loop import AgentLoop
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools import exec_session
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


class OwedResultWaitTest(unittest.IsolatedAsyncioTestCase):
    """The end-of-turn wait ends once no subagent result is owed any more.

    It was one blind five-minute wait whenever a subagent task was still
    alive, even when its result had already reached the model.
    """

    @staticmethod
    def _loop(owed: list[int]) -> AgentLoop:
        loop = AgentLoop.__new__(AgentLoop)
        loop.subagents = SimpleNamespace(pending_result_count=lambda _key: owed[0])
        return loop

    async def test_nothing_owed_ends_the_wait_at_once(self) -> None:
        started = time.monotonic()
        got = await self._loop([0])._wait_for_owed_result("cli:one", asyncio.Queue(), poll=0.01)
        self.assertIsNone(got)
        self.assertLess(time.monotonic() - started, 1.0)

    async def test_a_result_in_flight_is_still_taken(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        asyncio.get_running_loop().call_later(0.06, queue.put_nowait, "result")
        got = await self._loop([0])._wait_for_owed_result("cli:one", queue, poll=0.05)
        self.assertEqual(got, "result")

    async def test_an_owed_result_keeps_the_wait_open_until_it_lands(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        asyncio.get_running_loop().call_later(0.1, queue.put_nowait, "late result")
        got = await self._loop([1])._wait_for_owed_result("cli:one", queue, poll=0.01)
        self.assertEqual(got, "late result")

    async def test_the_wait_keeps_its_ceiling(self) -> None:
        got = await self._loop([1])._wait_for_owed_result(
            "cli:one", asyncio.Queue(), timeout=0.05, poll=0.01,
        )
        self.assertIsNone(got)

    def test_a_manager_without_the_owed_count_falls_back(self) -> None:
        loop = AgentLoop.__new__(AgentLoop)
        loop.subagents = SimpleNamespace(get_running_count_by_session=lambda _key: 2)
        self.assertEqual(loop._owed_subagent_results("cli:one"), 2)


class LiveBackgroundCommandTest(unittest.TestCase):
    """Only this conversation's commands (or ones of no known owner) count."""

    def _live(self, *owners: str | None) -> bool:
        rows = [SimpleNamespace(owner_session_key=owner) for owner in owners]
        manager = exec_session.DEFAULT_EXEC_SESSION_MANAGER
        with patch.object(manager, "running_snapshot", return_value=rows):
            return AgentLoop._live_background_commands("cli:one")

    def test_owners(self) -> None:
        self.assertFalse(self._live())
        self.assertFalse(self._live("cli:other"))
        self.assertTrue(self._live("cli:one"))
        self.assertTrue(self._live(None))


if __name__ == "__main__":
    unittest.main()
