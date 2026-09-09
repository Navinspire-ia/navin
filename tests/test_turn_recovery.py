"""Interrupted work resumes once, preserves scope and honours an explicit stop."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from navin.agent.loop import AgentLoop
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.registry import ToolRegistry
from navin.bus.events import InboundMessage
from navin.bus.queue import MessageBus
from navin.config.loader import get_config_path, set_config_path
from navin.providers.base import LLMProvider, LLMResponse
from navin.session.manager import SessionManager
from navin.session.turn_recovery import RECOVERY_KEY, TurnRecovery
from navin.session.webui_turns import reconnect_turn_action
from navin.utils.llm_runtime import LLMRuntime


class LocalProvider(LLMProvider):
    def __init__(self, *responses):
        super().__init__()
        self.responses = list(responses)
        self.requests = []

    def get_default_model(self):
        return "test-recovery"

    async def chat(self, **kwargs):
        self.requests.append(kwargs["messages"])
        assert self.responses, "Recovery must not create extra model requests"
        return self.responses.pop(0)

    async def chat_with_retry(self, **kwargs):
        # Exercise the durable retry after the provider's short retry ladder.
        return await self.chat(**kwargs)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path):
    old = get_config_path()
    set_config_path(tmp_path / "config.json")
    with patch("navin.agent.skills._home_skill_dirs", return_value=[]):
        yield
    set_config_path(old)


def request(content="Finish the accepted work", *, channel="websocket", chat="recovery-test"):
    return InboundMessage(
        channel=channel, chat_id=chat, sender_id="user", content=content,
        metadata={"webui": True, "composer_mode": "ask", "_wants_stream": False},
    )


def setup_record(tmp_path, msg=None):
    msg = msg or request()
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create(msg.session_key)
    session.add_message("user", msg.content)
    recovery = TurnRecovery(sessions)
    recovery.begin(session, msg)
    return sessions, session, recovery


def test_restart_restores_one_request_with_the_same_scope(tmp_path):
    sessions, session, recovery = setup_record(tmp_path)
    reborn = TurnRecovery(SessionManager(tmp_path))
    assert reborn.discover(channel="websocket") == [session.key]
    assert reborn.take_due(active_keys={session.key}) == []
    resumed = reborn.take_due(active_keys=set())
    assert len(resumed) == 1
    assert resumed[0].session_key == session.key
    assert resumed[0].metadata["composer_mode"] == "ask"
    assert resumed[0].metadata["_skip_user_persist"] is True
    assert "inspect" in resumed[0].content.lower()
    assert reborn.take_due(active_keys=set()) == []
    action, started = reconnect_turn_action(sessions, session.key, "recovery-test")
    assert action == "running" and started


def test_stop_survives_restart_and_invalidates_already_queued_recovery(tmp_path):
    sessions, session, recovery = setup_record(tmp_path)
    recovery.discover(channel="websocket")
    queued = recovery.take_due(active_keys=set())[0]
    recovery.cancel(session.key)
    assert not recovery.matches(queued, session.key)
    assert TurnRecovery(SessionManager(tmp_path)).discover(channel="websocket") == []
    assert RECOVERY_KEY not in sessions.peek(session.key).metadata


def test_new_user_request_invalidates_old_recovery(tmp_path):
    sessions, session, recovery = setup_record(tmp_path)
    recovery.discover(channel="websocket")
    queued = recovery.take_due(active_keys=set())[0]
    recovery.begin(session, request("Use the corrected task instead"))
    assert not recovery.matches(queued, session.key)


def test_close_before_history_write_recovers_the_accepted_input_once(tmp_path):
    sessions = SessionManager(tmp_path)
    msg = request()
    session = sessions.get_or_create(msg.session_key)
    recovery = TurnRecovery(sessions)
    recovery.begin(session, msg, persisted=False)
    reborn_sessions = SessionManager(tmp_path)
    reborn = TurnRecovery(reborn_sessions)
    reborn.discover(channel="websocket")
    assert len(reborn.take_due(active_keys=set())) == 1
    assert reborn.take_due(active_keys=set()) == []
    history = reborn_sessions.get_or_create(session.key).messages
    assert len(history) == 1 and history[0]["content"] == msg.content


def test_backoff_survives_restart_and_does_not_hammer_the_provider(tmp_path):
    sessions, session, recovery = setup_record(tmp_path)
    delays = [recovery.schedule(session) for _ in range(9)]
    assert delays == [5, 10, 20, 40, 60, 60, 60, 60, 60]
    assert recovery.schedule(session, retry_after=150) == 150
    reborn = TurnRecovery(SessionManager(tmp_path))
    reborn.discover(channel="websocket")
    assert reborn.take_due(active_keys=set(), now=time.time() + 100) == []
    assert len(reborn.take_due(active_keys=set(), now=time.time() + 151)) == 1


def test_cli_and_desktop_only_restore_their_own_sessions(tmp_path):
    _, session, _ = setup_record(tmp_path)
    sessions, cli_session, _ = setup_record(tmp_path, request(channel="cli", chat="one"))
    recovery = TurnRecovery(sessions)
    assert recovery.discover(channel="cli", session_key=cli_session.key) == [cli_session.key]
    assert session.key not in recovery.due
    assert recovery.discover(channel="cli", session_key="cli:other") == []


@pytest.mark.parametrize("error, expected", [
    (LLMResponse(content="offline", finish_reason="error", error_kind="connection"), True),
    (LLMResponse(content="busy", finish_reason="error", error_status_code=503), True),
    (LLMResponse(content="invalid key", finish_reason="error", error_status_code=401), False),
    (LLMResponse(content="insufficient quota", finish_reason="error", error_status_code=429,
                 error_code="insufficient_quota"), False),
    (LLMResponse(content="connection error", finish_reason="error", error_should_retry=False), False),
])
def test_recovery_uses_structured_provider_errors(error, expected):
    async def check():
        provider = LocalProvider(error)
        result = await AgentRunner().run(AgentRunSpec(
            runtime=LLMRuntime.capture(provider, "test-recovery", context_window_tokens=128000),
            initial_messages=[{"role": "user", "content": "Continue"}],
            tools=ToolRegistry(), max_iterations=1, max_tool_result_chars=4000,
        ))
        assert result.retryable_error is expected
    asyncio.run(check())


def make_loop(tmp_path, provider, sessions=None):
    loop = AgentLoop(provider=provider, workspace=tmp_path, bus=MessageBus(), session_manager=sessions)
    loop._mcp_servers = {}
    return loop


def instant_retry(monkeypatch):
    real = TurnRecovery.schedule

    def schedule(self, session, **kwargs):
        delay = real(self, session, **kwargs)
        if delay is not None:
            self.due[session.key] = time.time() - 1
            session.metadata[RECOVERY_KEY]["next_attempt_at"] = time.time() - 1
            self.sessions.save(session)
        return delay

    monkeypatch.setattr(TurnRecovery, "schedule", schedule)


def test_one_shot_cli_recovers_network_without_duplicate_user_messages(tmp_path, monkeypatch):
    instant_retry(monkeypatch)

    async def check():
        provider = LocalProvider(
            LLMResponse(content="offline", finish_reason="error", error_kind="connection"),
            LLMResponse(content="The accepted work is complete."),
        )
        loop = make_loop(tmp_path, provider)
        try:
            response = await asyncio.wait_for(loop.process_direct("Finish the accepted work"), 15)
            assert response.content == "The accepted work is complete."
            session = loop.sessions.get_or_create("cli:direct")
            assert sum(m["role"] == "user" for m in session.messages) == 1
            assert RECOVERY_KEY not in session.metadata
            assert len(provider.requests) == 2
        finally:
            await loop.close_mcp()
    asyncio.run(check())


def test_gateway_restarts_unfinished_request_from_saved_checkpoint(tmp_path):
    sessions, session, recovery = setup_record(tmp_path)
    session.metadata["runtime_checkpoint"] = {
        "phase": "tool_execution", "pending_tool_calls": [
            {"id": "already-started", "function": {"name": "write_file", "arguments": "{}"}},
        ],
        "assistant_message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "already-started", "type": "function", "function": {"name": "write_file", "arguments": "{}"}},
        ]},
    }
    sessions.save(session)

    async def check():
        provider = LocalProvider(LLMResponse(content="Saved work checked; resumed successfully."))
        loop = make_loop(tmp_path, provider, SessionManager(tmp_path))
        task = asyncio.create_task(loop.run())
        try:
            async def done():
                while not provider.requests or RECOVERY_KEY in loop.sessions.get_or_create(session.key).metadata:
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(done(), 15)
            history = provider.requests[0]
            unknown = [m for m in history if m.get("tool_call_id") == "already-started"]
            assert unknown and "outcome is unknown" in unknown[0]["content"]
            assert len(provider.requests) == 1
            assert not any("Send your message again" in str(m.get("content")) for m in history)
        finally:
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())


def test_final_checkpoint_is_delivered_without_running_the_task_again(tmp_path):
    sessions, session, recovery = setup_record(tmp_path)
    session.metadata["runtime_checkpoint"] = {
        "phase": "final_response", "assistant_message": {"role": "assistant", "content": "Already done."},
    }
    sessions.save(session)

    async def check():
        provider = LocalProvider()
        loop = make_loop(tmp_path, provider, SessionManager(tmp_path))
        task = asyncio.create_task(loop.run())
        try:
            async def done():
                while RECOVERY_KEY in loop.sessions.get_or_create(session.key).metadata:
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(done(), 10)
            assert not provider.requests
            assert loop.sessions.get_or_create(session.key).messages[-1]["content"] == "Already done."
        finally:
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())
