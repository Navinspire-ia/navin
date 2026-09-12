# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Development tasks need fresh executed tests on both product entry points."""

from __future__ import annotations

import asyncio
import shlex
import sys
from types import SimpleNamespace

import pytest

from navin.agent.code_validation import CodeValidationState
from navin.agent.loop import AgentLoop
from navin.agent.progress_hook import AgentProgressHook
from navin.agent.runner import AgentLoopGuard, AgentRunner, AgentRunSpec, _is_test_command
from navin.agent.subagent import SubagentManager
from navin.agent.tools.base import ToolResult
from navin.agent.tools.board import BoardTool
from navin.agent.tools.quality import TestRunTool as RunTestsTool
from navin.agent.tools.quality import VerifyTool
from navin.agent.tools.shell import ExecTool
from navin.board.store import ProjectBoardStore
from navin.bus.events import InboundMessage
from navin.bus.outbound_events import StreamDeltaEvent, StreamedResponseEvent
from navin.bus.queue import MessageBus
from navin.providers.base import LLMResponse
from navin.quality.evidence import VerificationEvidence
from navin.quality.evidence import test_evidence as suite_evidence
from navin.quality.testing import TestOutcome as SuiteOutcome
from navin.utils.llm_runtime import LLMRuntime
from tests.test_long_task_continuation import (
    ScriptedProvider,
    isolated_config,  # noqa: F401 - shared fixture isolates the real entry points
    registry,
    tool_call,
)

_PRICING = '''def discounted_total(price, percent):
    if price < 0 or not 0 <= percent <= 100:
        raise ValueError("Invalid price or percentage")
    return round(price * (100 - percent) / 100, 2)
'''
_PRICING_TESTS = '''import pytest

from pricing import discounted_total


@pytest.mark.parametrize("price, percent, expected", [
    (100, 20, 80), (39.99, 15, 33.99), (10, 100, 0),
])
def test_discount_matches_requested_behavior(price, percent, expected):
    assert discounted_total(price, percent) == expected

@pytest.mark.parametrize("price, percent", [(-1, 20), (100, 101)])
def test_invalid_input_is_rejected(price, percent):
    with pytest.raises(ValueError):
        discounted_total(price, percent)
'''


@pytest.mark.parametrize("surface", ["cli", "desktop", "desktop_stream"])
def test_default_entry_point_creates_and_executes_acceptance_tests(tmp_path, surface):
    """No Build metadata: a premature final must still lead to real validation."""
    async def run():
        (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
        provider = ScriptedProvider([
            tool_call("write_file", path="pricing.py", content=_PRICING),
            LLMResponse(content="The function is done."),
            tool_call("write_file", path="test_pricing.py", content=_PRICING_TESTS),
            tool_call("test_run", action="run", runner="pytest", target="test_pricing.py"),
            tool_call("verify", action="check", paths=["pricing.py", "test_pricing.py"], with_tests=False),
            LLMResponse(content="Done: discounts and invalid input verified by 5 passing tests."),
        ])
        loop = AgentLoop(provider=provider, workspace=tmp_path, bus=MessageBus(), max_iterations=2)
        loop._mcp_servers = {}
        loop.tools = registry(tmp_path)
        loop.tools.register(RunTestsTool(workspace=tmp_path))
        loop.tools.register(VerifyTool(workspace=tmp_path))
        task = None
        deltas = []
        key = "cli:direct" if surface == "cli" else "websocket:acceptance-tests"
        request = "Implement discounted_total: round to cents and reject negative prices or percentages outside 0..100."
        try:
            if surface == "cli":
                response = await asyncio.wait_for(loop.process_direct(request), 45)
            else:
                task = asyncio.create_task(loop.run())
                await loop.bus.publish_inbound(InboundMessage(
                    channel="websocket", sender_id="user", chat_id="acceptance-tests",
                    content=request, metadata={"_wants_stream": surface == "desktop_stream"},
                ))
                async with asyncio.timeout(45):
                    while True:
                        response = await loop.bus.consume_outbound()
                        if isinstance(response.event, StreamDeltaEvent):
                            deltas.append(response.event.content)
                        if response.event is None or isinstance(response.event, StreamedResponseEvent):
                            break
            assert response.content == "Done: discounts and invalid input verified by 5 passing tests."
            assert provider.calls == 6
            assert (tmp_path / "test_pricing.py").read_text() == _PRICING_TESTS
            session = loop.sessions.get_or_create(key)
            outputs = [str(m.get("content")) for m in session.messages if m["role"] == "tool"]
            assert any("5 passed" in output for output in outputs)
            assert any("PASS" in output and "Lint: clean" in output for output in outputs)
            assert key not in loop._loop_guards
            if surface != "cli":
                assert session.metadata["_turn_budget_continuation_rounds"] == 2
            if surface == "desktop_stream":
                assert "The function is done." not in "".join(deltas)
                assert "5 passing tests" in "".join(deltas)
        finally:
            loop.stop()
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await loop.close_mcp()
    asyncio.run(run())


def observe(state, name, params, result, status="ok"):
    state.observe(
        name, params, result, status=status, require_verify=False,
        validate_code=True, is_test_command=_is_test_command,
    )


def edited_state():
    state = CodeValidationState()
    observe(state, "apply_patch", {"edits": [{"path": "src/service.py"}]}, "Patched")
    return state


@pytest.mark.parametrize("name, params, result", [
    ("test_run", {"action": "detect"}, "pytest: available"),
    ("verify", {"action": "snapshot"}, "Snapshot saved"),
    ("verify", {"action": "check"}, ToolResult("PASS", verification=VerificationEvidence())),
    ("lint", {"action": "project"}, ToolResult("Clean", verification=VerificationEvidence(checks_ok=True))),
    ("exec", {"command": "pytest --collect-only -q"}, "5 tests collected\nExit code: 0"),
    ("exec", {"command": "pytest || true"}, "1 failed\nExit code: 0"),
    ("exec", {"command": "pytest; echo done"}, "1 failed\nExit code: 0"),
    ("exec", {"command": "pytest | tee result.txt"}, "1 failed\nExit code: 0"),
    ("exec", {"command": "pytest -q"}, "no tests ran\nExit code: 0"),
    ("exec", {"command": "pytest -q"}, "3 skipped\nExit code: 0"),
    ("exec", {"command": "go test ./..."}, "? example.com/app [no test files]\nExit code: 0"),
    ("exec", {"command": "go test -list ."}, "TestApp\nExit code: 0"),
    ("exec", {"command": "npm test -- --listTests=true"}, "app.test.js\nExit code: 0"),
])
def test_discovery_lint_and_masked_test_results_cannot_prove_behavior(name, params, result):
    state = edited_state()
    observe(state, name, params, result)
    assert state.pending


def test_edit_after_green_tests_invalidates_the_evidence():
    state = edited_state()
    observe(state, "exec", {"command": "python -m pytest"}, "4 passed\nExit code: 0")
    assert not state.pending
    observe(state, "edit_file", {"path": "src/service.py"}, "Edited")
    observe(state, "lint", {"action": "project"}, ToolResult("Clean", verification=VerificationEvidence(checks_ok=True)))
    assert state.pending
    observe(state, "exec", {"command": "python -m pytest"}, "4 passed\nExit code: 0")
    assert not state.pending


def test_tests_do_not_hide_a_lint_failure():
    state = edited_state()
    observe(state, "lint", {"action": "file"}, ToolResult("lint failed", verification=VerificationEvidence(checks_ok=False, summary="syntax error")))
    observe(state, "exec", {"command": "pytest"}, "4 passed\nExit code: 0")
    assert state.pending and state.failed
    observe(state, "lint", {"action": "project"}, ToolResult("Clean", verification=VerificationEvidence(checks_ok=True)))
    assert not state.pending


def test_background_tests_only_validate_the_revision_they_started_on():
    state = edited_state()
    observe(state, "exec", {"command": "pytest"}, "Process running. session_id: run1")
    assert state.pending
    observe(state, "write_file", {"path": "src/service.py"}, "Edited again")
    observe(state, "write_stdin", {"session_id": "run1"}, "4 passed\nExit code: 0")
    assert state.pending
    observe(state, "exec", {"command": "pytest"}, "Process running. session_id: run2")
    observe(state, "write_stdin", {"session_id": "run2"}, "4 passed\nExit code: 0")
    assert not state.pending


@pytest.mark.parametrize("path", ["README.md", "report.txt", "report.svg"])
def test_document_edits_do_not_require_boilerplate_tests(path):
    state = CodeValidationState()
    observe(state, "write_file", {"path": path}, "Written")
    assert not state.pending


def test_patch_preview_is_not_a_code_edit():
    state = CodeValidationState()
    observe(state, "apply_patch", {"dry_run": True, "edits": [{"path": "main.py"}]}, "Dry-run succeeded")
    assert not state.pending


@pytest.mark.parametrize("command", [
    "python -B -m pytest tests", "py -m unittest discover",
    r"C:\Python\python.exe -m pytest", "node --test", "cd app && npm test",
    r'"C:\Program Files\Python\python.exe" -m pytest',
])
def test_test_commands_work_for_cli_on_supported_systems(command):
    assert _is_test_command(command)


@pytest.mark.parametrize("command", [
    "npm install test", "cargo run -- test", "cargo nextest list",
    "python -c 'print(1)' -m pytest", "echo pytest", "pip install pytest",
])
def test_a_test_name_in_arguments_is_not_a_test_run(command):
    assert not _is_test_command(command)


@pytest.mark.parametrize("outcomes", [
    [], [SuiteOutcome(runner="pytest", ran=False)],
    [SuiteOutcome(runner="pytest", ran=True, exit_code=0)],
    [SuiteOutcome(runner="pytest", ran=True, skipped=3, total=3, exit_code=0)],
])
def test_zero_executed_tests_cannot_provide_green_evidence(outcomes):
    assert suite_evidence(outcomes).tests_ok is None


@pytest.mark.parametrize("streaming", [False, True, "progress"])
def test_repeated_unverified_final_answers_fail_honestly_across_slices(tmp_path, streaming):
    async def run():
        published = []

        async def on_stream(text):
            published.append(text)

        provider = ScriptedProvider([
            tool_call("write_file", path="pricing.py", content=_PRICING),
            *(LLMResponse(content="Done, everything works.") for _ in range(3)),
        ])
        provider.supports_progress_deltas = streaming == "progress"
        guard = AgentLoopGuard()
        messages = [{"role": "user", "content": "Implement a discount calculator."}]
        for _ in range(4):
            result = await AgentRunner().run(AgentRunSpec(
                initial_messages=messages, tools=registry(tmp_path),
                runtime=LLMRuntime.capture(provider, "test-quality", context_window_tokens=128_000),
                workspace=tmp_path, max_iterations=1, max_tool_result_chars=4_000,
                finalize_on_max_iterations=False, loop_guard=guard, validate_code_changes=True,
                hook=AgentProgressHook(on_stream=on_stream if streaming is True else None),
                progress_callback=on_stream if streaming == "progress" else None,
            ))
            messages = result.messages
            if result.stop_reason != "max_iterations":
                break
            assert result.final_content is None
        assert result.stop_reason == "validation_failed"
        assert result.error == result.final_content
        assert "task is not validated" in result.final_content
        assert "Done, everything works" not in result.final_content
        assert "pricing.py" in result.final_content
        assert not published, "Unverified success claims must not leak into a streamed answer"
        assert (tmp_path / "pricing.py").read_text() == _PRICING
    asyncio.run(run())


def test_shell_edits_cannot_bypass_validation(tmp_path):
    async def run():
        script = "from pathlib import Path; Path('pricing.py').write_text('def total(): return 42\\n')"
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
        provider = ScriptedProvider([
            tool_call("exec", command=command),
            *(LLMResponse(content="Done.") for _ in range(3)),
        ])
        tools = registry(tmp_path)
        tools.register(ExecTool(working_dir=str(tmp_path)))
        result = await AgentRunner().run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Implement pricing."}],
            tools=tools, runtime=LLMRuntime.capture(provider, "test-quality", context_window_tokens=128_000),
            workspace=tmp_path, max_iterations=2, continue_on_max_iterations=True,
            max_tool_result_chars=4_000, validate_code_changes=True,
        ))
        assert (tmp_path / "pricing.py").is_file()
        assert result.stop_reason == "validation_failed"
        assert "pricing.py" in result.final_content
        assert "meaningful tests" in result.final_content
    asyncio.run(run())


def test_real_repair_cycles_have_no_total_verification_retry_cap(tmp_path):
    async def run():
        (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
        (tmp_path / "test_pricing.py").write_text(_PRICING_TESTS)
        responses = []
        for offset in range(5, -1, -1):
            source = _PRICING.replace("100 - percent", f"100 - percent + {offset}")
            responses.extend([
                tool_call("write_file", path="pricing.py", content=source),
                tool_call("test_run", action="run", runner="pytest", target="test_pricing.py"),
                LLMResponse(content="Done: 5 tests passed." if offset == 0 else "Done."),
            ])
        provider = ScriptedProvider(responses)
        tools = registry(tmp_path)
        tools.register(RunTestsTool(workspace=tmp_path))
        spec = AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Fix incorrect discounts and preserve input validation."}],
            tools=tools, runtime=LLMRuntime.capture(provider, "test-quality", context_window_tokens=128_000),
            workspace=tmp_path, max_iterations=2, continue_on_max_iterations=True,
            max_tool_result_chars=4_000, validate_code_changes=True,
        )
        result = await AgentRunner().run(spec)
        assert result.stop_reason == "completed"
        assert result.final_content == "Done: 5 tests passed."
        assert provider.calls == 18
        assert spec.effort_escalations == 5
        tests = [m["content"] for m in result.messages if m.get("name") == "test_run"]
        assert len(tests) == 6
        assert all("failed" in output for output in tests[:-1])
        assert "5 passed" in tests[-1]
    asyncio.run(run())


def test_board_step_without_validation_metadata_still_needs_executed_tests(tmp_path):
    async def run():
        (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
        store = ProjectBoardStore(tmp_path)
        task = store.create_task(title="Fix discounts", status="in_progress", actor="agent", actor_type="agent")
        closing = {"action": "move", "task_id": task["id"], "status": "done", "actor": "agent", "evidence": "Discounts tested."}
        provider = ScriptedProvider([
            tool_call("write_file", path="pricing.py", content=_PRICING),
            tool_call("board", **closing),
            tool_call("write_file", path="test_pricing.py", content=_PRICING_TESTS),
            tool_call("test_run", action="run", runner="pytest", target="test_pricing.py"),
            tool_call("board", **closing),
            LLMResponse(content="Done: discounts tested."),
        ])
        tools = registry(tmp_path)
        tools.register(BoardTool(workspace=tmp_path))
        tools.register(RunTestsTool(workspace=tmp_path))
        result = await AgentRunner().run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Fix the discount task."}],
            tools=tools, runtime=LLMRuntime.capture(provider, "test-quality", context_window_tokens=128_000),
            workspace=tmp_path, max_iterations=2, continue_on_max_iterations=True,
            max_tool_result_chars=4_000, validate_code_changes=True,
        ))
        assert result.stop_reason == "completed"
        assert store.get_task(task["id"])["status"] == "done"
        assert [event["status"] for event in result.tool_events if event["name"] == "board"] == ["error", "ok"]
        assert provider.calls == 6
    asyncio.run(run())


def test_subagent_cannot_report_untested_code_as_completed(tmp_path, monkeypatch):
    async def run():
        announcements = []

        async def announce(message):
            announcements.append(message)

        provider = ScriptedProvider([
            tool_call("write_file", path="pricing.py", content=_PRICING),
            *(LLMResponse(content="Everything works.") for _ in range(3)),
        ])
        manager = SubagentManager(
            workspace=tmp_path, bus=SimpleNamespace(publish_inbound=announce),
            max_iterations=2, max_tool_result_chars=4_000,
        )
        monkeypatch.setattr(manager, "_build_tools", lambda **_: registry(tmp_path))
        monkeypatch.setattr(manager, "_build_subagent_prompt", lambda *a, **kw: "Complete the pricing task.")
        key = "cli:untested-subagent"
        await manager.spawn(
            task="Implement discounts.", label="pricing", session_key=key,
            runtime=LLMRuntime.capture(provider, "test-quality", context_window_tokens=128_000),
            origin_channel="cli", origin_chat_id="untested-subagent",
        )
        async with asyncio.timeout(10):
            while manager.get_running_count():
                await asyncio.gather(*list(manager._running_tasks.values()))
                await asyncio.sleep(0)
        assert len(announcements) == 1
        outcome = manager.recent_outcomes(session_key=key)[0]
        assert outcome.status == "error"
        assert "task is not validated" in outcome.summary
        assert "pricing.py" in outcome.summary
    asyncio.run(run())
