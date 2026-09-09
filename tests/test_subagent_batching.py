# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A subagent must batch its tool calls the way the parent loop does.

``AgentRunSpec.concurrent_tools`` defaults to False and only ``AgentLoop`` ever
set it, so subagents ran their reads strictly one at a time. Exploration is
most of what a subagent does, and every extra round trip re-sends its entire
prompt, so the default quietly cost one full request per file read.
"""

from __future__ import annotations

import asyncio
import queue
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from navin.agent.subagent import SubagentManager
from navin.providers.base import ToolCallRequest


class _Bus:
    def __init__(self) -> None:
        self.outbound: queue.Queue = queue.Queue()

    async def publish_inbound(self, msg) -> None:
        return None


class _RecordingRunner:
    def __init__(self) -> None:
        self.specs: list[AgentRunSpec] = []

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        self.specs.append(spec)
        return AgentRunResult(final_content="done", messages=[], stop_reason="end")


class SubagentBatchingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runner = _RecordingRunner()
        self.manager = SubagentManager(
            workspace=Path(self._tmp.name),
            bus=_Bus(),  # type: ignore[arg-type]
            max_tool_result_chars=4000,
        )
        self.manager.runner = self.runner  # type: ignore[assignment]

    async def _spawn(self) -> None:
        runtime = mock.Mock()
        runtime.model = "test-model"
        runtime.with_generation_overrides = lambda **_: runtime
        await self.manager.spawn(
            task="read three files and report what they do",
            label="Explorer",
            runtime=runtime,
            origin_channel="websocket",
            origin_chat_id="chat-live",
            session_key="websocket:chat-live",
        )
        await asyncio.gather(
            *list(self.manager._running_tasks.values()), return_exceptions=True
        )

    async def test_a_subagent_run_is_allowed_to_batch(self) -> None:
        await self._spawn()
        self.assertEqual(len(self.runner.specs), 1)
        self.assertTrue(self.runner.specs[0].concurrent_tools)


class _Tool:
    def __init__(self, name: str, *, safe: bool = True) -> None:
        self.name = name
        self._safe = safe

    def call_concurrency_safe(self, _arguments) -> bool:
        return self._safe


class _Registry:
    def __init__(self, *tools: _Tool) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> _Tool | None:
        return self._tools.get(name)

    def get_definitions(self) -> list[dict]:
        return []


def _spec(registry: _Registry, *, concurrent: bool) -> AgentRunSpec:
    return AgentRunSpec(
        initial_messages=[],
        tools=registry,  # type: ignore[arg-type]
        runtime=mock.Mock(),
        max_iterations=1,
        max_tool_result_chars=1000,
        concurrent_tools=concurrent,
    )


def _calls(*names: str) -> list[ToolCallRequest]:
    return [
        ToolCallRequest(id=f"c{i}", name=name, arguments={})
        for i, name in enumerate(names)
    ]


class BatchingStaysSafeTest(unittest.TestCase):
    """Turning the flag on must not parallelize what declares itself unsafe."""

    def setUp(self) -> None:
        self.runner = AgentRunner()
        self.registry = _Registry(
            _Tool("read_file"),
            _Tool("grep"),
            _Tool("apply_patch", safe=False),
        )

    def test_reads_land_in_one_batch(self) -> None:
        batches = self.runner._partition_tool_batches(
            _spec(self.registry, concurrent=True),
            _calls("read_file", "grep", "read_file"),
        )
        self.assertEqual([len(b) for b in batches], [3])

    def test_an_unsafe_call_breaks_the_batch(self) -> None:
        batches = self.runner._partition_tool_batches(
            _spec(self.registry, concurrent=True),
            _calls("read_file", "apply_patch", "grep"),
        )
        self.assertEqual([len(b) for b in batches], [1, 1, 1])

    def test_without_the_flag_nothing_is_grouped(self) -> None:
        batches = self.runner._partition_tool_batches(
            _spec(self.registry, concurrent=False),
            _calls("read_file", "grep"),
        )
        self.assertEqual([len(b) for b in batches], [1, 1])


if __name__ == "__main__":
    unittest.main()
