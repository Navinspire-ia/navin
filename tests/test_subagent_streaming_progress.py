# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A subagent streams through the real runner without leaking its stream.

The subagent asks for streaming so a slow model is watched for stalls instead
of being killed by the non-streamed read timeout. Its text and reasoning must
still stay private: the parent bus gets progress cards ("Thinking...",
"Writing response...") and, at the end, the announced result, nothing else.
"""

from __future__ import annotations

import asyncio
import queue
import tempfile
import unittest
from pathlib import Path
from typing import Any

from navin.agent.hook import AgentHookContext
from navin.agent.subagent import SubagentManager, SubagentStatus, _SubagentHook
from navin.bus.outbound_events import SubagentProgressEvent
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime

_REASONING = "PRIVATE-REASONING-7f3a"
_ANSWER_PARTS = ("FINAL-", "ANSWER-", "c41e")
_ANSWER = "".join(_ANSWER_PARTS)


class _Bus:
    def __init__(self) -> None:
        self.outbound: queue.Queue = queue.Queue()
        self.inbound: list[Any] = []

    async def publish_inbound(self, msg: Any) -> None:
        self.inbound.append(msg)


class _StreamingProvider:
    """Streams reasoning then text, the way a thinking model does."""

    def __init__(self) -> None:
        self.generation = GenerationSettings()
        self.streamed = 0
        self.plain = 0

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        self.plain += 1
        return LLMResponse(content=_ANSWER, finish_reason="stop")

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.streamed += 1
        on_thinking = kwargs.get("on_thinking_delta")
        on_content = kwargs.get("on_content_delta")
        if on_thinking is not None:
            for chunk in (_REASONING[:8], _REASONING[8:]):
                await on_thinking(chunk)
        if on_content is not None:
            for chunk in _ANSWER_PARTS:
                await on_content(chunk)
        return LLMResponse(
            content=_ANSWER,
            reasoning_content=_REASONING,
            finish_reason="stop",
        )


def _drain(bus: _Bus) -> list[Any]:
    out: list[Any] = []
    while not bus.outbound.empty():
        out.append(bus.outbound.get_nowait())
    return out


class SubagentStreamingProgressTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = _Bus()
        self.provider = _StreamingProvider()
        self.manager = SubagentManager(
            workspace=Path(self._tmp.name),
            bus=self.bus,  # type: ignore[arg-type]
            max_tool_result_chars=4000,
        )

    async def test_stream_stays_private_and_the_result_survives(self) -> None:
        runtime = LLMRuntime(
            provider=self.provider,  # type: ignore[arg-type]
            model="stream-model",
            generation=GenerationSettings(),
            context_window_tokens=128_000,
        )
        await self.manager.spawn(
            task="translate the file",
            label="Translate",
            runtime=runtime,
            origin_channel="websocket",
            origin_chat_id="chat-s",
            session_key="websocket:chat-s",
        )
        tasks = list(self.manager._running_tasks.values())
        self.assertEqual(len(tasks), 1)
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)

        # The real streaming path ran, not the plain fallback.
        self.assertEqual(self.provider.streamed, 1)
        self.assertEqual(self.provider.plain, 0)

        outbound = _drain(self.bus)
        events = [m.event for m in outbound if isinstance(getattr(m, "event", None), SubagentProgressEvent)]
        # Only progress cards on the parent's outbound bus: no delta frames,
        # no reasoning frames, no partial assistant message.
        self.assertEqual(len(events), len(outbound), outbound)
        for msg in outbound:
            blob = repr(msg)
            self.assertNotIn(_REASONING[:8], blob)
            self.assertNotIn(_ANSWER_PARTS[0], blob)
            self.assertNotIn(_ANSWER_PARTS[2], blob)

        phases = [e.phase for e in events]
        self.assertIn("thinking", phases)
        self.assertIn("responding", phases)
        self.assertLess(phases.index("thinking"), phases.index("responding"))
        lines = {e.phase: e.status_line for e in events}
        self.assertEqual(lines["thinking"], "Thinking…")
        self.assertEqual(lines["responding"], "Writing response…")
        # One frame per phase change, not one per chunk.
        self.assertEqual(phases.count("thinking"), 1)
        self.assertEqual(phases.count("responding"), 1)
        self.assertTrue(events[-1].done)
        self.assertEqual(events[-1].phase, "done")

        # The final text reaches the parent intact, through the announcement.
        self.assertEqual(len(self.bus.inbound), 1)
        self.assertIn(_ANSWER, self.bus.inbound[0].content)
        self.assertNotIn(_REASONING, self.bus.inbound[0].content)


class SubagentHookPhaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_completed_action_is_visible_before_its_sibling_finishes(self) -> None:
        frames = []
        status = SubagentStatus(task_id="p", label="L", task_description="t", started_at=0.0)
        hook = _SubagentHook("p", status, on_progress=lambda s: frames.append(
            SubagentManager._progress_steps(s)
        ))
        calls = [ToolCallRequest(id=f"c{i}", name="read_file", arguments={"path": f"file{i}"})
                 for i in range(2)]
        context = AgentHookContext(iteration=1, messages=[], tool_calls=calls)
        await hook.before_execute_tools(context)
        await hook.on_tool_done(context, calls[0], "PRIVATE TOOL OUTPUT", {"name": "read_file", "status": "ok"})
        self.assertEqual([step["status"] for step in frames[-1]], ["ok", "running"])
        self.assertEqual([step["target"] for step in frames[-1]], ["file0", "file1"])
        await hook.on_tool_done(context, calls[1], "error", {"name": "read_file", "status": "error"})
        context.tool_events = [{"name": "read_file", "status": state} for state in ("ok", "error")]
        await hook.after_iteration(context)
        self.assertEqual(len(status.steps), 2, "Batch completion must not duplicate live actions")
        self.assertEqual([step["status"] for step in status.steps], ["ok", "error"])
        self.assertNotIn("PRIVATE TOOL OUTPUT", repr(frames))

    async def test_model_wait_replaces_starting_and_the_previous_tool(self) -> None:
        seen: list[str] = []
        status = SubagentStatus(task_id="p", label="L", task_description="t", started_at=0.0)
        hook = _SubagentHook("p", status, on_progress=lambda s: seen.append(s.phase))
        for iteration in (1, 2):
            await hook.before_iteration(AgentHookContext(iteration=iteration, messages=[]))
            self.assertEqual(status.phase, "awaiting_model")
            self.assertEqual(SubagentManager._status_line(status), "Waiting for model response...")
            await hook.emit_reasoning("private")
            self.assertEqual(status.phase, "thinking")
            await hook.on_stream(None, "text")
            status.phase = "tools_completed"
            status.tool_events = [{"name": "read_file", "status": "ok", "args": {"path": "x"}}]
        self.assertEqual(seen, ["awaiting_model", "thinking", "responding"] * 2)

    async def test_late_reasoning_does_not_flip_a_writing_card_back(self) -> None:
        seen: list[str] = []
        status = SubagentStatus(task_id="p", label="L", task_description="t", started_at=0.0)
        hook = _SubagentHook("p", status, on_progress=lambda s: seen.append(s.phase))
        await hook.emit_reasoning("hmm")
        await hook.emit_reasoning("more")
        await hook.on_stream(None, "text")  # type: ignore[arg-type]
        await hook.on_stream(None, "more text")  # type: ignore[arg-type]
        await hook.emit_reasoning("late")
        self.assertEqual(seen, ["thinking", "responding"])
        self.assertEqual(status.phase, "responding")


if __name__ == "__main__":
    unittest.main()
