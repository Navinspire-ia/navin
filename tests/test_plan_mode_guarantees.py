"""Plan is really non-mutating, and mode switches apply mid-turn.

The audit found the Plan promise ("design only, no code until Build") was a
brief the runner did not enforce: exec, git commit and manage_files all went
through. These tests pin the enforcement:

* Plan refuses every mutating call (edits, shell, git writes) at the runner.
* Plan keeps its read paths (git status, board list) and its planning
  surfaces (board writes, ask_user, set_composer_mode).
* A successful set_composer_mode changes the policy in the same turn - the
  documented plan→agent simple-task handoff - and tightening (→plan, →ask)
  applies immediately too. Ask's read-only promise is never lifted mid-turn.
* Module-level denials (Code product decisions) survive any mode switch.
"""

from __future__ import annotations

import unittest
from typing import Any

from navin.agent.hook import AgentHook
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tool_surface import denied_tools_for_composer_mode
from navin.agent.tools.base import Tool
from navin.agent.tools.board import BoardTool
from navin.agent.tools.git import GitTool
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime


class _FakeProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat_with_retry(**kwargs)


def _runtime(provider: Any) -> LLMRuntime:
    return LLMRuntime(
        provider=provider,
        model="test-model",
        generation=GenerationSettings(),
        context_window_tokens=128_000,
    )


class _StubTool(Tool):
    """Minimal named tool; subclasses set the class attributes."""

    _name = "stub"
    _read_only = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def read_only(self) -> bool:
        return self._read_only

    async def execute(self, **kwargs: Any) -> str:
        return f"{self._name} ok"


class _GitLikeTool(_StubTool):
    """git multiplexes reads and writes behind one name, like the real tool."""

    _name = "git"

    def call_read_only(self, arguments: Any) -> bool:
        if not isinstance(arguments, dict):
            return False
        return arguments.get("action") in {"status", "diff", "log"}

    async def execute(self, **kwargs: Any) -> str:
        return f"git {kwargs.get('action')} ok"


class _BoardLikeTool(_StubTool):
    _name = "board"

    def call_read_only(self, arguments: Any) -> bool:
        if not isinstance(arguments, dict):
            return False
        return arguments.get("action") in {"list", "next", "plan", "get"}

    async def execute(self, **kwargs: Any) -> str:
        return f"board {kwargs.get('action')} ok"


class _SpawnLikeTool(_StubTool):
    """spawn multiplexes a read (results) and a write (start), like the real tool."""

    _name = "spawn"

    def call_read_only(self, arguments: Any) -> bool:
        if not isinstance(arguments, dict):
            return False
        return arguments.get("action") == "results"

    async def execute(self, **kwargs: Any) -> str:
        return f"spawn {kwargs.get('action', 'start')} ok"


class _SetComposerModeTool(_StubTool):
    _name = "set_composer_mode"
    _read_only = True

    async def execute(self, **kwargs: Any) -> str:
        return f"Composer mode set to {kwargs.get('mode')}."


class _WriteFileTool(_StubTool):
    _name = "write_file"


class _ExecTool(_StubTool):
    _name = "exec"


class _ScrapeTool(_StubTool):
    _name = "scrape"


class _ReadFileTool(_StubTool):
    _name = "read_file"
    _read_only = True


def _tools() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        _GitLikeTool(),
        _BoardLikeTool(),
        _SpawnLikeTool(),
        _SetComposerModeTool(),
        _WriteFileTool(),
        _ExecTool(),
        _ScrapeTool(),
        _ReadFileTool(),
    ):
        registry.register(tool)
    return registry


def _call(name: str, call_id: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(id=call_id, name=name, arguments=arguments)],
    )


_DONE = LLMResponse(content="done", finish_reason="stop")


def _plan_spec(responses: list[LLMResponse], **overrides: Any) -> AgentRunSpec:
    defaults: dict[str, Any] = dict(
        initial_messages=[{"role": "user", "content": "plan the feature"}],
        tools=_tools(),
        runtime=_runtime(_FakeProvider(responses)),
        max_iterations=8,
        max_tool_result_chars=2000,
        hook=AgentHook(),
        plan_read_only=True,
        composer_mode="plan",
        denied_tools=denied_tools_for_composer_mode("plan"),
    )
    defaults.update(overrides)
    return AgentRunSpec(**defaults)


def _events(result: Any, name: str) -> list[dict[str, str]]:
    return [e for e in result.tool_events if e.get("name") == name]


class PlanModeBlocksMutationsTest(unittest.IsolatedAsyncioTestCase):
    async def test_git_status_answers_and_git_commit_refuses(self) -> None:
        result = await AgentRunner().run(_plan_spec([
            _call("git", "1", action="status"),
            _call("git", "2", action="commit", message="wip"),
            _DONE,
        ]))
        status, commit = _events(result, "git")
        self.assertEqual(status["status"], "ok")
        self.assertEqual(commit["detail"], "blocked by plan mode")

    async def test_exec_is_refused_in_plan(self) -> None:
        result = await AgentRunner().run(_plan_spec([
            _call("exec", "1", command="pip install requests"),
            _DONE,
        ]))
        (event,) = _events(result, "exec")
        self.assertEqual(event["detail"], "blocked by plan mode")
        blocked_payloads = [
            m for m in result.messages
            if m.get("role") == "tool" and "Plan mode" in str(m.get("content", ""))
        ]
        self.assertTrue(blocked_payloads, "the refusal must name Plan mode")

    async def test_write_file_is_refused_in_plan(self) -> None:
        result = await AgentRunner().run(_plan_spec([
            _call("write_file", "1", path="a.py", content="x"),
            _DONE,
        ]))
        (event,) = _events(result, "write_file")
        self.assertEqual(event["detail"], "blocked by plan mode")

    async def test_spawn_results_answers_and_spawn_start_refuses(self) -> None:
        """A plan folds in finished research; it does not delegate new work.

        The corpus case plan-05 asks for exactly this, and used to pass only
        because the offline mock printed the word 'spawn' while the runtime
        refused the whole tool.
        """
        result = await AgentRunner().run(_plan_spec([
            _call("spawn", "1", action="results"),
            _call("spawn", "2", action="start", task="rewrite the parser"),
            _DONE,
        ]))
        results_call, start_call = _events(result, "spawn")
        self.assertEqual(results_call["status"], "ok")
        self.assertEqual(start_call["detail"], "blocked by plan mode")

    async def test_spawn_stays_in_the_plan_schema(self) -> None:
        """Denying the schema would also deny the read action."""
        self.assertNotIn("spawn", denied_tools_for_composer_mode("plan"))

    async def test_filing_board_tasks_is_how_a_plan_is_delivered(self) -> None:
        result = await AgentRunner().run(_plan_spec([
            _call("board", "1", action="create", title="step 1"),
            _call("board", "2", action="list"),
            _DONE,
        ]))
        create, listed = _events(result, "board")
        self.assertEqual(create["status"], "ok")
        self.assertEqual(listed["status"], "ok")

    async def test_reads_flow_freely(self) -> None:
        result = await AgentRunner().run(_plan_spec([
            _call("read_file", "1", path="a.py"),
            _DONE,
        ]))
        (event,) = _events(result, "read_file")
        self.assertEqual(event["status"], "ok")


class MidTurnModeSwitchTest(unittest.IsolatedAsyncioTestCase):
    async def test_plan_to_agent_handoff_unlocks_build_in_the_same_turn(self) -> None:
        """The brief's simple-task exception: switch, then do the work now."""
        result = await AgentRunner().run(_plan_spec([
            _call("set_composer_mode", "1", mode="agent"),
            _call("write_file", "2", path="a.py", content="x"),
            _call("git", "3", action="commit", message="feat"),
            _DONE,
        ]))
        (write,) = _events(result, "write_file")
        (commit,) = _events(result, "git")
        self.assertEqual(write["status"], "ok")
        self.assertEqual(commit["status"], "ok")

    async def test_agent_to_plan_switch_tightens_in_the_same_turn(self) -> None:
        result = await AgentRunner().run(_plan_spec(
            [
                _call("set_composer_mode", "1", mode="plan"),
                _call("write_file", "2", path="a.py", content="x"),
                _DONE,
            ],
            plan_read_only=False,
            composer_mode="agent",
            denied_tools=frozenset(),
        ))
        (write,) = _events(result, "write_file")
        self.assertEqual(write["detail"], "blocked by plan mode")

    async def test_module_denials_survive_a_mode_switch(self) -> None:
        result = await AgentRunner().run(_plan_spec(
            [
                _call("set_composer_mode", "1", mode="agent"),
                _call("scrape", "2", url="https://example.com"),
                _DONE,
            ],
            denied_tools=denied_tools_for_composer_mode("plan") | {"scrape"},
            locked_denied_tools=frozenset({"scrape"}),
        ))
        (event,) = _events(result, "scrape")
        self.assertEqual(event["detail"], "blocked by code module denylist")


class AskModeReadOnlyCallsTest(unittest.IsolatedAsyncioTestCase):
    def _ask_spec(self, responses: list[LLMResponse]) -> AgentRunSpec:
        return _plan_spec(
            responses,
            plan_read_only=False,
            composer_mode="ask",
            read_only_tools=True,
            denied_tools=denied_tools_for_composer_mode("ask"),
        )

    async def test_git_and_board_reads_answer_in_ask(self) -> None:
        """Ask used to lose git and board entirely; their query actions are
        reads and the /ask brief promises them."""
        result = await AgentRunner().run(self._ask_spec([
            _call("git", "1", action="status"),
            _call("board", "2", action="list"),
            _DONE,
        ]))
        (git_event,) = _events(result, "git")
        (board_event,) = _events(result, "board")
        self.assertEqual(git_event["status"], "ok")
        self.assertEqual(board_event["status"], "ok")

    async def test_git_commit_still_refuses_in_ask(self) -> None:
        result = await AgentRunner().run(self._ask_spec([
            _call("git", "1", action="commit", message="wip"),
            _DONE,
        ]))
        (event,) = _events(result, "git")
        self.assertEqual(event["detail"], "blocked by read-only turn")

    async def test_ask_to_agent_handoff_unlocks_tools_in_the_same_turn(self) -> None:
        """"mode agent" in Ask: the tool says "set to Agent", the editor flips
        to Agent, so exec / writes must run now. Keeping the turn read-only
        showed "not allowed in Ask mode" errors under an Agent badge."""
        result = await AgentRunner().run(self._ask_spec([
            _call("set_composer_mode", "1", mode="agent"),
            _call("git", "2", action="commit", message="wip"),
            _call("write_file", "3", path="a.py", content="x"),
            _DONE,
        ]))
        (commit,) = _events(result, "git")
        (write,) = _events(result, "write_file")
        self.assertEqual(commit["status"], "ok")
        self.assertEqual(write["status"], "ok")

    async def test_switching_back_to_ask_tightens_again(self) -> None:
        result = await AgentRunner().run(self._ask_spec([
            _call("set_composer_mode", "1", mode="agent"),
            _call("set_composer_mode", "2", mode="ask"),
            _call("git", "3", action="commit", message="wip"),
            _DONE,
        ]))
        (event,) = _events(result, "git")
        self.assertEqual(event["detail"], "blocked by read-only turn")

    async def test_a_runtime_check_is_a_read_in_ask(self) -> None:
        """my(action=check) only inspects state; Ask refused it as a write."""
        from navin.agent.tools.self import MyTool

        tool = MyTool.__new__(MyTool)
        self.assertTrue(tool.call_read_only({"action": "check", "key": "request.channel"}))
        self.assertTrue(tool.call_read_only({"action": "inspect"}))
        self.assertFalse(tool.call_read_only({"action": "set", "key": "max_iterations"}))
        self.assertFalse(tool.call_read_only({}))


class ModeDenylistMessageTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_mode_denial_is_not_blamed_on_the_code_module(self) -> None:
        """scrape refused by Review mode must say so, not accuse the module."""
        result = await AgentRunner().run(_plan_spec(
            [
                _call("scrape", "1", url="https://example.com"),
                _DONE,
            ],
            plan_read_only=False,
            composer_mode="review",
            denied_tools=denied_tools_for_composer_mode("review"),
        ))
        (event,) = _events(result, "scrape")
        self.assertEqual(event["detail"], "blocked by mode denylist")
        refusals = [
            m for m in result.messages
            if m.get("role") == "tool" and "review mode" in str(m.get("content", ""))
        ]
        self.assertTrue(refusals, "the refusal must name the review mode")


class RealToolsJudgeCallsPerActionTest(unittest.TestCase):
    """The shipped git and board tools classify their own actions."""

    def test_git_query_actions_are_read_only_calls(self) -> None:
        tool = GitTool.__new__(GitTool)
        for action in ("status", "diff", "log", "show", "blame", "branches"):
            self.assertTrue(tool.call_read_only({"action": action}), action)
        for action in ("commit", "push", "reset", "switch", "merge", "stash"):
            self.assertFalse(tool.call_read_only({"action": action}), action)
        self.assertFalse(tool.call_read_only(None))

    def test_board_reads_are_read_only_calls(self) -> None:
        tool = BoardTool.__new__(BoardTool)
        for action in ("list", "next", "plan", "get", "ledger_get"):
            self.assertTrue(tool.call_read_only({"action": action}), action)
        for action in ("create", "update", "move", "claim", "ledger_init", "log"):
            self.assertFalse(tool.call_read_only({"action": action}), action)
        self.assertFalse(tool.call_read_only(None))


class LoopPolicyWiringTest(unittest.TestCase):
    """The loop derives the runner policy from turn metadata."""

    def test_composer_mode_is_normalized_from_metadata(self) -> None:
        from navin.agent.loop import AgentLoop

        self.assertEqual(
            AgentLoop._composer_mode(None, {"composer_mode": " Plan "}), "plan"
        )
        self.assertIsNone(AgentLoop._composer_mode(None, {}))
        self.assertIsNone(AgentLoop._composer_mode(None, None))

    def test_locked_denials_carry_the_code_module_decisions(self) -> None:
        from navin.agent.loop import AgentLoop
        from navin.command.modules import (
            CODE_DENIED_TOOLS,
            PRODUCT_MODULE_METADATA_KEY,
        )

        locked = AgentLoop._locked_denied_tools(
            None, {PRODUCT_MODULE_METADATA_KEY: "code"}
        )
        for name in CODE_DENIED_TOOLS:
            self.assertIn(name, locked)
        # No module at all still pins the off-module specialists.
        bare = AgentLoop._locked_denied_tools(None, {})
        self.assertIn("visual_qa", bare)
        self.assertIn("seo", bare)
        career = AgentLoop._locked_denied_tools(
            None, {PRODUCT_MODULE_METADATA_KEY: "career"}
        )
        self.assertIn("tenders", career)
        self.assertIn("trading", career)
        self.assertNotIn("career", career)
        self.assertNotIn("scrape", career)


if __name__ == "__main__":
    unittest.main()
