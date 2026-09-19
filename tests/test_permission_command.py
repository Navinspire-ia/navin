# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Permission switches persist, take effect live, and release the current prompt."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from navin.agent.approval import ApprovalBroker, bind_approval_gate, reset_approval_gate
from navin.agent.loop import AgentLoop, should_inject_into_active_turn
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.agent.tools.exec_session import WriteStdinTool
from navin.agent.tools.shell import ExecTool, StdinCommandGuard
from navin.bus.events import InboundMessage
from navin.bus.outbound_events import ApprovalClosedEvent, ApprovalRequestedEvent
from navin.bus.queue import MessageBus
from navin.command.builtin import builtin_command_palette, register_builtin_commands
from navin.command.router import CommandContext, CommandRouter
from navin.config.loader import get_config_path, load_config, save_config
from navin.config.schema import Config
from navin.providers.base import LLMResponse
from navin.webui.exec_policy_api import apply_approval_mode, read_approval_mode
from tests.test_long_task_continuation import ScriptedProvider, registry, tool_call


def make_state(tmp_path, mode="always"):
    config = Config()
    apply_approval_mode(config, mode)
    config.tools.exec.allow_patterns = [r"^echo trusted$"]
    config.tools.exec.deny_patterns = [r"\boperator-denied-command\b"]
    config.tools.restrict_to_workspace = True
    save_config(config)
    tools = registry(tmp_path)
    tools.register(ExecTool(working_dir=str(tmp_path), ask_every_command=mode == "always"))
    tools.register(WriteStdinTool(stdin_guard=StdinCommandGuard.from_config(
        config.tools.exec, config.tools.approvals,
    )))
    requests = asyncio.Queue()

    async def publish(request_id, request, route):
        requests.put_nowait((request_id, request))

    state = SimpleNamespace(
        tools=tools, tools_config=config.tools, exec_config=config.tools.exec,
        approvals=ApprovalBroker(
            config=config.tools.approvals, publish=publish,
            answering_channels=frozenset({"cli", "websocket"}),
        ),
    )
    router = CommandRouter()
    register_builtin_commands(router)
    return state, router, requests


async def command(state, router, text, *, channel="cli", sender_id="user"):
    msg = InboundMessage(channel=channel, sender_id=sender_id, chat_id="permissions", content=text)
    return await router.dispatch(CommandContext(msg=msg, session=None, key=msg.session_key, raw=text, loop=state))


@pytest.mark.parametrize("argument,expected", [
    ("auto", "autonomous"), ("full", "autonomous"), ("autonomous", "autonomous"),
    ("ask", "risky"), ("risky", "risky"), ("always", "always"),
])
def test_modes_persist_and_hot_apply_without_resetting_other_settings(tmp_path, argument, expected):
    async def run():
        state, router, _ = make_state(tmp_path)
        response = await command(state, router, f"/permission {argument}")
        assert f"**{expected}**" in response.content
        assert "applied immediately" in response.content
        saved = load_config()
        assert read_approval_mode(saved) == expected
        assert read_approval_mode(SimpleNamespace(tools=state.tools_config)) == expected
        assert state.tools.get("exec").ask_every_command is (expected == "always")
        assert state.tools.get("write_stdin")._stdin_guard.ask_every_command is (expected == "always")
        assert state.tools_config.security_profile == saved.tools.security_profile
        assert saved.tools.exec.allow_patterns == [r"^echo trusted$"]
        assert saved.tools.exec.deny_patterns == [r"\boperator-denied-command\b"]
        assert saved.tools.restrict_to_workspace
    asyncio.run(run())


@pytest.mark.parametrize("text,expected", [
    ("/permission", "**always**"),
    ("/permission status", "**always**"),
    ("/permission maybe", "Unknown permission mode"),
    ("/permission auto extra", "Unknown permission mode"),
])
def test_status_and_invalid_arguments_do_not_change_permissions(tmp_path, text, expected):
    async def run():
        state, router, _ = make_state(tmp_path)
        before = get_config_path().read_bytes()
        response = await command(state, router, text)
        assert expected in response.content
        assert "/permission auto" in response.content
        assert get_config_path().read_bytes() == before
        assert state.tools.get("exec").ask_every_command
    asyncio.run(run())


def test_save_failure_does_not_claim_success_or_apply_permissions(tmp_path):
    async def run():
        state, router, _ = make_state(tmp_path)
        with patch("navin.webui.exec_policy_api.save_config", side_effect=OSError("read-only config")):
            response = await command(state, router, "/permission auto")
        assert "Could not save" in response.content
        assert state.tools.get("exec").ask_every_command
        assert state.approvals._config.enabled
        assert read_approval_mode(load_config()) == "always"
    asyncio.run(run())


def test_live_apply_failure_reports_the_saved_setting_and_required_restart(tmp_path):
    async def run():
        state, router, _ = make_state(tmp_path)
        with patch.object(state.tools.get("exec"), "apply_policy", side_effect=RuntimeError("reload failed")):
            response = await command(state, router, "/permission auto")
        assert "Restart Navin" in response.content
        assert "applied immediately" not in response.content
        assert read_approval_mode(load_config()) == "autonomous"
    asyncio.run(run())


@pytest.mark.parametrize("channel,sender", [("telegram", "user"), ("cli", "subagent"), ("system", "system")])
def test_remote_or_internal_messages_cannot_change_global_permissions(tmp_path, channel, sender):
    async def run():
        state, router, _ = make_state(tmp_path)
        response = await command(state, router, "/permission auto", channel=channel, sender_id=sender)
        assert "from the Navin CLI or desktop" in response.content
        assert read_approval_mode(load_config()) == "always"
    asyncio.run(run())


def test_reenabling_confirmation_applies_to_existing_terminal_input(tmp_path):
    async def run():
        state, router, requests = make_state(tmp_path, mode="autonomous")
        request_token = bind_request_context(RequestContext(channel="cli", chat_id="permissions", session_key="cli:permissions"))
        gate_token = bind_approval_gate(state.approvals)
        try:
            stdin = state.tools.get("write_stdin")
            assert await stdin._stdin_guard.check("echo hello\n", session_command="sh") is None
            await command(state, router, "/permission always")
            pending = asyncio.create_task(stdin._stdin_guard.check("echo hello\n", session_command="sh"))
            request_id, request = await asyncio.wait_for(requests.get(), 2)
            assert request.tool == "write_stdin"
            state.approvals.resolve(request_id, allowed=False)
            assert "refused" in str(await asyncio.wait_for(pending, 2)).lower()
        finally:
            reset_approval_gate(gate_token)
            reset_request_context(request_token)
    asyncio.run(run())


@pytest.mark.parametrize("channel", ["cli", "websocket"])
def test_live_cli_and_desktop_switch_resumes_a_command_without_an_extra_model_call(tmp_path, channel):
    async def run():
        workspace = tmp_path / "project"
        workspace.mkdir()
        config = Config()
        apply_approval_mode(config, "always")
        save_config(config)
        provider = ScriptedProvider([
            tool_call("exec", command="echo permission-check"),
            LLMResponse(content="Shell check complete."),
        ])
        loop = AgentLoop(provider=provider, workspace=workspace, bus=MessageBus(), tools_config=config.tools)
        loop._mcp_servers = {}
        loop.tools = registry(workspace)
        loop.tools.register(ExecTool(working_dir=str(workspace), ask_every_command=True))
        loop.approvals.enable_channel(channel)
        task = asyncio.create_task(loop.run())
        try:
            await loop.bus.publish_inbound(InboundMessage(
                channel=channel, sender_id="user", chat_id="permissions", content="Run the shell check.",
            ))
            async with asyncio.timeout(15):
                while True:
                    message = await loop.bus.consume_outbound()
                    if isinstance(message.event, ApprovalRequestedEvent):
                        break
            assert provider.calls == 1
            await loop.bus.publish_inbound(InboundMessage(
                channel=channel, sender_id="user", chat_id="permissions", content="/permission auto",
            ))
            closed = saved = finished = False
            async with asyncio.timeout(15):
                while not (closed and saved and finished):
                    message = await loop.bus.consume_outbound()
                    if isinstance(message.event, ApprovalClosedEvent):
                        assert message.event.allowed
                        closed = True
                    saved |= "Saved and applied immediately" in message.content
                    finished |= message.content == "Shell check complete."
            assert provider.calls == 2
            assert read_approval_mode(load_config()) == "autonomous"
            assert not loop.approvals.open_requests(f"{channel}:permissions")
            assert not any("/permission auto" in str(m.get("content"))
                           for m in loop.sessions.get_or_create(f"{channel}:permissions").get_history())
        finally:
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await loop.close_mcp()
    asyncio.run(run())


def test_permission_is_discoverable_and_bypasses_the_active_turn_queue():
    router = CommandRouter()
    register_builtin_commands(router)
    for module in (None, "code", "career"):
        entry = next(row for row in builtin_command_palette(module) if row["command"] == "/permission")
        assert entry["accepts_args"]
        assert entry["lifecycle"] == "side_channel"
    assert not should_inject_into_active_turn(router, "/permission auto")
