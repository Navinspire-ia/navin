# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A follow-up sent during a running turn must reach the model and the history.

Regression harness for the TUI "Send now" report: the queued prompt is
published while the first turn runs; the engine must inject it into the
running conversation (the next model call sees it) and persist it in the
session history so it stays visible in the transcript.
"""

from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import patch

import pytest

from navin.agent.loop import AgentLoop
from navin.agent.tools.filesystem import ReadFileTool
from navin.agent.tools.registry import ToolRegistry
from navin.bus.events import InboundMessage
from navin.bus.queue import MessageBus
from navin.config.loader import get_config_path, set_config_path
from navin.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class ScriptedProvider(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self.responses = deque(responses)
        self.calls: list[list[dict]] = []

    def get_default_model(self):
        return "test-send-now"

    async def chat(self, **kwargs):
        self.calls.append(list(kwargs.get("messages") or []))
        return self.responses.popleft()

    async def chat_with_retry(self, **kwargs):
        return await self.chat(**kwargs)


def tool_call(name, **arguments):
    return LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
        ToolCallRequest(id="call", name=name, arguments=arguments),
    ])


@pytest.fixture(autouse=True)
def isolated_config(tmp_path):
    old = get_config_path()
    set_config_path(tmp_path / "config.json")
    try:
        with (
            patch("navin.agent.skills._home_skill_dirs", return_value=[]),
            patch("navin.agent.subagent._outcomes_dir", return_value=tmp_path / "outcomes"),
        ):
            yield
    finally:
        set_config_path(old)


def test_followup_sent_mid_turn_reaches_model_and_history(tmp_path):
    async def run():
        (tmp_path / "a.json").write_text("{}\n")
        provider = ScriptedProvider([
            tool_call("read_file", path="a.json"),
            LLMResponse(content="first part done"),
        ])
        loop = AgentLoop(provider=provider, workspace=tmp_path, bus=MessageBus())
        loop._mcp_servers = {}
        tools = ToolRegistry()
        tools.register(ReadFileTool(workspace=tmp_path))
        loop.tools = tools
        task = asyncio.create_task(loop.run())
        try:
            await loop.bus.publish_inbound(InboundMessage(
                channel="cli", sender_id="user", chat_id="direct",
                content="first prompt", metadata={"_wants_stream": False},
            ))
            # Wait until the turn actually started (first model call in flight),
            # then send the follow-up exactly like the TUI "Send now" does.
            for _ in range(200):
                if provider.calls:
                    break
                await asyncio.sleep(0.01)
            assert provider.calls, "the first turn never reached the model"
            await loop.bus.publish_inbound(InboundMessage(
                channel="cli", sender_id="user", chat_id="direct",
                content="urgent follow-up", metadata={"_wants_stream": False},
            ))

            async with asyncio.timeout(30):
                while True:
                    await loop.bus.consume_outbound()
                    if len(provider.calls) >= 2:
                        break
            await asyncio.sleep(0.2)

            # The second model call must contain the follow-up as a user turn.
            second_call = provider.calls[1]
            user_texts = [
                str(m.get("content")) for m in second_call if m.get("role") == "user"
            ]
            assert any("urgent follow-up" in t for t in user_texts), user_texts

            # And it must be persisted in the session history (transcript).
            session = loop.sessions.get_or_create("cli:direct")
            history_user = [
                str(m.get("content")) for m in session.messages if m.get("role") == "user"
            ]
            assert any("urgent follow-up" in t for t in history_user), history_user
        finally:
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await loop.close_mcp()
    asyncio.run(run())
