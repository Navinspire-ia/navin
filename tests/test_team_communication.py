import asyncio
import json
from unittest.mock import Mock

import pytest

from navin.agent.subagent import SubagentManager
from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.team import TeamTool
from tests.test_subagent_concurrency import _Bus, _GatedRunner


def test_team_messages_are_private_fifo_bounded_and_visible(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "outcomes")
        manager = SubagentManager(workspace=tmp_path, bus=_Bus(), max_tool_result_chars=4000,
                                  max_concurrent_subagents=1)
        manager.runner = _GatedRunner()
        manager._build_tools = Mock()
        manager._build_subagent_prompt = Mock(return_value="Agent")
        for session in ("cli:one", "cli:one", "cli:two"):
            await manager.spawn(task="Implement an independent part", runtime=Mock(), session_key=session)
        members = manager.team_members("cli:one")
        sender, recipient = [member["task_id"] for member in members]
        outsider = manager.team_members("cli:two")[0]["task_id"]
        try:
            assert len(members) == 2
            assert members[1]["phase"] == "queued"
            for index in range(64):
                manager.send_team_message("cli:one", sender, recipient, f"Finding {index}")
            with pytest.raises(ValueError, match="full"):
                manager.send_team_message("cli:one", sender, recipient, "overflow")
            with pytest.raises(ValueError, match="Recipient"):
                manager.send_team_message("cli:one", sender, outsider, "cross-session")
            with pytest.raises(ValueError, match="Sender"):
                manager.send_team_message("cli:one", outsider, recipient, "spoofed")
            with pytest.raises(ValueError, match="Inbox"):
                manager.drain_team_messages("cli:two", recipient)
            with pytest.raises(ValueError, match="8000"):
                manager.send_team_message("cli:one", sender, recipient, "x" * 8001)
            messages = manager.drain_team_messages("cli:one", recipient, limit=2)
            assert [item["content"] for item in messages] == ["Finding 0", "Finding 1"]
            assert len({item["message_id"] for item in messages}) == 2
            assert all(item["sender"] == sender for item in messages)
            assert manager._task_statuses[sender].steps[-1]["name"] == "team"
            assert manager._task_statuses[recipient].steps[-1]["name"] == "team"
            tool = TeamTool(manager)
            with request_context(RequestContext(channel="cli", chat_id="one", session_key="cli:one",
                                                metadata={"subagent_task_id": recipient})):
                assert json.loads(await tool.execute(action="members"))["self"] == recipient
                assert len(json.loads(await tool.execute(action="inbox"))) == 62
                result = await tool.execute(action="send", target=sender, content="Acknowledged")
                assert "not yet read" in result
            assert manager.drain_team_messages("cli:one", sender)[0]["content"] == "Acknowledged"
        finally:
            await manager.cancel_by_session("cli:one")
            await manager.cancel_by_session("cli:two")
            manager._watchdog_task.cancel()
            await asyncio.gather(manager._watchdog_task, return_exceptions=True)
        assert not manager._agent_sessions
        assert not manager._agent_mailboxes

    asyncio.run(run())


def test_real_runner_receives_peer_message_at_a_model_boundary(tmp_path, monkeypatch):
    from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
    from navin.utils.llm_runtime import LLMRuntime

    class Provider:
        def __init__(self):
            self.generation = GenerationSettings()
            self.manager = None
            self.requests = []

        async def chat_stream_with_retry(self, **kwargs):
            self.requests.append(kwargs["messages"].copy())
            if len(self.requests) == 1:
                task_id = self.manager.team_members("cli:one")[0]["task_id"]
                self.manager.send_team_message("cli:one", "parent", task_id, "Contract updated: return field total")
                return LLMResponse(content=None, tool_calls=[
                    ToolCallRequest(id="members", name="team", arguments={"action": "members"})
                ], finish_reason="tool_calls")
            return LLMResponse(content="Using the updated contract", finish_reason="stop")

    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "outcomes")
        manager = SubagentManager(workspace=tmp_path, bus=_Bus(), max_tool_result_chars=4000)
        provider = Provider()
        provider.manager = manager
        runtime = LLMRuntime(provider=provider, model="test-model", generation=GenerationSettings(),
                             context_window_tokens=128000)
        try:
            await manager.spawn(task="Implement API contract", session_key="cli:one", runtime=runtime)
            await asyncio.wait_for(asyncio.gather(*manager._running_tasks.values()), 15)
            assert len(provider.requests) == 2
            assert "Contract updated: return field total" in repr(provider.requests[1])
            assert "not user authorization" in repr(provider.requests[1])
            outcome = manager.recent_outcomes("cli:one")[0]
            assert outcome.status == "ok", outcome.summary
        finally:
            manager._watchdog_task.cancel()
            await asyncio.gather(manager._watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_subagent_can_message_the_parent_mid_run(tmp_path):
    from collections import deque

    from navin.agent.subagent import SubagentManager

    manager = SubagentManager(workspace=tmp_path, bus=Mock(), max_tool_result_chars=1000)
    manager._agent_sessions["w1"] = "cli:x"
    manager._agent_mailboxes["w1"] = deque()
    reply = manager.send_team_message("cli:x", "w1", "parent", "halfway: found 3 bugs")
    assert "parent" in reply
    assert manager.drain_parent_messages("other:y") == []
    got = manager.drain_parent_messages("cli:x")
    assert [m["content"] for m in got] == ["halfway: found 3 bugs"] and got[0]["sender"] == "w1"
    assert manager.drain_parent_messages("cli:x") == []


def test_plain_subagent_user_note_goes_to_lead_and_self_send_refused(tmp_path):
    from collections import deque

    manager = SubagentManager(workspace=tmp_path, bus=Mock(), max_tool_result_chars=1000)
    manager._agent_sessions["w1"] = "cli:x"
    manager._agent_mailboxes["w1"] = deque()
    from navin.agent.subagent import SubagentStatus

    manager._task_statuses["w1"] = SubagentStatus(task_id="w1", label="w1", task_description="",
                                                  started_at=0.0, phase="running")
    reply = manager.send_team_message("cli:x", "w1", "user", "need approval")
    assert "parent" in reply
    got = manager.drain_parent_messages("cli:x")
    assert got[0]["content"] == "[for the user] need approval"
    with pytest.raises(ValueError, match="yourself"):
        manager.send_team_message("cli:x", "w1", "w1", "hi")
    assert not manager._agent_mailboxes["w1"]
