# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Parallel subagent progress events for the WebUI card panel.

Covers publish, throttle, done/error, 3-way fan-out over websocket, and the
WebSocketChannel.send dispatch path end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from navin.agent.hook import AgentHookContext
from navin.agent.runner import AgentRunResult
from navin.agent.subagent import (
    SubagentManager,
    SubagentStatus,
    _SubagentHook,
)
from navin.bus.events import OutboundMessage
from navin.bus.outbound_events import SubagentProgressEvent, outbound_message_for_event
from navin.channels.websocket import WebSocketChannel
from navin.providers.base import ToolCallRequest


class _OutboundBus:
    def __init__(self) -> None:
        self.outbound: queue.Queue = queue.Queue()
        self.published: list = []

    async def publish_inbound(self, msg) -> None:
        self.published.append(msg)


class _GatedRunner:
    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.started = 0
        self.peak_concurrent = 0
        self._in_flight = 0
        self.fail = False

    async def run(self, spec) -> AgentRunResult:
        self.started += 1
        self._in_flight += 1
        self.peak_concurrent = max(self.peak_concurrent, self._in_flight)
        try:
            await self.gate.wait()
        finally:
            self._in_flight -= 1
        if self.fail:
            return AgentRunResult(
                final_content="",
                messages=[],
                stop_reason="error",
                error="boom",
            )
        return AgentRunResult(final_content="done", messages=[], stop_reason="end")


def _progress_events(bus: _OutboundBus) -> list[SubagentProgressEvent]:
    events: list[SubagentProgressEvent] = []
    while not bus.outbound.empty():
        msg = bus.outbound.get_nowait()
        if isinstance(getattr(msg, "event", None), SubagentProgressEvent):
            events.append(msg.event)
    return events


class SubagentProgressPublishTest(unittest.TestCase):
    def test_publish_progress_emits_websocket_event(self) -> None:
        bus = _OutboundBus()
        mgr = SubagentManager.__new__(SubagentManager)
        mgr.bus = bus
        status = SubagentStatus(
            task_id="abcd1234",
            label="Build artifacts",
            task_description="Implement P1-A",
            started_at=0.0,
            phase="awaiting_tools",
            model="test-model",
            origin_channel="websocket",
            origin_chat_id="chat-1",
            tool_events=[{"name": "write_file", "status": "running"}],
        )
        mgr._publish_progress(status, force=True)
        events = _progress_events(bus)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].task_id, "abcd1234")
        self.assertEqual(events[0].label, "Build artifacts")
        self.assertIn("write_file", events[0].status_line)
        self.assertEqual(events[0].model, "test-model")
        self.assertFalse(events[0].done)

    def test_cli_origin_is_silent(self) -> None:
        bus = _OutboundBus()
        mgr = SubagentManager.__new__(SubagentManager)
        mgr.bus = bus
        status = SubagentStatus(
            task_id="x",
            label="cli",
            task_description="t",
            started_at=0.0,
            origin_channel="cli",
            origin_chat_id="direct",
        )
        mgr._publish_progress(status, force=True)
        self.assertTrue(bus.outbound.empty())

    def test_throttle_skips_identical_status_line(self) -> None:
        bus = _OutboundBus()
        mgr = SubagentManager.__new__(SubagentManager)
        mgr.bus = bus
        status = SubagentStatus(
            task_id="t",
            label="L",
            task_description="d",
            started_at=0.0,
            phase="awaiting_tools",
            origin_channel="websocket",
            origin_chat_id="c",
            tool_events=[{"name": "read_file", "status": "running"}],
        )
        mgr._publish_progress(status, force=True)
        mgr._publish_progress(status, force=False)
        events = _progress_events(bus)
        self.assertEqual(len(events), 1)

    def test_force_and_done_bypass_throttle(self) -> None:
        bus = _OutboundBus()
        mgr = SubagentManager.__new__(SubagentManager)
        mgr.bus = bus
        status = SubagentStatus(
            task_id="t",
            label="L",
            task_description="d",
            started_at=0.0,
            phase="done",
            origin_channel="websocket",
            origin_chat_id="c",
        )
        mgr._publish_progress(status, force=True)
        mgr._publish_progress(status, force=True, done=True)
        events = _progress_events(bus)
        self.assertEqual(len(events), 2)
        self.assertTrue(events[-1].done)
        self.assertEqual(events[-1].status_line, "Completed")

    def test_status_line_for_error(self) -> None:
        status = SubagentStatus(
            task_id="t",
            label="L",
            task_description="d",
            started_at=0.0,
            phase="error",
            error="disk full",
        )
        self.assertEqual(SubagentManager._status_line(status), "disk full")


class SubagentHookProgressTest(unittest.IsolatedAsyncioTestCase):
    async def test_hook_calls_on_progress(self) -> None:
        seen: list[str] = []
        status = SubagentStatus(
            task_id="h1",
            label="Hooked",
            task_description="t",
            started_at=0.0,
            origin_channel="websocket",
            origin_chat_id="c",
        )
        hook = _SubagentHook(
            "h1",
            status,
            on_progress=lambda s: seen.append(s.phase),
        )
        ctx = AgentHookContext(
            iteration=0,
            messages=[],
            tool_calls=[
                ToolCallRequest(id="1", name="read_file", arguments={"path": "a.py"})
            ],
        )
        await hook.before_execute_tools(ctx)
        self.assertEqual(status.phase, "awaiting_tools")
        self.assertIn("awaiting_tools", seen)

        ctx.iteration = 1
        ctx.tool_events = [{"name": "read_file", "status": "ok"}]
        await hook.after_iteration(ctx)
        self.assertEqual(status.phase, "tools_completed")
        self.assertIn("tools_completed", seen)


class ThreeWebsocketSubagentsLiveTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self.bus = _OutboundBus()
        self.runner = _GatedRunner()
        self.manager = SubagentManager(
            workspace=self.workspace,
            bus=self.bus,  # type: ignore[arg-type]
            max_tool_result_chars=4000,
        )
        self.manager.runner = self.runner

    async def _spawn(self, label: str) -> str:
        runtime = mock.Mock()
        runtime.model = "cursor-grok-test"
        runtime.with_generation_overrides = lambda **_: runtime
        return await self.manager.spawn(
            task=f"work on {label}",
            label=label,
            runtime=runtime,
            origin_channel="websocket",
            origin_chat_id="chat-live",
            session_key="websocket:chat-live",
        )

    async def _settle(self, until=None, timeout: float = 10.0) -> None:
        # Real (tiny) sleeps, not bare yields: the spawn path builds the
        # subagent prompt in a worker thread, and a sleep(0) pass gives that
        # thread no time to finish before the assertions run. When a condition
        # is given, poll it instead of guessing a duration: prompt building
        # reads the filesystem, which can take far longer than a fixed wait.
        loop = asyncio.get_running_loop()
        if until is None:
            for _ in range(30):
                await asyncio.sleep(0.005)
            return
        deadline = loop.time() + timeout
        while not until() and loop.time() < deadline:
            await asyncio.sleep(0.005)

    async def test_three_parallel_subagents_emit_start_then_done(self) -> None:
        labels = ["P1-A artifacts", "P1-B voice", "P1-C collab"]
        for label in labels:
            reply = await self._spawn(label)
            self.assertIn("started", reply)
        await self._settle(lambda: self.runner.started >= 3)

        self.assertEqual(self.manager.get_running_count(), 3)
        self.assertEqual(self.runner.peak_concurrent, 3)

        starts = _progress_events(self.bus)
        start_labels = {e.label for e in starts}
        self.assertTrue(set(labels).issubset(start_labels), start_labels)
        for event in starts:
            self.assertEqual(event.model, "cursor-grok-test")
            self.assertFalse(event.done)
            self.assertEqual(event.phase, "initializing")

        tasks = list(self.manager._running_tasks.values())
        self.runner.gate.set()
        await asyncio.gather(*tasks)
        # Allow done callbacks to publish.
        await self._settle()

        dones = [e for e in _progress_events(self.bus) if e.done]
        self.assertGreaterEqual(len(dones), 3)
        done_labels = {e.label for e in dones}
        self.assertTrue(set(labels).issubset(done_labels), done_labels)
        for event in dones:
            self.assertEqual(event.phase, "done")
            self.assertEqual(event.status_line, "Completed")

    async def test_failed_subagent_emits_done_error_card(self) -> None:
        self.runner.fail = True
        await self._spawn("broken task")
        await self._settle(lambda: self.runner.started >= 1)
        _progress_events(self.bus)  # drain starts
        tasks = list(self.manager._running_tasks.values())
        self.runner.gate.set()
        await asyncio.gather(*tasks)
        await self._settle()
        dones = [e for e in _progress_events(self.bus) if e.done]
        self.assertTrue(dones)
        self.assertEqual(dones[-1].phase, "error")
        self.assertTrue(dones[-1].error)


class WebsocketDispatchPathTest(unittest.IsolatedAsyncioTestCase):
    async def test_channel_send_routes_subagent_progress(self) -> None:
        sent: list[str] = []

        class _Conn:
            async def send(self, raw: str) -> None:
                sent.append(raw)

        channel = WebSocketChannel.__new__(WebSocketChannel)
        channel._subs = {"chat-1": (_Conn(),)}  # type: ignore[attr-defined]

        async def _safe(connection, raw, label=""):
            await connection.send(raw)

        channel._safe_send_to = _safe  # type: ignore[method-assign]
        msg = outbound_message_for_event(
            channel="websocket",
            chat_id="chat-1",
            event=SubagentProgressEvent(
                task_id="t1",
                label="Voice realtime",
                phase="tools_completed",
                status_line="Running read_file",
                model="gpt-test",
                iteration=2,
                done=False,
            ),
        )
        await channel.send(msg)
        self.assertEqual(len(sent), 1)
        body = json.loads(sent[0])
        self.assertEqual(
            body,
            {
                "event": "subagent_progress",
                "chat_id": "chat-1",
                "task_id": "t1",
                "label": "Voice realtime",
                "phase": "tools_completed",
                "status_line": "Running read_file",
                "iteration": 2,
                "done": False,
                "model": "gpt-test",
            },
        )

    async def test_send_subagent_progress_shape(self) -> None:
        sent: list[str] = []

        class _Conn:
            async def send(self, raw: str) -> None:
                sent.append(raw)

        channel = WebSocketChannel.__new__(WebSocketChannel)
        channel._subs = {"chat-1": (_Conn(),)}  # type: ignore[attr-defined]

        async def _safe(connection, raw, label=""):
            await connection.send(raw)

        channel._safe_send_to = _safe  # type: ignore[method-assign]
        await channel.send_subagent_progress(
            "chat-1",
            SubagentProgressEvent(
                task_id="t1",
                label="Voice realtime",
                phase="tools_completed",
                status_line="Running read_file",
                model="gpt-test",
                iteration=2,
                done=False,
            ),
        )
        self.assertEqual(len(sent), 1)
        body = json.loads(sent[0])
        self.assertEqual(body["event"], "subagent_progress")
        self.assertEqual(body["task_id"], "t1")
        self.assertFalse(body["done"])

    async def test_no_subscribers_is_silent(self) -> None:
        channel = WebSocketChannel.__new__(WebSocketChannel)
        channel._subs = {}  # type: ignore[attr-defined]
        # Must not raise when nobody is listening.
        await channel.send_subagent_progress(
            "missing",
            SubagentProgressEvent(
                task_id="t",
                label="L",
                phase="initializing",
                status_line="Starting…",
            ),
        )


class RunningSubagentReplayTest(unittest.IsolatedAsyncioTestCase):
    """A refreshed client must get its running subagents back.

    The cards live in browser memory, so without this replay a background task
    keeps working with nothing on screen saying so, and the request reads as
    ignored.
    """

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = _OutboundBus()
        self.runner = _GatedRunner()
        self.manager = SubagentManager(
            workspace=Path(self._tmp.name),
            bus=self.bus,  # type: ignore[arg-type]
            max_tool_result_chars=4000,
        )
        self.manager.runner = self.runner

    async def _spawn(self, label: str) -> None:
        runtime = mock.Mock()
        runtime.model = "cursor-grok-test"
        runtime.with_generation_overrides = lambda **_: runtime
        await self.manager.spawn(
            task=f"build a pitch deck for {label}",
            label=label,
            runtime=runtime,
            origin_channel="websocket",
            origin_chat_id="chat-live",
            session_key="websocket:chat-live",
        )
        for _ in range(30):
            await asyncio.sleep(0)

    async def _drain(self) -> None:
        tasks = list(self.manager._running_tasks.values())
        self.runner.gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for _ in range(30):
            await asyncio.sleep(0)

    async def test_snapshot_lists_what_is_still_running(self) -> None:
        await self._spawn("Deck")
        snapshot = self.manager.running_snapshot("websocket:chat-live")
        self.assertEqual(len(snapshot), 1)
        card = snapshot[0]
        self.assertEqual(card["label"], "Deck")
        self.assertFalse(card["done"])
        self.assertEqual(card["model"], "cursor-grok-test")
        self.assertIn("pitch deck", card["task_description"])
        self.assertGreaterEqual(card["started_ms_ago"], 0)
        await self._drain()

    async def test_snapshot_is_empty_for_another_session(self) -> None:
        await self._spawn("Deck")
        self.assertEqual(self.manager.running_snapshot("websocket:other"), [])
        await self._drain()

    async def test_finished_subagents_leave_the_snapshot(self) -> None:
        await self._spawn("Deck")
        await self._drain()
        self.assertEqual(self.manager.running_snapshot("websocket:chat-live"), [])

    async def test_runtime_control_query_answers_with_the_snapshot(self) -> None:
        from navin.agent.subagent import handle_subagents_query, request_running_subagents

        await self._spawn("Deck")

        async def _publish(msg) -> None:
            await handle_subagents_query(
                SimpleNamespace(subagents=self.manager), msg, None
            )

        bus = SimpleNamespace(publish_inbound=_publish)
        running = await request_running_subagents(bus, "websocket:chat-live")
        self.assertEqual([card["label"] for card in running], ["Deck"])
        await self._drain()

    async def test_query_for_another_control_message_is_not_claimed(self) -> None:
        from navin.agent.subagent import handle_subagents_query

        handled = await handle_subagents_query(
            SimpleNamespace(subagents=self.manager),
            SimpleNamespace(metadata={"_runtime_control": "mcp_reload"}),
            None,
        )
        self.assertFalse(handled)

    async def test_query_without_a_manager_answers_empty(self) -> None:
        from navin.agent.subagent import handle_subagents_query, request_running_subagents

        async def _publish(msg) -> None:
            await handle_subagents_query(object(), msg, None)

        bus = SimpleNamespace(publish_inbound=_publish)
        self.assertEqual(await request_running_subagents(bus, "websocket:chat-live"), [])

    async def test_query_without_an_answer_times_out_quietly(self) -> None:
        from navin.agent.subagent import request_running_subagents

        async def _publish(msg) -> None:
            return None

        bus = SimpleNamespace(publish_inbound=_publish)
        running = await request_running_subagents(
            bus, "websocket:chat-live", timeout=0.01
        )
        self.assertEqual(running, [])

    async def test_hydrate_pushes_a_card_with_its_real_age(self) -> None:
        sent: list[str] = []

        class _Conn:
            async def send(self, raw: str) -> None:
                sent.append(raw)

        channel = WebSocketChannel.__new__(WebSocketChannel)
        channel._subs = {"chat-live": (_Conn(),)}  # type: ignore[attr-defined]
        channel.bus = SimpleNamespace()  # type: ignore[attr-defined]
        channel.logger = mock.Mock()  # type: ignore[attr-defined]

        async def _safe(connection, raw, label=""):
            await connection.send(raw)

        channel._safe_send_to = _safe  # type: ignore[method-assign]

        with mock.patch(
            "navin.agent.subagent.request_running_subagents",
            new=mock.AsyncMock(return_value=[{
                "task_id": "t1",
                "label": "Deck",
                "phase": "awaiting_tools",
                "status_line": "Running write_file",
                "model": "gpt-test",
                "iteration": 3,
                "done": False,
                "task_description": "build a pitch deck",
                "started_ms_ago": 660_000,
            }]),
        ):
            await channel._maybe_push_running_subagents("chat-live")

        self.assertEqual(len(sent), 1)
        body = json.loads(sent[0])
        self.assertEqual(body["task_id"], "t1")
        self.assertEqual(body["started_ms_ago"], 660_000)
        self.assertFalse(body["done"])

    async def test_hydrate_survives_a_failing_query(self) -> None:
        channel = WebSocketChannel.__new__(WebSocketChannel)
        channel._subs = {}  # type: ignore[attr-defined]
        channel.bus = SimpleNamespace()  # type: ignore[attr-defined]
        channel.logger = mock.Mock()  # type: ignore[attr-defined]
        with mock.patch(
            "navin.agent.subagent.request_running_subagents",
            new=mock.AsyncMock(side_effect=RuntimeError("bus down")),
        ):
            # Attaching a client must not fail because a replay could not run.
            await channel._maybe_push_running_subagents("chat-live")


class OutboundMessageShapeTest(unittest.TestCase):
    def test_outbound_message_carries_typed_event(self) -> None:
        msg = outbound_message_for_event(
            channel="websocket",
            chat_id="c",
            event=SubagentProgressEvent(
                task_id="id",
                label="L",
                phase="initializing",
                status_line="Starting…",
                done=False,
            ),
        )
        self.assertIsInstance(msg, OutboundMessage)
        self.assertIsInstance(msg.event, SubagentProgressEvent)


if __name__ == "__main__":
    unittest.main()
