# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Long accepted tasks finish across slices without a synthetic user reply."""

from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from navin.agent.loop import AgentLoop
from navin.agent.runner import AgentLoopGuard, AgentRunner, AgentRunSpec
from navin.agent.subagent import SubagentManager
from navin.agent.tools.filesystem import ReadFileTool, WriteFileTool
from navin.agent.tools.registry import ToolRegistry
from navin.bus.events import InboundMessage
from navin.bus.queue import MessageBus
from navin.config.loader import get_config_path, set_config_path
from navin.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime


class ScriptedProvider(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self.responses = deque(responses)
        self.calls = 0

    def get_default_model(self):
        return "test-long-task"

    async def chat(self, **kwargs):
        self.calls += 1
        assert self.responses, "The runner must stop when the task is complete"
        response = self.responses.popleft()
        for index, call in enumerate(response.tool_calls):
            call.id = f"call-{self.calls}-{index}"
        return response

    async def chat_with_retry(self, **kwargs):
        return await self.chat(**kwargs)


def tool_call(name, **arguments):
    return LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
        ToolCallRequest(id="call", name=name, arguments=arguments),
    ])


def registry(root):
    tools = ToolRegistry()
    tools.register(ReadFileTool(workspace=root))
    tools.register(WriteFileTool(workspace=root))
    return tools


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


@pytest.mark.parametrize("surface", ["cli", "gateway"])
def test_audit_of_169_files_finishes_without_continue(tmp_path, surface):
    async def run():
        for index in range(169):
            (tmp_path / f"locale-{index}.json").write_text('{"company": "Entreprise"}\n')
        provider = ScriptedProvider([
            *(tool_call("read_file", path=f"locale-{index}.json") for index in range(169)),
            tool_call("write_file", path="audit-result.txt", content="169 files checked\n"),
            LLMResponse(content="Audit complete: 169 files checked."),
        ])
        loop = AgentLoop(provider=provider, workspace=tmp_path, bus=MessageBus(), max_iterations=2)
        loop._mcp_servers = {}
        loop.tools = registry(tmp_path)
        task = None
        key = "cli:direct" if surface == "cli" else "websocket:long-audit"
        try:
            if surface == "cli":
                response = await asyncio.wait_for(loop.process_direct("Audit all 169 locale files."), 45)
            else:
                task = asyncio.create_task(loop.run())
                await loop.bus.publish_inbound(InboundMessage(
                    channel="websocket", sender_id="user", chat_id="long-audit",
                    content="Audit all 169 locale files.", metadata={"_wants_stream": False},
                ))
                async with asyncio.timeout(45):
                    while True:
                        response = await loop.bus.consume_outbound()
                        assert "safety cap" not in response.content
                        assert "Reply **continue**" not in response.content
                        if response.content == "Audit complete: 169 files checked.":
                            break
            assert response.content == "Audit complete: 169 files checked."
            assert (tmp_path / "audit-result.txt").read_text() == "169 files checked\n"
            assert provider.calls == 171
            session = loop.sessions.get_or_create(key)
            assert sum(m["role"] == "user" for m in session.messages) == 1
            assert not any("safety cap" in str(m.get("content")) for m in session.messages)
            assert key not in loop._loop_guards
            if surface == "gateway":
                assert session.metadata["_turn_budget_continuation_rounds"] == 85
        finally:
            loop.stop()
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await loop.close_mcp()
    asyncio.run(run())


@pytest.mark.parametrize("name, arguments, stop", [
    ("write_file", {}, "tool_error"),
    ("read_file", {"path": "locale.json"}, "no_progress"),
])
def test_real_loop_guard_survives_single_call_slices(tmp_path, name, arguments, stop):
    async def run():
        (tmp_path / "locale.json").write_text('{"company": "Entreprise"}\n')
        provider = ScriptedProvider([tool_call(name, **arguments) for _ in range(20)])
        guard = AgentLoopGuard()
        messages = [{"role": "user", "content": "Audit the locale file."}]
        for _ in range(15):
            result = await AgentRunner().run(AgentRunSpec(
                initial_messages=messages, tools=registry(tmp_path),
                runtime=LLMRuntime.capture(provider, "test-long-task", context_window_tokens=128_000),
                workspace=tmp_path, max_iterations=1, max_tool_result_chars=4_000,
                finalize_on_max_iterations=False, loop_guard=guard,
            ))
            messages = result.messages
            if result.stop_reason != "max_iterations":
                break
            assert result.final_content is None
        assert result.stop_reason == stop
        if stop == "tool_error":
            assert provider.calls == 3
        else:
            assert "blocked tool calls" in result.final_content
    asyncio.run(run())


def test_a_stalled_subagent_reports_failure_instead_of_completion(tmp_path):
    async def run():
        (tmp_path / "locale.json").write_text('{"company": "Entreprise"}\n')
        provider = ScriptedProvider([
            *(tool_call("read_file", path="locale.json") for _ in range(10)),
            LLMResponse(content="The same blocked read keeps repeating; the audit is unfinished."),
        ])
        announcements = []

        async def announce(message):
            announcements.append(message)

        manager = SubagentManager(
            workspace=tmp_path, bus=SimpleNamespace(publish_inbound=announce),
            max_iterations=2, max_tool_result_chars=4_000,
        )
        key = "cli:stalled-subagent"
        with patch.object(manager, "_build_tools", side_effect=lambda **_: registry(tmp_path)), patch.object(
            manager, "_build_subagent_prompt", return_value="Audit the locale file.",
        ):
            await manager.spawn(
                task="Audit the locale file.", label="locale-audit", session_key=key,
                runtime=LLMRuntime.capture(provider, "test-long-task", context_window_tokens=128_000),
                origin_channel="cli", origin_chat_id="stalled-subagent",
            )
            async with asyncio.timeout(10):
                while manager.get_running_count():
                    await asyncio.gather(*list(manager._running_tasks.values()))
                    await asyncio.sleep(0)
        assert len(announcements) == 1
        outcome = manager.recent_outcomes(session_key=key)[0]
        assert outcome.status == "error"
        assert "repeated identical read blocked" in outcome.summary
        assert "unfinished" in outcome.summary
    asyncio.run(run())
