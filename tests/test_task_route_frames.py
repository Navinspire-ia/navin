# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Thinking-strip identity must ride every live frame, not only the answer."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock

from navin.agent.loop import AgentLoop
from navin.bus.events import OutboundMessage
from navin.bus.outbound_events import ProgressEvent, TurnEndEvent


def _ws_channel():
    from navin.channels.websocket import WebSocketChannel

    channel = WebSocketChannel.__new__(WebSocketChannel)
    sent: list[str] = []

    async def _safe_send_to(_connection: Any, raw: str, label: str = "") -> None:
        sent.append(raw)

    channel._safe_send_to = _safe_send_to  # type: ignore[method-assign]
    channel._conn_chats = {"conn-a": set()}  # type: ignore[attr-defined]
    channel._subs = {"chat-1": ["conn-a"]}  # type: ignore[attr-defined]
    channel._transcripts = MagicMock()
    channel.logger = MagicMock()
    return channel, sent


class TurnModelMetaTests(unittest.TestCase):
    def test_turn_meta_includes_task_and_label(self) -> None:
        loop = MagicMock()
        loop.model = "qwen/qwen3.8-max"
        loop.model_preset = "complex"
        preset = MagicMock()
        preset.label = "Qwen 3.8 Max"
        loop.model_presets = {"complex": preset}
        loop._active_task_role = "deep"
        meta = AgentLoop._turn_model_meta(loop)
        self.assertEqual(meta["model_name"], "qwen/qwen3.8-max")
        self.assertEqual(meta["model_label"], "Qwen 3.8 Max")
        self.assertEqual(meta["task_role"], "deep")
        self.assertEqual(meta["model_route_role"], "deep")


class TaskRouteWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_delta_forwards_model_and_task(self) -> None:
        channel, sent = _ws_channel()
        await channel.send_reasoning_delta(
            "chat-1",
            "thinking",
            {
                "model_name": "qwen/qwen3.8-max",
                "model_label": "Qwen 3.8 Max",
                "task_role": "deep",
            },
        )
        body = json.loads(sent[0])
        self.assertEqual(body["event"], "reasoning_delta")
        self.assertEqual(body["model_name"], "qwen/qwen3.8-max")
        self.assertEqual(body["model_label"], "Qwen 3.8 Max")
        self.assertEqual(body["task_role"], "deep")

    async def test_turn_end_forwards_model_and_task(self) -> None:
        channel, sent = _ws_channel()
        await channel.send_turn_end(
            "chat-1",
            latency_ms=12,
            metadata={
                "model_name": "deepseek/deepseek-v4-flash",
                "model_label": "DeepSeek V4 Flash",
                "model_route_role": "fast",
            },
        )
        body = json.loads(sent[0])
        self.assertEqual(body["event"], "turn_end")
        self.assertEqual(body["model_name"], "deepseek/deepseek-v4-flash")
        self.assertEqual(body["model_label"], "DeepSeek V4 Flash")
        self.assertEqual(body["task_role"], "fast")

    async def test_progress_event_keeps_task_on_the_message_frame(self) -> None:
        channel, sent = _ws_channel()
        channel._media = MagicMock()
        channel._media.rewrite_local_markdown_images.side_effect = lambda text: text
        await channel.send(
            OutboundMessage(
                channel="websocket",
                chat_id="chat-1",
                content="Used tools",
                event=ProgressEvent(content="Used tools", tool_hint=True),
                metadata={
                    "model_name": "z-ai/glm-5.3-flash",
                    "model_label": "GLM 5.3 Flash",
                    "task_role": "dev",
                },
            )
        )
        body = json.loads(sent[0])
        self.assertEqual(body["event"], "message")
        self.assertEqual(body["task_role"], "dev")
        self.assertEqual(body["model_label"], "GLM 5.3 Flash")

    async def test_turn_end_event_reads_metadata_from_the_outbound(self) -> None:
        channel, sent = _ws_channel()
        await channel.send(
            OutboundMessage(
                channel="websocket",
                chat_id="chat-1",
                content="",
                event=TurnEndEvent(latency_ms=9),
                metadata={
                    "model": "moonshotai/kimi-k2.5",
                    "model_label": "Kimi K2.5",
                    "task_role": "review",
                },
            )
        )
        body = json.loads(sent[0])
        self.assertEqual(body["event"], "turn_end")
        self.assertEqual(body["model_name"], "moonshotai/kimi-k2.5")
        self.assertEqual(body["task_role"], "review")
