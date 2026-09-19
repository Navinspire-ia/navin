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
@pytest.mark.parametrize("premature_board_closures", [False, True])
def test_default_entry_point_creates_and_executes_acceptance_tests(tmp_path, surface, premature_board_closures):
    """No Build metadata: a premature final must still lead to real validation."""
    async def run():
        (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
        store = ProjectBoardStore(tmp_path)
        board_task = store.create_task(title="Fix discounts", status="in_progress", actor="agent", actor_type="agent")
        closing = {"action": "move", "task_id": board_task["id"], "status": "done", "actor": "agent"}
        provider = ScriptedProvider([
            tool_call("write_file", path="pricing.py", content=_PRICING),
            *([tool_call("board", **closing) for _ in range(5)] if premature_board_closures
              else [LLMResponse(content="The function is done.")]),
            tool_call("write_file", path="test_pricing.py", content=_PRICING_TESTS),
            tool_call("test_run", action="run", runner="pytest", target="test_pricing.py"),
            tool_call("verify", action="check", paths=["pricing.py", "test_pricing.py"], with_tests=False),
            *([tool_call("board", **closing)] if premature_board_closures else []),
            LLMResponse(content="Done: discounts and invalid input verified by 5 passing tests."),
        ])
        loop = AgentLoop(provider=provider, workspace=tmp_path, bus=MessageBus(), max_iterations=2)
        loop._mcp_servers = {}
        loop.tools = registry(tmp_path)
        loop.tools.register(RunTestsTool(workspace=tmp_path))
        loop.tools.register(VerifyTool(workspace=tmp_path))
        loop.tools.register(BoardTool(workspace=tmp_path))
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
            assert provider.calls == (11 if premature_board_closures else 6)
            if premature_board_closures:
                assert store.get_task(board_task["id"])["status"] == "done"
            assert (tmp_path / "test_pricing.py").read_text() == _PRICING_TESTS
            session = loop.sessions.get_or_create(key)
            outputs = [str(m.get("content")) for m in session.messages if m["role"] == "tool"]
            assert any("5 passed" in output for output in outputs)
            assert any("PASS" in output and "Lint: clean" in output for output in outputs)
            assert key not in loop._loop_guards
            if surface != "cli" and not premature_board_closures:
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


def test_completion_report_distinguishes_missing_failed_running_and_stale_results():
    state = edited_state()
    assert "| Tests | No current result |" in state.completion_message()
    observe(state, "exec", {"command": "pytest"}, "1 failed\nExit code: 1")
    assert "| Tests | Failed |" in state.completion_message()
    assert "```text\n1 failed\nExit code: 1\n```" in state.completion_message()
    observe(state, "exec", {"command": "pytest"}, "6 passed\nExit code: 0")
    observe(state, "edit_file", {"path": "src/service.py"}, "Edited")
    assert "| Tests | Needs rerun after edits |" in state.completion_message()
    observe(state, "exec", {"command": "pytest"}, "Process running. session_id: tests-1")
    report = state.completion_message(reason="ended")
    assert "| Tests | Still running |" in report
    assert "Wait for the running tests" in report
    assert "Run meaningful tests" not in report


def test_running_test_guidance_waits_for_the_current_revision_only():
    state = edited_state()
    observe(state, "exec", {"command": "pytest"}, "Process running. session_id: checks-1")
    assert "write_stdin" in state.missing()
    assert "session_id: checks-1" in state.missing()
    assert "Run meaningful tests" not in state.missing()
    observe(state, "edit_file", {"path": "src/service.py"}, "Edited again")
    assert "Run meaningful tests" in state.missing()
    observe(state, "write_stdin", {"session_id": "checks-1"}, "6 passed\nExit code: 0")
    assert state.pending


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


@pytest.mark.parametrize("command", [
    "cd supabase/audit && python3 -m pytest test_clean_dump.py 2>&1 | tail -1",
    "pytest -q | tail -n 1",
    "pytest -q | tee result.txt | tail --lines=3",
    "uv run pytest -q | tee result.txt",
])
def test_filtered_pytest_summary_counts_as_validation(command):
    state = edited_state()
    observe(state, "exec", {"command": command},
            "============================== 6 passed in 4.66s ===============================\nExit code: 0")
    assert not state.pending


@pytest.mark.parametrize("summary, failed", [
    ("1 failed, 5 passed in 4.66s", True),
    ("5 passed, 1 error in 4.66s", True),
    ("6 skipped in 4.66s", False),
    ("no tests ran in 0.01s", False),
    ("6 passed", False),
    ("", False),
])
def test_filter_exit_zero_is_not_proof_of_passing_tests(summary, failed):
    state = edited_state()
    observe(state, "exec", {"command": "python -m pytest 2>&1 | tail -1"}, summary + "\nExit code: 0")
    assert state.pending
    assert state.failed is failed
    if not failed:
        assert "Run pytest directly without the output filter" in state.missing()


@pytest.mark.parametrize("command", [
    "pytest | tail -1 other-results.txt",
    "pytest | head -1",
    "pytest | grep passed",
    "pytest | tail -1 || true",
    "pytest | tail -1; echo done",
    "pytest && false | tail -1",
    "echo pytest | tail -1",
    "pytest --collect-only | tail -1",
])
def test_other_commands_cannot_supply_filtered_test_evidence(command):
    state = edited_state()
    observe(state, "exec", {"command": command}, "6 passed in 4.66s\nExit code: 0")
    assert state.pending


@pytest.mark.parametrize("edit_during_run", [False, True])
def test_background_filtered_tests_wait_for_exit_and_preserve_revision(edit_during_run):
    state = edited_state()
    observe(state, "exec", {"command": "pytest -q | tail -1"}, "Process running. session_id: filtered-1")
    observe(state, "write_stdin", {"session_id": "filtered-1"}, "6 passed in 4.66s\n")
    assert state.pending
    if edit_during_run:
        observe(state, "edit_file", {"path": "src/service.py"}, "Edited again")
    observe(state, "write_stdin", {"session_id": "filtered-1"}, "Exit code: 0")
    assert state.pending is edit_during_run
    assert not state.pending_tests
    assert not state.pending_filtered_outputs


@pytest.mark.parametrize("passing", [True, False])
def test_real_pytest_tail_pipeline_observes_success_and_masked_failure(tmp_path, passing):
    async def run():
        audit = tmp_path / "supabase" / "audit"
        audit.mkdir(parents=True)
        (audit / "test_clean_dump.py").write_text(
            "import pytest\n@pytest.mark.parametrize('item', range(6))\n"
            f"def test_clean_dump(item):\n    assert item < {6 if passing else 5}\n"
        )
        command = f"cd supabase/audit && {shlex.quote(sys.executable)} -m pytest test_clean_dump.py 2>&1 | tail -1"
        result = await ExecTool(working_dir=str(tmp_path)).execute(command=command)
        assert "Exit code: 0" in str(result), "The output filter masks the test runner's exit status"
        assert ("6 passed" if passing else "1 failed, 5 passed") in str(result)
        state = edited_state()
        observe(state, "exec", {"command": command}, result)
        assert state.pending is not passing
        assert state.failed is not passing
    asyncio.run(run())


def test_edit_after_green_tests_invalidates_the_evidence():
    state = edited_state()
    observe(state, "exec", {"command": "python -m pytest"}, "4 passed\nExit code: 0")
    assert not state.pending
    observe(state, "edit_file", {"path": "src/service.py"}, "Edited")
    observe(state, "lint", {"action": "project"}, ToolResult("Clean", verification=VerificationEvidence(checks_ok=True)))
    assert state.pending
    observe(state, "exec", {"command": "python -m pytest"}, "4 passed\nExit code: 0")
    assert not state.pending


@pytest.mark.parametrize("summary, expected", [
    ("Ran 3 tests in 0.01s\nOK", True),
    ("Ran 3 tests in 0.01s\nOK (skipped=3)", False),
    ("Ran 3 tests in 0.01s\nOK (skipped=1)", True),
    ("", False),
    ("3 passed in 0.01s", True),
])
def test_direct_audit_script_requires_executed_tests(summary, expected):
    state = edited_state()
    observe(state, "exec", {"command": "python -B supabase/audit/test_stock_fix.py"}, summary + "\nExit code: 0")
    assert state.pending is not expected


def test_background_audit_script_keeps_summary_until_process_exits():
    state = edited_state()
    observe(state, "exec", {"command": "python supabase/audit/test_stock_fix.py"}, "session_id: audit-1")
    observe(state, "write_stdin", {"session_id": "audit-1"}, "Ran 2 tests in 0.01s\nOK\n")
    assert state.pending
    observe(state, "write_stdin", {"session_id": "audit-1"}, "Exit code: 0")
    assert not state.pending


def test_direct_unittest_audit_script_really_executes(tmp_path):
    async def run():
        script = tmp_path / "test_audit.py"
        script.write_text("import unittest\nclass Audit(unittest.TestCase):\n    def test_total(self):\n        self.assertEqual(sum([2, 3]), 5)\nunittest.main()\n")
        state = edited_state()
        command = shlex.quote(sys.executable) + " test_audit.py"
        result = await ExecTool(working_dir=str(tmp_path)).execute(command=command)
        observe(state, "exec", {"command": command}, result)
        assert "Ran 1 test" in str(result)
        assert not state.pending
    asyncio.run(run())


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


@pytest.mark.parametrize("rejected_closures", [1, 3, 5])
@pytest.mark.parametrize("test_executor", ["test_run", "exec_pipe"])
def test_board_step_without_validation_metadata_still_needs_executed_tests(tmp_path, rejected_closures, test_executor):
    async def run():
        (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
        store = ProjectBoardStore(tmp_path)
        task = store.create_task(title="Fix discounts", status="in_progress", actor="agent", actor_type="agent")
        closing = {"action": "move", "task_id": task["id"], "status": "done", "actor": "agent", "evidence": "Discounts tested."}
        provider = ScriptedProvider([
            tool_call("write_file", path="pricing.py", content=_PRICING),
            *(tool_call("board", **closing) for _ in range(rejected_closures)),
            tool_call("write_file", path="test_pricing.py", content=_PRICING_TESTS),
            (tool_call("test_run", action="run", runner="pytest", target="test_pricing.py")
             if test_executor == "test_run" else tool_call(
                 "exec", command=f"{shlex.quote(sys.executable)} -m pytest test_pricing.py 2>&1 | tail -1",
             )),
            tool_call("board", **closing),
            LLMResponse(content="Done: discounts tested."),
        ])
        tools = registry(tmp_path)
        tools.register(BoardTool(workspace=tmp_path))
        tools.register(RunTestsTool(workspace=tmp_path))
        tools.register(ExecTool(working_dir=str(tmp_path)))
        result = await AgentRunner().run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Fix the discount task."}],
            tools=tools, runtime=LLMRuntime.capture(provider, "test-quality", context_window_tokens=128_000),
            workspace=tmp_path, max_iterations=2, continue_on_max_iterations=True,
            max_tool_result_chars=4_000, validate_code_changes=True,
        ))
        assert result.stop_reason == "completed"
        assert store.get_task(task["id"])["status"] == "done"
        board_events = [event for event in result.tool_events if event["name"] == "board"]
        assert [event["status"] for event in board_events] == ["error"] * rejected_closures + ["ok"]
        assert all(event["error_kind"] == "validation_required" for event in board_events[:-1])
        assert provider.calls == 5 + rejected_closures
    asyncio.run(run())


def test_repeated_board_closures_without_validation_stop_without_closing_the_task(tmp_path):
    async def run():
        store = ProjectBoardStore(tmp_path)
        task = store.create_task(title="Fix discounts", status="in_progress", actor="agent", actor_type="agent")
        provider = ScriptedProvider([
            tool_call("write_file", path="pricing.py", content=_PRICING),
            *(tool_call("board", action="move", task_id=task["id"], status="done") for _ in range(20)),
        ])
        tools = registry(tmp_path)
        tools.register(BoardTool(workspace=tmp_path))
        result = await AgentRunner().run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Fix the discount task."}],
            tools=tools, runtime=LLMRuntime.capture(provider, "test-quality", context_window_tokens=128_000),
            workspace=tmp_path, max_iterations=2, continue_on_max_iterations=True,
            validate_code_changes=True, max_tool_result_chars=4_000,
        ))
        assert result.stop_reason == "validation_failed"
        assert store.get_task(task["id"])["status"] == "in_progress"
        assert provider.calls < 21
        assert "No passing test result" in result.final_content
        assert all(event.get("error_kind") == "validation_required"
                   for event in result.tool_events if event["name"] == "board")
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
