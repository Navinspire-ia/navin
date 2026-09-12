# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Invalid tool arguments must recover or release a subagent's queue slot."""

from __future__ import annotations

import asyncio
import queue
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.subagent import SubagentManager
from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.filesystem import WriteFileTool
from navin.agent.tools.quality import VerifyTool
from navin.agent.tools.registry import ToolRegistry
from navin.agent.tools.shell import ExecTool
from navin.bus.outbound_events import SubagentProgressEvent
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime


class _Provider:
    def __init__(self, *responses: LLMResponse):
        self.responses = responses
        self.requests: list[dict] = []

    async def chat_with_retry(self, **kwargs):
        response = deepcopy(self.responses[min(len(self.requests), len(self.responses) - 1)])
        self.requests.append(deepcopy(kwargs))
        for index, call in enumerate(response.tool_calls):
            call.id = f"call-{len(self.requests)}-{index}"
        return response

    async def chat_stream_with_retry(self, **kwargs):
        return await self.chat_with_retry(**kwargs)


def _call(name: str = "write_file", **arguments) -> ToolCallRequest:
    return ToolCallRequest(id="call", name=name, arguments=arguments)


def _tools(*calls: ToolCallRequest) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=list(calls), finish_reason="tool_calls")


def _runtime(provider: _Provider) -> LLMRuntime:
    return LLMRuntime(
        provider=provider,
        model="test-model",
        generation=GenerationSettings(),
        context_window_tokens=128_000,
    )


def _registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(WriteFileTool(workspace=root))
    registry.register(ExecTool(working_dir=str(root)))
    registry.register(VerifyTool(workspace=root))
    return registry


def _spec(root: Path, provider: _Provider, **overrides) -> AgentRunSpec:
    return AgentRunSpec(
        initial_messages=[{"role": "user", "content": "Update the translations."}],
        tools=_registry(root),
        runtime=_runtime(provider),
        max_iterations=12,
        max_tool_result_chars=4000,
        workspace=root,
        finalize_on_max_iterations=False,
        **overrides,
    )


def test_empty_write_arguments_get_schema_guidance_and_can_recover(tmp_path):
    provider = _Provider(
        _tools(_call()),
        _tools(_call(path="fr.json", content='{"company": "Entreprise"}\n')),
        LLMResponse(content="Translations updated."),
    )
    result = asyncio.run(AgentRunner().run(_spec(tmp_path, provider)))

    feedback = next(m["content"] for m in provider.requests[1]["messages"] if m["role"] == "tool")
    assert "No tool was executed" in feedback
    assert "path (string)" in feedback and "content (string)" in feedback
    assert "Reissue" in feedback
    assert result.stop_reason == "completed"
    assert result.tools_used == ["write_file"]
    assert (tmp_path / "fr.json").read_text() == '{"company": "Entreprise"}\n'


@pytest.mark.parametrize("batch_size", [1, 8])
def test_repeated_invalid_batches_stop_before_exhausting_the_turn(tmp_path, batch_size):
    target = tmp_path / "fr.json"
    target.write_text("original\n")
    provider = _Provider(_tools(*(_call() for _ in range(batch_size))))
    result = asyncio.run(AgentRunner().run(_spec(tmp_path, provider)))

    assert len(provider.requests) == 3
    assert result.stop_reason == "tool_error"
    assert "invalid tool arguments" in result.error
    assert "write_file" in result.error
    assert not result.tools_used
    assert target.read_text() == "original\n"


def test_changing_incomplete_arguments_does_not_evade_the_limit(tmp_path):
    provider = _Provider(
        _tools(_call()),
        _tools(_call(path="fr.json")),
        _tools(_call(content="missing path")),
    )
    result = asyncio.run(AgentRunner().run(_spec(tmp_path, provider)))
    assert len(provider.requests) == 3
    assert result.stop_reason == "tool_error"
    assert not (tmp_path / "fr.json").exists()


@pytest.mark.parametrize("arguments", [{}, {"command": ""}, {"command": " \n\t"}, {"cmd": ""}])
def test_exec_without_a_command_is_rejected_before_execution_and_stops(tmp_path, arguments):
    provider = _Provider(_tools(_call("exec", **arguments)))
    spec = _spec(tmp_path, provider)
    executor = spec.tools.get("exec")
    with patch.object(executor, "execute", wraps=executor.execute) as execute:
        result = asyncio.run(AgentRunner().run(spec))
    execute.assert_not_called()
    assert len(provider.requests) == 3
    assert result.stop_reason == "tool_error"
    assert "exec" in result.error
    feedback = next(m["content"] for m in provider.requests[1]["messages"] if m["role"] == "tool")
    assert "command (string)" in feedback


@pytest.mark.parametrize(
    "arguments",
    [{"cmd": "echo navin-recovery-ok"}, {"command": "echo navin-recovery-ok"}, "echo navin-recovery-ok"],
)
def test_exec_command_alias_and_bare_command_still_work(tmp_path, arguments):
    result = asyncio.run(_registry(tmp_path).execute("exec", arguments))
    assert not getattr(result, "is_error", False), result
    assert "navin-recovery-ok" in result
    assert "Exit code: 0" in result


def test_successful_work_resets_the_invalid_arguments_streak(tmp_path):
    provider = _Provider(
        _tools(_call()),
        _tools(_call()),
        _tools(_call(), _call(path="fr.json", content="first\n")),
        _tools(_call()),
        _tools(_call()),
        _tools(_call(path="fr.json", content="recovered\n")),
        LLMResponse(content="Translations updated."),
    )
    result = asyncio.run(AgentRunner().run(_spec(tmp_path, provider)))
    assert result.stop_reason == "completed"
    assert (tmp_path / "fr.json").read_text() == "recovered\n"


def test_an_injected_correction_can_resume_after_the_failure_limit(tmp_path):
    provider = _Provider(
        *(_tools(_call()) for _ in range(3)),
        _tools(_call(path="fr.json", content="corrected\n")),
        LLMResponse(content="Translations updated."),
    )
    injected = False

    async def inject(**_):
        nonlocal injected
        if len(provider.requests) == 3 and not injected:
            injected = True
            return [{"role": "user", "content": "Write corrected followed by a newline to fr.json."}]
        return []

    result = asyncio.run(AgentRunner().run(_spec(tmp_path, provider, injection_callback=inject)))
    assert result.had_injections
    assert result.stop_reason == "completed"
    assert result.error is None
    assert len(provider.requests) == 5
    assert (tmp_path / "fr.json").read_text() == "corrected\n"


class _NoArgsTool(Tool):
    name = "status"
    description = "Check status."
    parameters = {"type": "object", "properties": {}}

    async def execute(self):
        return "ready"


def test_valid_empty_arguments_remain_executable(tmp_path):
    provider = _Provider(
        *(_tools(_call("status")) for _ in range(4)),
        LLMResponse(content="Ready."),
    )
    spec = _spec(tmp_path, provider)
    spec.tools.register(_NoArgsTool())
    result = asyncio.run(AgentRunner().run(spec))
    assert result.stop_reason == "completed"
    assert result.tools_used == ["status"] * 4


class _TemporarilyFailingTool(_NoArgsTool):
    async def execute(self):
        return ToolResult.error("Temporary execution failure.")


def test_ordinary_execution_errors_keep_their_existing_recovery_budget(tmp_path):
    provider = _Provider(
        *(_tools(_call("status")) for _ in range(4)),
        LLMResponse(content="Service is temporarily unavailable."),
    )
    spec = _spec(tmp_path, provider)
    spec.tools.register(_TemporarilyFailingTool())
    result = asyncio.run(AgentRunner().run(spec))
    assert len(provider.requests) == 5
    assert result.stop_reason == "completed"


@pytest.mark.parametrize("iteration_limit, expected_requests", [(12, 3), (2, 3)])
def test_failed_subagent_releases_the_slot_and_reports_failure(
    tmp_path, iteration_limit, expected_requests,
):
    async def run():
        announcements = []
        outbound = queue.Queue()
        session_key = "websocket:parameter-recovery"

        async def publish(message):
            announcements.append(message)

        manager = SubagentManager(
            workspace=tmp_path,
            bus=SimpleNamespace(publish_inbound=publish, outbound=outbound),
            max_tool_result_chars=4000,
            max_iterations=iteration_limit,
            max_concurrent_subagents=1,
        )
        broken = _Provider(_tools(_call()))
        working = _Provider(
            _tools(_call(path="crm.json", content='{"status": "recovered queue"}\n')),
            *(_tools(_call(path=f"module-{i}.json", content='{"status": "translated"}\n')) for i in range(4)),
            _tools(_call("verify", action="check", paths=["crm.json", *(f"module-{i}.json" for i in range(4))], with_tests=False)),
            LLMResponse(content="CRM translations updated."),
        )
        with patch.object(manager, "_build_tools", side_effect=lambda **_: _registry(tmp_path)), patch.object(
            manager, "_build_subagent_prompt", return_value="Complete the assigned task.",
        ):
            await manager.spawn(
                task="Update company translations.", label="i18n-company",
                runtime=_runtime(broken), session_key=session_key,
                origin_channel="websocket", origin_chat_id="parameter-recovery",
            )
            queued = await manager.spawn(
                task="Update CRM translations.", label="i18n-crm-main",
                runtime=_runtime(working), session_key=session_key,
                origin_channel="websocket", origin_chat_id="parameter-recovery",
            )
            assert "queued" in queued
            async with asyncio.timeout(10):
                while manager.get_running_count():
                    await asyncio.gather(*list(manager._running_tasks.values()))
                    await asyncio.sleep(0)

        assert len(broken.requests) == expected_requests
        assert len(working.requests) == 7
        assert (tmp_path / "crm.json").read_text() == '{"status": "recovered queue"}\n'
        assert all((tmp_path / f"module-{i}.json").is_file() for i in range(4))
        assert manager.get_running_count() == 0
        outcomes = manager._outcomes[session_key]
        assert [(o.label, o.status) for o in outcomes] == [
            ("i18n-company", "error"), ("i18n-crm-main", "ok"),
        ]
        assert "Stop reason:" in outcomes[0].summary
        assert len(announcements) == 2
        progress = []
        while not outbound.empty():
            event = outbound.get_nowait().event
            if isinstance(event, SubagentProgressEvent):
                progress.append(event)
        assert any(e.label == "i18n-crm-main" and e.phase == "queued" for e in progress)
        finished = {e.label: e for e in progress if e.done}
        assert finished["i18n-company"].phase == "error"
        assert finished["i18n-company"].error not in {None, "tool_error"}
        assert finished["i18n-crm-main"].phase == "done"
        assert finished["i18n-crm-main"].error is None

    asyncio.run(run())
