"""Phase 2 gates: Code denylist, apply_patch only, verify-red retry."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from navin.agent.hook import AgentHook
from navin.agent.runner import AgentRunner, AgentRunSpec, _last_verify_failed
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.command.builtin import _workflow_handler
from navin.command.modules import (
    APPLY_PATCH_ONLY_METADATA_KEY,
    CODE_DENIED_TOOLS,
    CODE_VERIFY_FAIL_NUDGE_LIMIT,
    REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY,
    VERIFY_FAIL_NUDGE_LIMIT_METADATA_KEY,
)
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.runtime import VERIFY_FAILED_CONTINUE_PROMPT


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


class _WriteFileTool(Tool):
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "write"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "wrote"


class _ScrapeTool(Tool):
    @property
    def name(self) -> str:
        return "scrape"

    @property
    def description(self) -> str:
        return "scrape"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "scraped"


class _ApplyPatchTool(Tool):
    @property
    def name(self) -> str:
        return "apply_patch"

    @property
    def description(self) -> str:
        return "patch"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "patched"


class _EditFileTool(Tool):
    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "edit"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "edited"


class _FailingVerifyTool(Tool):
    @property
    def name(self) -> str:
        return "verify"

    @property
    def description(self) -> str:
        return "verify"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "FAIL - lint errors must be fixed"


class _EventuallyPassingVerifyTool(Tool):
    """Red the first ``fail_times`` runs, then green - a hard-bug simulation."""

    def __init__(self, fail_times: int) -> None:
        self._fail_times = fail_times
        self.calls = 0

    @property
    def name(self) -> str:
        return "verify"

    @property
    def description(self) -> str:
        return "verify"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        self.calls += 1
        if self.calls <= self._fail_times:
            return f"FAIL - tests still red (run {self.calls})"
        return "PASS - all tests green"


class ForgeMetadataTest(unittest.TestCase):
    def test_forge_keeps_verify_and_full_fs_writers(self) -> None:
        msg = SimpleNamespace(content="", metadata={}, channel="cli", chat_id="t")
        ctx = SimpleNamespace(
            args="fix bug",
            raw="/forge fix bug",
            msg=msg,
            loop=None,
        )
        asyncio.run(_workflow_handler("/forge")(ctx))  # type: ignore[arg-type]
        self.assertFalse(msg.metadata.get(APPLY_PATCH_ONLY_METADATA_KEY))
        self.assertTrue(msg.metadata.get(REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY))
        # P2-1: build workflows raise the red-verify retry cap.
        self.assertEqual(
            msg.metadata.get(VERIFY_FAIL_NUDGE_LIMIT_METADATA_KEY),
            CODE_VERIFY_FAIL_NUDGE_LIMIT,
        )
        self.assertIn("WORKSPACE FS", msg.content)

    def test_debug_review_security_montage_keep_full_fs_writers(self) -> None:
        cases = (
            ("/debug", "fix null pointer crash in login"),
            ("/inspect", "review auth module for bugs"),
            ("/fortify", "security audit of the API"),
            ("/montage", "build product demo montage for the app"),
            ("/forge", "continue architecture map audit tasks"),
        )
        for command, args in cases:
            with self.subTest(command=command):
                msg = SimpleNamespace(content="", metadata={}, channel="cli", chat_id="t")
                ctx = SimpleNamespace(
                    args=args,
                    raw=f"{command} {args}",
                    msg=msg,
                    loop=None,
                )
                asyncio.run(_workflow_handler(command)(ctx))  # type: ignore[arg-type]
                self.assertFalse(
                    msg.metadata.get(APPLY_PATCH_ONLY_METADATA_KEY),
                    f"{command} must not arm apply_patch_only",
                )
                self.assertIn("WORKSPACE FS", msg.content)


class CodeDeniedToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_blocked_in_code_module(self) -> None:
        provider = _FakeProvider(
            [
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(id="1", name="scrape", arguments={"url": "x"})
                    ],
                ),
                LLMResponse(content="ok", finish_reason="stop"),
            ]
        )
        tools = ToolRegistry()
        tools.register(_ScrapeTool())
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "scrape docs"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=4,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                denied_tools=CODE_DENIED_TOOLS,
            )
        )
        self.assertNotIn("scrape", result.tools_used)
        blocked = [
            e for e in result.tool_events if e.get("detail") == "blocked by code module denylist"
        ]
        self.assertEqual(len(blocked), 1)


class ApplyPatchOnlyTest(unittest.IsolatedAsyncioTestCase):
    async def test_write_file_allowed_on_existing_path_even_with_legacy_flag(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            existing = workspace / "audit" / "01-architecture-map.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("# old\n", encoding="utf-8")
            provider = _FakeProvider(
                [
                    LLMResponse(
                        content="",
                        finish_reason="tool_calls",
                        tool_calls=[
                            ToolCallRequest(
                                id="1",
                                name="write_file",
                                arguments={
                                    "path": "audit/01-architecture-map.md",
                                    "content": "# map\n",
                                },
                            )
                        ],
                    ),
                    LLMResponse(content="ok", finish_reason="stop"),
                ]
            )
            tools = ToolRegistry()
            tools.register(_WriteFileTool())
            tools.register(_ApplyPatchTool())
            result = await AgentRunner().run(
                AgentRunSpec(
                    initial_messages=[{"role": "user", "content": "rewrite audit map"}],
                    tools=tools,
                    runtime=_runtime(provider),
                    max_iterations=4,
                    max_tool_result_chars=2000,
                    hook=AgentHook(),
                    apply_patch_only=True,
                    workspace=workspace,
                )
            )
            self.assertIn("write_file", result.tools_used)
            blocked = [
                e
                for e in result.tool_events
                if e.get("detail") == "blocked by apply_patch_only"
            ]
            self.assertEqual(len(blocked), 0)

    async def test_edit_file_allowed_when_apply_patch_only(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "a.py").write_text("old\n", encoding="utf-8")
            provider = _FakeProvider(
                [
                    LLMResponse(
                        content="",
                        finish_reason="tool_calls",
                        tool_calls=[
                            ToolCallRequest(
                                id="1",
                                name="edit_file",
                                arguments={
                                    "path": "a.py",
                                    "old_text": "old",
                                    "new_text": "new",
                                },
                            )
                        ],
                    ),
                    LLMResponse(content="ok", finish_reason="stop"),
                ]
            )
            tools = ToolRegistry()
            tools.register(_EditFileTool())
            tools.register(_ApplyPatchTool())
            result = await AgentRunner().run(
                AgentRunSpec(
                    initial_messages=[{"role": "user", "content": "edit"}],
                    tools=tools,
                    runtime=_runtime(provider),
                    max_iterations=4,
                    max_tool_result_chars=2000,
                    hook=AgentHook(),
                    apply_patch_only=True,
                    workspace=workspace,
                )
            )
            self.assertIn("edit_file", result.tools_used)
            blocked = [
                e
                for e in result.tool_events
                if e.get("detail") == "blocked by apply_patch_only"
            ]
            self.assertEqual(len(blocked), 0)


class _ExecTool(Tool):
    """Fake shell: the command decides the exit code the way a real run would."""

    def __init__(self, exit_codes: dict[str, int] | None = None) -> None:
        self._exit_codes = exit_codes or {}
        self.commands: list[str] = []

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "exec"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"command": {"type": "string"}}}

    async def execute(self, command: str = "", **kwargs: Any) -> str:
        self.commands.append(command)
        code = self._exit_codes.get(command, 0)
        body = "1 passed" if code == 0 else "1 failed"
        return f"{body}\n\nExit code: {code}"


def _edit_then_exec_then_done(command: str) -> list[LLMResponse]:
    return [
        LLMResponse(
            content="",
            finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id="1", name="apply_patch", arguments={"path": "a.py"})],
        ),
        LLMResponse(
            content="",
            finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id="2", name="exec", arguments={"command": command})],
        ),
        LLMResponse(content="Done.", finish_reason="stop"),
    ]


class ExecTestRunCountsAsVerifyTest(unittest.IsolatedAsyncioTestCase):
    """``pytest`` through ``exec`` is the evidence ``verify`` would produce.

    Nudging for ``verify`` on top cost two more model calls on every small
    change (one to read the nudge, one to run a tool whose answer was already
    in the transcript).
    """

    async def _run(self, responses: list[LLMResponse], exec_tool: _ExecTool) -> tuple[int, list[dict[str, Any]]]:
        provider = _FakeProvider(responses)
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(exec_tool)
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "fix"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=8,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                requires_verify_before_done=True,
            )
        )
        return provider.calls, result.messages

    @staticmethod
    def _verify_nudges(messages: list[dict[str, Any]]) -> int:
        from navin.utils.runtime import VERIFY_BEFORE_DONE_CONTINUE_PROMPT

        return sum(
            1
            for m in messages
            if m.get("role") == "user" and VERIFY_BEFORE_DONE_CONTINUE_PROMPT in str(m.get("content"))
        )

    async def test_a_green_pytest_through_exec_is_enough(self) -> None:
        calls, messages = await self._run(
            _edit_then_exec_then_done("python -m pytest tests -q"), _ExecTool(),
        )
        self.assertEqual(self._verify_nudges(messages), 0)
        self.assertEqual(calls, 3)

    async def test_a_red_pytest_still_gets_the_nudge(self) -> None:
        command = "pytest -q"
        calls, messages = await self._run(
            _edit_then_exec_then_done(command), _ExecTool({command: 1}),
        )
        self.assertEqual(self._verify_nudges(messages), 1)
        self.assertGreater(calls, 3)

    async def test_a_command_that_is_not_a_test_run_still_gets_the_nudge(self) -> None:
        calls, messages = await self._run(
            _edit_then_exec_then_done("npm run build"), _ExecTool(),
        )
        self.assertEqual(self._verify_nudges(messages), 1)

    async def test_an_edit_after_the_green_run_reopens_the_question(self) -> None:
        responses = [
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id="1", name="exec", arguments={"command": "pytest -q"})],
            ),
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id="2", name="apply_patch", arguments={"path": "a.py"})],
            ),
            LLMResponse(content="Done.", finish_reason="stop"),
        ]
        _calls, messages = await self._run(responses, _ExecTool())
        self.assertEqual(self._verify_nudges(messages), 1)


class VerifyFailedNudgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_red_verify_nudges_fix_retry(self) -> None:
        provider = _FakeProvider(
            [
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="1", name="apply_patch", arguments={"path": "a.py"}
                        )
                    ],
                ),
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="2", name="verify", arguments={"action": "check"}
                        )
                    ],
                ),
                LLMResponse(content="Done anyway.", finish_reason="stop"),
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="3", name="verify", arguments={"action": "fix"}
                        )
                    ],
                ),
                LLMResponse(content="Fixed.", finish_reason="stop"),
            ]
        )
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(_FailingVerifyTool())
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "fix"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=8,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                requires_verify_before_done=True,
            )
        )
        nudges = [
            m
            for m in result.messages
            if m.get("role") == "user"
            and VERIFY_FAILED_CONTINUE_PROMPT in str(m.get("content"))
        ]
        self.assertGreaterEqual(len(nudges), 1)

    def test_last_verify_failed_helper(self) -> None:
        self.assertTrue(
            _last_verify_failed(
                [{"name": "verify", "status": "ok", "detail": "FAIL - lint errors"}]
            )
        )
        self.assertFalse(
            _last_verify_failed(
                [{"name": "verify", "status": "ok", "detail": "PASS - clean"}]
            )
        )

    async def test_raised_limit_keeps_nudging_until_verify_passes(self) -> None:
        """The runner honors a raised verify-fail cap (hard bugs need cycles).

        The product /forge default is 2 so a stuck loop cannot burn an hour.
        This test pins 5 on the spec to prove the runner still retries until
        PASS when a workflow asks for more.
        """
        # Model script: edit, then 3 x (verify red -> "Done." -> nudge), then
        # the 4th verify runs green and the real final message ends the turn.
        responses = [
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCallRequest(
                        id="edit", name="apply_patch", arguments={"path": "a.py"}
                    )
                ],
            ),
        ]
        for index in range(3):
            responses.append(
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id=f"verify-{index}",
                            name="verify",
                            arguments={"action": "check"},
                        )
                    ],
                )
            )
            responses.append(
                LLMResponse(content="Done anyway.", finish_reason="stop")
            )
        responses.append(
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCallRequest(
                        id="verify-final",
                        name="verify",
                        arguments={"action": "check"},
                    )
                ],
            )
        )
        responses.append(LLMResponse(content="Fixed for real.", finish_reason="stop"))

        provider = _FakeProvider(responses)
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        verify_tool = _EventuallyPassingVerifyTool(fail_times=3)
        tools.register(verify_tool)
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "fix the hard bug"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=20,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                requires_verify_before_done=True,
                verify_fail_nudge_limit=5,
            )
        )
        nudges = [
            m
            for m in result.messages
            if m.get("role") == "user"
            and VERIFY_FAILED_CONTINUE_PROMPT in str(m.get("content"))
        ]
        # 3 red verifies -> 3 nudges (the historical cap of 2 stopped at 2).
        self.assertEqual(len(nudges), 3)
        self.assertEqual(verify_tool.calls, 4)
        self.assertEqual(result.final_content, "Fixed for real.")
        self.assertEqual(result.stop_reason, "completed")


if __name__ == "__main__":
    unittest.main()
