# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent behaviour on dev tasks, driven by a scripted model through the real
runner and loop: the edit/test/fix cycle finishes on its own, spinning models
are stopped within a bounded number of steps with a clear report, parallel
work is offered through spawn, and the recovery paths (blank reply, length
cutoff, broken tool JSON) are all bounded.
"""

from __future__ import annotations

import asyncio
import sys
from collections import deque
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from navin.agent.loop import AgentLoop
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tool_surface import CODE_BUILD_ALLOWED_TOOLS, denied_tools_for_composer_mode
from navin.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from navin.agent.tools.registry import ToolRegistry
from navin.agent.tools.shell import ExecTool
from navin.bus.events import InboundMessage
from navin.bus.queue import MessageBus
from navin.config.loader import get_config_path, set_config_path
from navin.providers.base import LLMProvider, LLMResponse, ToolCallRequest, parse_tool_arguments
from navin.utils.llm_runtime import LLMRuntime

# Never ask the user to type "continue" / resend: the product resumes on its own.
_ASKS_TO_CONTINUE = ('"continue"', "reply **continue**", "send continue", "type continue", "resend")


class Scripted(LLMProvider):
    """Plays a fixed script; repeats ``tail`` forever once it runs out."""

    def __init__(self, script: list[LLMResponse], tail: LLMResponse | None = None) -> None:
        super().__init__()
        self.script = deque(script)
        self.tail = tail
        self.calls = 0

    def get_default_model(self) -> str:
        return "scripted-dev"

    async def chat(self, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.script:
            response = self.script.popleft()
        else:
            assert self.tail is not None, "the run asked for more steps than scripted"
            response = self.tail
        response = LLMResponse(
            content=response.content,
            tool_calls=[
                ToolCallRequest(id=f"call-{self.calls}-{i}", name=c.name, arguments=c.arguments)
                for i, c in enumerate(response.tool_calls)
            ],
            finish_reason=response.finish_reason,
        )
        return response

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat(**kwargs)

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        response = await self.chat(**kwargs)
        on_delta = kwargs.get("on_content_delta")
        if on_delta and response.content:
            await on_delta(response.content)
        return response


def call(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(
        content="", finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(id="x", name=name, arguments=arguments)],
    )


def final(text: str, finish: str = "stop") -> LLMResponse:
    return LLMResponse(content=text, finish_reason=finish)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path):
    old = get_config_path()
    set_config_path(tmp_path / "config.json")
    try:
        with patch("navin.agent.skills._home_skill_dirs", return_value=[]):
            yield
    finally:
        set_config_path(old)


def _tools(root: Path, *, exec_tool: bool = True) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(ReadFileTool(workspace=root))
    tools.register(WriteFileTool(workspace=root))
    tools.register(EditFileTool(workspace=root))
    if exec_tool:
        tools.register(ExecTool(working_dir=str(root), timeout=60))
    return tools


def _spec(provider: LLMProvider, root: Path, **kw: Any) -> AgentRunSpec:
    kw.setdefault("max_iterations", 20)
    return AgentRunSpec(
        initial_messages=[{"role": "user", "content": kw.pop("prompt", "Fix the bug in calc.py and make the tests pass.")}],
        tools=kw.pop("tools", None) or _tools(root),
        runtime=LLMRuntime.capture(provider, "scripted-dev", context_window_tokens=128_000),
        max_tool_result_chars=8000,
        workspace=root,
        **kw,
    )


def _assert_never_asks_to_continue(text: str | None) -> None:
    lowered = (text or "").lower()
    for phrase in _ASKS_TO_CONTINUE:
        assert phrase not in lowered, f"asked the user to continue: {text!r}"


PYTEST = f"{Path(sys.executable).as_posix()} -m pytest -q -p no:cacheprovider test_calc.py"


def _dev_cycle_script() -> list[LLMResponse]:
    return [
        call("write_file", path="calc.py", content="def add(a, b):\n    return a - b\n"),
        call("write_file", path="test_calc.py", content="from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"),
        call("exec", command=PYTEST),
        call("read_file", path="calc.py"),
        call("edit_file", path="calc.py", old_text="return a - b", new_text="return a + b"),
        call("exec", command=PYTEST),
        final("Fixed `add` in calc.py; the test suite is green."),
    ]


# ---------------------------------------------------------------------------
# Edit -> test -> fix -> green, with no "continue" from the user
# ---------------------------------------------------------------------------


def test_dev_cycle_completes_across_budget_slices_in_one_run(tmp_path):
    """Subagents / one-shot CLI: the runner keeps going past max_iterations."""
    provider = Scripted(_dev_cycle_script())
    result = asyncio.run(AgentRunner().run(_spec(
        provider, tmp_path, max_iterations=2, continue_on_max_iterations=True, validate_code_changes=True,
    )))
    assert result.stop_reason == "completed", result.final_content
    assert result.final_content == "Fixed `add` in calc.py; the test suite is green."
    assert (tmp_path / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert provider.calls == 7
    exec_events = [e for e in result.tool_events if e["name"] == "exec"]
    assert [e.get("exit_code") for e in exec_events] == ["1", "0"]
    _assert_never_asks_to_continue(result.final_content)


def test_dev_cycle_completes_through_gateway_continuations(tmp_path):
    """WebUI/desktop: max_iterations slices are resumed internally."""
    root = tmp_path / "ws"
    root.mkdir()
    provider = Scripted(_dev_cycle_script())

    async def run():
        loop = AgentLoop(provider=provider, workspace=root, bus=MessageBus(), max_iterations=2)
        loop._mcp_servers = {}
        loop.tools = _tools(root)
        task = asyncio.create_task(loop.run())
        seen: list[str] = []
        try:
            await loop.bus.publish_inbound(InboundMessage(
                channel="websocket", sender_id="user", chat_id="dev-cycle",
                content="Fix the bug in calc.py and make the tests pass.",
                metadata={"_wants_stream": False, "composer_mode": "agent"},
            ))
            async with asyncio.timeout(90):
                while True:
                    out = await loop.bus.consume_outbound()
                    if out.content:
                        seen.append(out.content)
                    if "the test suite is green" in (out.content or ""):
                        break
            return loop.sessions.get_or_create("websocket:dev-cycle"), seen
        finally:
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await loop.close_mcp()

    session, seen = asyncio.run(run())
    assert (root / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert provider.calls == 7
    for text in seen:
        _assert_never_asks_to_continue(text)
        assert "safety cap" not in text
    # One user request, no synthetic "continue" turn persisted as the user.
    assert sum(m["role"] == "user" for m in session.messages) == 1
    assert session.messages[-1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Spinning models are stopped, with a report
# ---------------------------------------------------------------------------


def test_rereading_the_same_file_forever_is_stopped_with_a_report(tmp_path):
    (tmp_path / "calc.py").write_text("x = 1\n")
    provider = Scripted([], tail=call("read_file", path="calc.py"))
    result = asyncio.run(AgentRunner().run(_spec(provider, tmp_path, max_iterations=40)))
    assert result.stop_reason == "no_progress"
    assert provider.calls <= 12  # 2 real reads + blocked repeats + finalization
    assert (result.final_content or "").strip()
    _assert_never_asks_to_continue(result.final_content)


def test_alternating_rereads_of_the_same_files_are_stopped(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / "b.py").write_text("b = 1\n")
    script = [call("read_file", path=p) for p in ["a.py", "b.py"] * 30]
    provider = Scripted(script, tail=call("read_file", path="a.py"))
    result = asyncio.run(AgentRunner().run(_spec(provider, tmp_path, max_iterations=60)))
    assert result.stop_reason == "no_progress"
    assert provider.calls <= 16
    assert (result.final_content or "").strip()


def test_repeating_the_same_failing_call_is_bounded(tmp_path):
    (tmp_path / "calc.py").write_text("x = 1\n")
    provider = Scripted([], tail=call("edit_file", path="calc.py", old_text="nope", new_text="y"))
    result = asyncio.run(AgentRunner().run(_spec(provider, tmp_path, max_iterations=60)))
    assert result.stop_reason in {"no_progress", "tool_error"}
    assert provider.calls <= 16
    assert (result.final_content or "").strip()
    assert (tmp_path / "calc.py").read_text() == "x = 1\n"


def test_rerunning_the_same_passing_command_is_stopped_in_an_unbounded_run(tmp_path):
    command = f"{Path(sys.executable).as_posix()} -c \"print('ok')\""
    provider = Scripted([], tail=call("exec", command=command))
    result = asyncio.run(asyncio.wait_for(AgentRunner().run(_spec(
        provider, tmp_path, max_iterations=3, continue_on_max_iterations=True,
    )), 120))
    # Two slices in a row that changed nothing end the run, or the repeated
    # command is reminded about and the turn answers once it is ignored twice.
    assert result.stop_reason in {"max_iterations", "no_progress"}
    # Stalled = 2+ slices and 12+ calls without change: 1 + 4 slices of 3, plus
    # the finalization request.
    assert provider.calls <= 16
    assert (result.final_content or "").strip()
    _assert_never_asks_to_continue(result.final_content)


def test_gateway_slices_that_rerun_a_passing_command_end_with_a_stuck_report(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    command = f"{Path(sys.executable).as_posix()} -c \"print('ok')\""
    report = "Stuck: the check passes but there is nothing left to change."
    provider = Scripted([call("exec", command=command) for _ in range(12)], tail=final(report))

    async def run():
        loop = AgentLoop(provider=provider, workspace=root, bus=MessageBus(), max_iterations=2)
        loop._mcp_servers = {}
        loop.tools = _tools(root)
        task = asyncio.create_task(loop.run())
        try:
            await loop.bus.publish_inbound(InboundMessage(
                channel="websocket", sender_id="user", chat_id="spin",
                content="Run the check until it is fine.",
                metadata={"_wants_stream": False, "composer_mode": "agent"},
            ))
            async with asyncio.timeout(90):
                while True:
                    out = await loop.bus.consume_outbound()
                    if out.content and "Stuck" in out.content:
                        return out.content
        finally:
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await loop.close_mcp()

    text = asyncio.run(run())
    assert text == report
    # Bounded: one slice that ran it the first time, six unchanged slices of
    # two steps (12 calls), then the report slice with no tools.
    assert provider.calls <= 14


# ---------------------------------------------------------------------------
# Parallel work: spawn is offered and the prompt says when to use it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["agent", "debug", "review", "security", "reverse", "plan"])
def test_spawn_stays_in_the_tool_surface_of_working_modes(mode):
    assert "spawn" not in denied_tools_for_composer_mode(mode)


def test_spawn_is_in_the_code_module_allowlist_and_ask_mode_denies_it():
    assert "spawn" in CODE_BUILD_ALLOWED_TOOLS
    assert "spawn" in denied_tools_for_composer_mode("ask")


def test_loop_registers_spawn_and_prompt_asks_for_one_subagent_per_task(tmp_path):
    loop = AgentLoop(provider=Scripted([final("ok")]), workspace=tmp_path, bus=MessageBus())
    loop._mcp_servers = {}
    assert loop.tools.get("spawn") is not None
    for slim in (True, False):
        prompt = loop.context.build_system_prompt(channel="websocket", slim_skill_preload=slim)
        lowered = prompt.lower()
        assert "one subagent per task" in lowered  # identity.md
        assert "same reply" in lowered
        assert (
            ("one `spawn` per task" in lowered)
            or ("one `spawn` subagent per task" in lowered)
            or ("one `spawn` each" in lowered)  # tool notes, reworded in cbe50202
        )


# ---------------------------------------------------------------------------
# Recovery paths are bounded
# ---------------------------------------------------------------------------


def test_blank_replies_are_bounded_and_end_without_asking_to_continue(tmp_path):
    provider = Scripted([], tail=final(""))
    result = asyncio.run(AgentRunner().run(_spec(provider, tmp_path, max_iterations=30)))
    assert result.stop_reason == "empty_final_response"
    assert provider.calls <= 5
    assert (result.final_content or "").strip()
    _assert_never_asks_to_continue(result.final_content)


def test_length_cutoffs_are_bounded_and_the_text_is_kept_whole(tmp_path):
    provider = Scripted([], tail=final("part ", finish="length"))
    result = asyncio.run(AgentRunner().run(_spec(provider, tmp_path, max_iterations=30, max_length_recoveries=3)))
    assert provider.calls == 4
    assert result.final_content == "part part part part"


def test_length_cutoff_then_finish_joins_the_pieces(tmp_path):
    provider = Scripted([final("The plan has three ", finish="length"), final("steps.")])
    result = asyncio.run(AgentRunner().run(_spec(provider, tmp_path)))
    assert result.stop_reason == "completed"
    assert result.final_content == "The plan has three steps."


def test_invalid_tool_json_is_bounded(tmp_path):
    broken = LLMResponse(
        content="", finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(id="x", name="read_file", arguments=parse_tool_arguments('{"path": "calc.py'))],
    )
    provider = Scripted([], tail=broken)
    result = asyncio.run(AgentRunner().run(_spec(provider, tmp_path, max_iterations=40, tools=_tools(tmp_path, exec_tool=False))))
    # Regression: the rejected call was counted as a successful read, so the
    # model was told "this read already ran" and the invalid-arguments guard
    # never fired (11 calls, misleading "repeated read" errors).
    assert result.stop_reason == "tool_error"
    assert provider.calls == 3
    # The user sees a plain sentence; the technical cause stays in result.error.
    assert "invalid tool arguments" in (result.error or "")
    assert "Error:" not in (result.final_content or "")
    assert all(e.get("error_kind") == "invalid_parameters" for e in result.tool_events)


def test_invalid_tool_json_recovers_when_the_model_fixes_it(tmp_path):
    (tmp_path / "calc.py").write_text("x = 1\n")
    broken = LLMResponse(
        content="", finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(id="x", name="read_file", arguments=parse_tool_arguments('{"path": "calc.py'))],
    )
    provider = Scripted([broken, call("read_file", path="calc.py"), final("x is 1.")])
    result = asyncio.run(AgentRunner().run(_spec(provider, tmp_path, tools=_tools(tmp_path, exec_tool=False))))
    assert result.stop_reason == "completed"
    assert result.final_content == "x is 1."
