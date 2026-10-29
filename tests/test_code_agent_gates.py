# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 2 gates: Code denylist, apply_patch only, verify-red retry."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from navin.agent.hook import AgentHook
from navin.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec, _last_verify_failed
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.command.builtin import _workflow_handler
from navin.command.modules import (
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
        self.assertNotIn("apply_patch_only", msg.metadata)
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
                self.assertNotIn("apply_patch_only", msg.metadata, command)
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


class FsWritersAllowedTest(unittest.IsolatedAsyncioTestCase):
    async def test_write_file_allowed_on_existing_path(self) -> None:
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

    async def test_edit_file_allowed(self) -> None:
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

    async def _run(self, responses: list[LLMResponse], exec_tool: _ExecTool) -> tuple[int, AgentRunResult]:
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
        return provider.calls, result

    @staticmethod
    def _verify_nudges(messages: list[dict[str, Any]]) -> int:
        from navin.utils.runtime import VERIFY_BEFORE_DONE_CONTINUE_PROMPT

        return sum(
            1
            for m in messages
            if m.get("role") == "user" and VERIFY_BEFORE_DONE_CONTINUE_PROMPT in str(m.get("content"))
        )

    async def test_a_green_pytest_through_exec_is_enough(self) -> None:
        calls, result = await self._run(
            _edit_then_exec_then_done("python -m pytest tests -q"), _ExecTool(),
        )
        self.assertEqual(self._verify_nudges(result.messages), 0)
        self.assertEqual(calls, 3)
        self.assertEqual(result.stop_reason, "completed")

    async def test_a_red_pytest_still_gets_the_nudge(self) -> None:
        command = "pytest -q"
        calls, result = await self._run(
            _edit_then_exec_then_done(command), _ExecTool({command: 1}),
        )
        self.assertTrue(any(
            VERIFY_FAILED_CONTINUE_PROMPT in str(message.get("content"))
            for message in result.messages if message.get("role") == "user"
        ))
        self.assertEqual(result.stop_reason, "validation_failed")
        self.assertIn("task is not validated", result.final_content)
        self.assertGreater(calls, 3)

    async def test_a_command_that_is_not_a_test_run_still_gets_the_nudge(self) -> None:
        calls, result = await self._run(
            _edit_then_exec_then_done("npm run build"), _ExecTool(),
        )
        self.assertEqual(self._verify_nudges(result.messages), 2)
        self.assertEqual(result.stop_reason, "validation_failed")

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
        _calls, result = await self._run(responses, _ExecTool())
        self.assertEqual(self._verify_nudges(result.messages), 2)
        self.assertEqual(result.stop_reason, "validation_failed")


class _ReadFileTool(Tool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "read_file"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        return "1| x = 1"


def _read(call_id: str, path: str) -> LLMResponse:
    return LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(id=call_id, name="read_file", arguments={"path": path})],
    )


class WrapUpAfterGreenChecksTest(unittest.IsolatedAsyncioTestCase):
    """Validated work followed by a reading tour is told to wrap up."""

    async def _run(self, responses: list[LLMResponse]) -> tuple[_FakeProvider, AgentRunResult]:
        provider = _FakeProvider(responses)
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(_ExecTool())
        tools.register(_ReadFileTool())
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "fix"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=20,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                requires_verify_before_done=True,
            )
        )
        return provider, result

    @staticmethod
    def _wrap_ups(result: AgentRunResult) -> int:
        return sum(
            1 for m in result.messages
            if m.get("role") == "user" and str(m.get("content", "")).startswith("Wrap-up check")
        )

    async def test_reading_on_after_green_checks_ends_the_turn(self) -> None:
        edit_and_test = _edit_then_exec_then_done("python -m pytest tests -q")[:2]
        reads = [_read(str(i), f"f{i}.py") for i in range(10)]
        final = LLMResponse(content="Tout est fait, tests verts.", finish_reason="stop")
        provider, result = await self._run([*edit_and_test, *reads, final])
        self.assertEqual(self._wrap_ups(result), 1)
        self.assertEqual(result.stop_reason, "no_progress")
        # edit, test, 3 reads, the check, 2 ignored reads, then the forced answer.
        self.assertEqual(provider.calls, 8)

    async def test_a_short_review_read_is_left_alone(self) -> None:
        edit_and_test = _edit_then_exec_then_done("python -m pytest tests -q")[:2]
        final = LLMResponse(content="Fait.", finish_reason="stop")
        provider, result = await self._run([*edit_and_test, _read("r1", "a.py"), _read("r2", "b.py"), final])
        self.assertEqual(self._wrap_ups(result), 0)
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(provider.calls, 5)

    async def test_reads_before_any_validated_edit_are_not_wrap_up(self) -> None:
        reads = [_read(str(i), f"f{i}.py") for i in range(5)]
        edit_and_test = _edit_then_exec_then_done("python -m pytest tests -q")
        provider, result = await self._run([*reads, *edit_and_test])
        self.assertEqual(self._wrap_ups(result), 0)
        self.assertEqual(result.stop_reason, "completed")


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
        # Model script: edit, then 3 x (verify red -> "Done." -> nudge -> fix),
        # then the 4th verify runs green and the real final message ends the turn.
        # Each verify follows an edit: an identical check with nothing changed
        # since is refused (RerunCheckGuardTest), so it would not run at all.
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
                            id=f"fix-{index}", name="apply_patch", arguments={"path": "a.py"}
                        )
                    ],
                )
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


class UnwiredScreenNudgeTest(unittest.IsolatedAsyncioTestCase):
    """A new screen nothing imports gets one wiring reminder, never a loop."""

    _MARKER = "Integration check before the final answer"

    async def _run(self, app_source: str, *, scope: str = "main") -> tuple[int, AgentRunResult]:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {
                "web/package.json": '{"dependencies": {"react": "18"}}',
                "web/src/main.tsx": "import App from './App'\n",
                "web/src/App.tsx": app_source,
                "web/src/pages/Dashboard.tsx": "export default function Dashboard() {}\n",
            }
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text, encoding="utf-8")
            provider = _FakeProvider([
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[ToolCallRequest(
                        id="1", name="apply_patch", arguments={"path": "web/src/pages/Dashboard.tsx"},
                    )],
                ),
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[ToolCallRequest(
                        id="2", name="exec", arguments={"command": "python -m pytest tests -q"},
                    )],
                ),
                LLMResponse(content="Done.", finish_reason="stop"),
            ])
            tools = ToolRegistry()
            tools.register(_ApplyPatchTool())
            tools.register(_ExecTool())
            result = await AgentRunner().run(
                AgentRunSpec(
                    initial_messages=[{"role": "user", "content": "add a dashboard"}],
                    tools=tools,
                    runtime=_runtime(provider),
                    max_iterations=8,
                    max_tool_result_chars=2000,
                    hook=AgentHook(),
                    requires_verify_before_done=True,
                    workspace=root,
                    user_hook_scope=scope,
                )
            )
        return provider.calls, result

    def _nudges(self, result: AgentRunResult) -> list[str]:
        return [
            str(m.get("content"))
            for m in result.messages
            if m.get("role") == "user" and self._MARKER in str(m.get("content"))
        ]

    async def test_unwired_screen_is_named_once_then_the_turn_ends(self) -> None:
        calls, result = await self._run("export default function App() {}\n")

        nudges = self._nudges(result)
        self.assertEqual(len(nudges), 1)
        self.assertIn("- web/src/pages/Dashboard.tsx", nudges[0])
        self.assertEqual(calls, 4)
        self.assertEqual(result.final_content, "Done.")

    async def test_routed_screen_finishes_without_a_reminder(self) -> None:
        calls, result = await self._run("import Dashboard from './pages/Dashboard'\n")

        self.assertEqual(self._nudges(result), [])
        self.assertEqual(calls, 3)

    async def test_subagent_slice_is_named_once_with_the_lead_handoff(self) -> None:
        # A subagent may write the screen while the lead wires it in: it is
        # reminded once, and may leave the wiring to the lead in its report
        # instead of editing a router another teammate owns.
        calls, result = await self._run("export default function App() {}\n", scope="subagent")

        nudges = self._nudges(result)
        self.assertEqual(len(nudges), 1)
        self.assertIn("- web/src/pages/Dashboard.tsx", nudges[0])
        self.assertIn("left for the lead to wire", nudges[0])
        self.assertEqual(calls, 4)

    async def test_lead_reminder_has_no_subagent_handoff(self) -> None:
        _calls, result = await self._run("export default function App() {}\n")

        self.assertNotIn("left for the lead to wire", self._nudges(result)[0])


class RerunCheckGuardTest(unittest.TestCase):
    """The same check is not re-run when nothing could have changed its result."""

    def test_identical_check_with_no_edit_since_is_refused(self) -> None:
        from navin.utils.runtime import repeated_rerun_check_error as guard

        seen: dict[str, int] = {}
        tests = {"command": "python -m pytest tests/test_a.py -q"}
        self.assertIsNone(guard("exec", tests, seen))
        self.assertIsNone(guard("read_file", {"path": "a.py"}, seen))
        self.assertIsNone(guard("git", {"action": "commit", "message": "x"}, seen))
        refused = guard("exec", tests, seen)
        self.assertIsNotNone(refused)
        self.assertIn("already ran", str(refused))
        # A different check in the same epoch still runs once.
        self.assertIsNone(guard("verify", {"action": "check"}, seen))
        self.assertIsNotNone(guard("verify", {"action": "check"}, seen))

    def test_any_possible_edit_between_runs_allows_the_check_again(self) -> None:
        from navin.utils.runtime import repeated_rerun_check_error as guard

        for edit_tool, edit_args in (
            ("apply_patch", {"edits": []}),
            ("exec", {"command": "npm install"}),
            ("project", {"action": "install"}),
            ("lint", {"action": "fix", "path": "a.py"}),
            ("some_new_tool", {}),
        ):
            seen: dict[str, int] = {}
            self.assertIsNone(guard("verify", {"action": "check"}, seen))
            self.assertIsNone(guard(edit_tool, edit_args, seen))
            self.assertIsNone(guard("verify", {"action": "check"}, seen), edit_tool)

    def test_different_checks_and_background_runs_are_never_refused(self) -> None:
        from navin.utils.runtime import repeated_rerun_check_error as guard

        seen: dict[str, int] = {}
        self.assertIsNone(guard("test_run", {"target": "tests/a.py"}, seen))
        self.assertIsNone(guard("test_run", {"target": "tests/b.py"}, seen))
        self.assertIsNone(guard("exec", {"command": "npm run build"}, seen))
        self.assertIsNone(guard("test_run", {"action": "detect"}, seen))
        self.assertIsNone(guard("test_run", {"action": "detect"}, seen))
        background = {"command": "pytest", "background": True}
        self.assertIsNone(guard("exec", background, seen))
        self.assertIsNone(guard("exec", background, seen))
        self.assertIsNone(guard("exec", {"command": "ls"}, seen))
        self.assertIsNone(guard("exec", {"command": "ls"}, seen))

    def test_runner_answers_the_repeat_from_the_earlier_run(self) -> None:
        import asyncio

        provider = _FakeProvider([
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id="1", name="apply_patch", arguments={"path": "a.py"})],
            ),
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(
                    id="2", name="exec", arguments={"command": "python -m pytest tests -q"},
                )],
            ),
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(
                    id="3", name="exec", arguments={"command": "python -m pytest tests -q"},
                )],
            ),
            LLMResponse(content="Done.", finish_reason="stop"),
        ])
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(_ExecTool())
        result = asyncio.run(AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "fix it"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=8,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                requires_verify_before_done=True,
            )
        ))

        tool_outputs = [str(m.get("content")) for m in result.messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_outputs), 3)
        self.assertNotIn("already ran", tool_outputs[1])
        self.assertIn("already ran", tool_outputs[2])
        # The refusal is not a red check: the green run stays recorded, so no
        # verify-failed nudge sends the model back to the same command.
        self.assertFalse([
            m for m in result.messages
            if m.get("role") == "user" and VERIFY_FAILED_CONTINUE_PROMPT in str(m.get("content"))
        ])
        self.assertEqual(provider.calls, 4)
        self.assertEqual(result.final_content, "Done.")


class FrontendGapNudgeTest(unittest.IsolatedAsyncioTestCase):
    """A dashboard request answered with backend code only is sent back once."""

    _MARKER = "and this turn changed backend files only"

    async def _run(
        self,
        ui_terms: tuple[str, ...],
        *,
        requires_verify: bool = True,
        validate_code: bool = False,
        scope: str = "main",
    ) -> tuple[int, AgentRunResult]:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "web").mkdir()
            (root / "web/package.json").write_text(
                '{"dependencies": {"react": "18"}}', encoding="utf-8",
            )
            provider = _FakeProvider([
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[ToolCallRequest(
                        id="1", name="apply_patch", arguments={"path": "api/stats.py"},
                    )],
                ),
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[ToolCallRequest(
                        id="2", name="exec", arguments={"command": "python -m pytest tests -q"},
                    )],
                ),
                LLMResponse(content="Done.", finish_reason="stop"),
                LLMResponse(content="Done, screen wired.", finish_reason="stop"),
            ])
            tools = ToolRegistry()
            tools.register(_ApplyPatchTool())
            tools.register(_ExecTool())
            result = await AgentRunner().run(
                AgentRunSpec(
                    initial_messages=[{"role": "user", "content": "add a stats dashboard"}],
                    tools=tools,
                    runtime=_runtime(provider),
                    max_iterations=8,
                    max_tool_result_chars=2000,
                    hook=AgentHook(),
                    requires_verify_before_done=requires_verify,
                    validate_code_changes=validate_code,
                    user_hook_scope=scope,
                    workspace=root,
                    ui_terms=ui_terms,
                )
            )
        return provider.calls, result

    def _assert_sent_back_once(self, calls: int, result: AgentRunResult) -> None:
        nudges = [
            str(m.get("content"))
            for m in result.messages
            if m.get("role") == "user" and self._MARKER in str(m.get("content"))
        ]
        self.assertEqual(len(nudges), 1)
        self.assertEqual(calls, 4)
        self.assertEqual(result.final_content, "Done, screen wired.")

    async def test_plain_dev_turn_without_a_workflow_command_is_sent_back_once(self) -> None:
        # A CLI or desktop message with no /forge still validates code changes.
        calls, result = await self._run(
            ("dashboard",), requires_verify=False, validate_code=True,
        )

        self._assert_sent_back_once(calls, result)

    async def test_subagent_turn_is_sent_back_once(self) -> None:
        calls, result = await self._run(("dashboard",), scope="subagent")

        self._assert_sent_back_once(calls, result)

    async def test_turn_that_validates_nothing_is_not_checked(self) -> None:
        calls, result = await self._run(
            ("dashboard",), requires_verify=False, validate_code=False,
        )

        self.assertEqual(calls, 3)
        self.assertEqual(result.final_content, "Done.")

    async def test_backend_only_turn_for_a_dashboard_is_sent_back_once(self) -> None:
        calls, result = await self._run(("dashboard",))

        nudges = [
            str(m.get("content"))
            for m in result.messages
            if m.get("role") == "user" and self._MARKER in str(m.get("content"))
        ]
        self.assertEqual(len(nudges), 1)
        self.assertIn("(dashboard)", nudges[0])
        self.assertIn("- api/stats.py", nudges[0])
        self.assertIn("menu or navigation entry", nudges[0])
        self.assertEqual(calls, 4)
        self.assertEqual(result.final_content, "Done, screen wired.")

    async def test_request_without_ui_words_ends_on_the_backend_answer(self) -> None:
        calls, result = await self._run(())

        self.assertEqual(calls, 3)
        self.assertEqual(result.final_content, "Done.")


class RemainingWorkNudgeTest(unittest.TestCase):
    """A multi-task request keeps going task after task, but never in a loop."""

    def test_unfinished_work_labels_are_recognised_and_done_answers_are_not(self) -> None:
        from navin.utils.runtime import lists_remaining_work

        for text in (
            "Task 1 done. Still to do: task 2 and task 3.",
            "Task 2 done. Remaining: task 3.",
            "Tache 2 faite. Reste : la tache 3.",
            "Restantes : brancher l'ecran.",
            "Ce qui reste : la recherche.",
            # Real endings of a 16-step plan stopped after 2 steps (2026-10-22).
            "Pas commencé : D1, F3 à F5 et G1.",
            "Dis « continue » et j'attaque D1.",
            "Je fais cette partie dans un tour dédié.",
            "Not started: F3 and G1. Say continue and I will do F3.",
            # Report headings that hand work back (2026-10-23).
            "Tout passe.\n\n**Non traité**\n- les lectures à l'aveugle",
            "Tests verts.\n\n**Pas corrigé, par choix de priorité**\n- les motifs répétés",
            "Prochaines étapes :\n1. brancher l'écran",
            "Build OK.\n\nNext steps:\n- wire the screen",
        ):
            self.assertTrue(lists_remaining_work(text), text)
        for text in (
            "All three tasks are done.",
            "Les trois taches sont faites et testees.",
            "The remaining tests pass.",
            "Il restera a pousser avec git push.",
            "Le serveur a commencé à répondre, tout est en place.",
            # A remark in a sentence is not a heading of work left.
            "Les cas non traités par l'API renvoient 422.",
            "Le bug n'est pas corrigé en v1, il l'est dans ce commit.",
            "Pas de régression sur les suites ciblées.",
            # A remark quoting the phrase it is about.
            "Le rapport ne contient plus de « reste à faire ».",
            'The fallback prompt still says "what remains" on purpose.',
            "Les titres `Next steps` sont reconnus.",
        ):
            self.assertFalse(lists_remaining_work(text), text)

    def test_report_instructions_ask_for_remarks_not_a_list_of_work_left(self) -> None:
        from pathlib import Path

        import navin
        from navin.command.builtin import _STAY_ON_ASK_CLAUSE, _TRACKED_RUN_CLAUSE
        from navin.utils.runtime import (
            REMAINING_WORK_CONTINUE_PROMPT,
            STRICT_FINALIZATION_RETRY_PROMPT,
        )

        identity = (Path(navin.__file__).parent / "templates" / "agent" / "identity.md").read_text(
            encoding="utf-8",
        )
        texts = {
            "identity": identity,
            "tracked run": _TRACKED_RUN_CLAUSE,
            "stay on ask": _STAY_ON_ASK_CLAUSE,
            "remaining work reminder": REMAINING_WORK_CONTINUE_PROMPT,
            "blank reply retry": STRICT_FINALIZATION_RETRY_PROMPT,
        }
        for name, text in texts.items():
            self.assertNotIn("what remains", text, name)
            self.assertNotIn("what is left", text, name)
            self.assertIn("remarks", text, name)
        self.assertIn("never hand back with work of the request still to do", identity)
        self.assertIn("never a list of work still to do", _TRACKED_RUN_CLAUSE)

    def test_a_step_that_only_ran_commands_still_earns_a_new_push(self) -> None:
        import asyncio

        from navin.agent.runner import build_remaining_work_continue_message

        def call(call_id: str, name: str, arguments: dict) -> LLMResponse:
            return LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id=call_id, name=name, arguments=arguments)],
            )

        provider = _FakeProvider([
            call("a1", "apply_patch", {"path": "migrations/0002.py"}),
            call("t1", "exec", {"command": "python -m pytest tests -q"}),
            LLMResponse(content="Migration written. Remaining: run it, seed the demo data.", finish_reason="stop"),
            # No file edited, but the step ran a command: that is progress.
            call("m1", "exec", {"command": "python manage.py migrate"}),
            LLMResponse(content="Migration applied. Remaining: seed the demo data.", finish_reason="stop"),
            call("s1", "exec", {"command": "python manage.py seed_demo"}),
            LLMResponse(content="Migration applied, demo data seeded, tests pass.", finish_reason="stop"),
        ])
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(_ExecTool())
        result = asyncio.run(AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "add the migration, run it, seed demo data"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=12,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                requires_verify_before_done=True,
            )
        ))

        push = build_remaining_work_continue_message()["content"]
        pushes = [
            m for m in result.messages if m.get("role") == "user" and push in str(m.get("content"))
        ]
        self.assertEqual(len(pushes), 2)
        self.assertEqual(provider.calls, 7)
        self.assertEqual(result.final_content, "Migration applied, demo data seeded, tests pass.")

    def test_each_task_finished_with_an_edit_earns_a_new_push(self) -> None:
        import asyncio

        from navin.agent.runner import build_remaining_work_continue_message

        def call(call_id: str, name: str, arguments: dict) -> LLMResponse:
            return LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id=call_id, name=name, arguments=arguments)],
            )

        tests = {"command": "python -m pytest tests -q"}
        provider = _FakeProvider([
            call("a1", "apply_patch", {"path": "a.py"}),
            call("t1", "exec", tests),
            LLMResponse(content="Task 1 done. Still to do: task 2 and task 3.", finish_reason="stop"),
            call("a2", "apply_patch", {"path": "b.py"}),
            call("t2", "exec", tests),
            LLMResponse(content="Task 2 done. Still to do: task 3.", finish_reason="stop"),
            # No edit since the last push: one firmer push, then the turn ends.
            LLMResponse(content="Task 3 is still to do.", finish_reason="stop"),
        ])
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(_ExecTool())
        result = asyncio.run(AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "do tasks 1, 2 and 3"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=12,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                requires_verify_before_done=True,
            )
        ))

        push = build_remaining_work_continue_message()["content"]
        pushes = [
            m for m in result.messages if m.get("role") == "user" and push in str(m.get("content"))
        ]
        self.assertEqual(len(pushes), 2)
        self.assertEqual(provider.calls, 8)
        self.assertEqual(result.final_content, "Task 3 is still to do.")


class OpenPlanStepsNudgeTest(unittest.IsolatedAsyncioTestCase):
    """A build turn does not hand back while its plan still has open steps."""

    _MARKER = "The plan of this conversation still has open steps"
    _STEP = {"id": "t-1", "status": "in_progress", "title": "Wire the stats screen"}

    async def _run(
        self, plan: list[list[dict[str, str]]], finals: list[str], *, scope: str = "main",
    ) -> tuple[int, AgentRunResult]:
        import tempfile
        from pathlib import Path
        from unittest import mock

        responses = [
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id="1", name="apply_patch", arguments={"path": "api/stats.py"})],
            ),
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(
                    id="2", name="exec", arguments={"command": "python -m pytest tests -q"},
                )],
            ),
        ] + [LLMResponse(content=text, finish_reason="stop") for text in finals]
        provider = _FakeProvider(responses)
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(_ExecTool())
        answers = iter(plan)
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "navin.board.auto_advance.open_plan_steps",
            side_effect=lambda *_a: next(answers, plan[-1]),
        ):
            result = await AgentRunner().run(
                AgentRunSpec(
                    initial_messages=[{"role": "user", "content": "add stats, then wire the screen"}],
                    tools=tools,
                    runtime=_runtime(provider),
                    max_iterations=10,
                    max_tool_result_chars=2000,
                    hook=AgentHook(),
                    requires_verify_before_done=True,
                    workspace=Path(tmp),
                    session_key="cli:plan",
                    user_hook_scope=scope,
                )
            )
        return provider.calls, result

    def _nudges(self, result: AgentRunResult) -> list[str]:
        return [
            str(m.get("content"))
            for m in result.messages
            if m.get("role") == "user" and self._MARKER in str(m.get("content"))
        ]

    async def test_open_step_sends_the_turn_back_until_the_plan_is_done(self) -> None:
        calls, result = await self._run([[self._STEP], []], ["Done with step 1.", "All steps done."])

        nudges = self._nudges(result)
        self.assertEqual(len(nudges), 1)
        self.assertIn("- t-1 [in_progress] Wire the stats screen", nudges[0])
        self.assertIn("claim_next=true", nudges[0])
        self.assertEqual(calls, 4)
        self.assertEqual(result.final_content, "All steps done.")

    async def test_a_reminder_that_changed_nothing_is_repeated_once_then_ends_the_turn(self) -> None:
        calls, result = await self._run([[self._STEP]], ["Done with step 1.", "Still on it.", "Never read."])

        self.assertEqual(len(self._nudges(result)), 1)
        repeats = [
            m for m in result.messages
            if m.get("role") == "user" and "changed nothing on the plan" in str(m.get("content"))
        ]
        self.assertEqual(len(repeats), 1)
        self.assertEqual(calls, 5)
        self.assertEqual(result.final_content, "Never read.")

    async def test_a_question_to_the_user_or_a_subagent_slice_is_left_alone(self) -> None:
        calls, result = await self._run([[self._STEP]], ["Which chart library should I use?", "x"])
        self.assertEqual(self._nudges(result), [])
        self.assertEqual(calls, 3)

        calls, result = await self._run([[self._STEP]], ["Done with step 1.", "x"], scope="subagent")
        self.assertEqual(self._nudges(result), [])
        self.assertEqual(calls, 3)


class ShortcutNudgeTest(unittest.IsolatedAsyncioTestCase):
    """A green run obtained by skipping or silencing is sent back once."""

    _MARKER = "Shortcut check before the final answer"
    _FINDING = "tests/test_stats.py: test skipped or marked expected-to-fail (`@pytest.mark.skip`)"
    _run = OpenPlanStepsNudgeTest._run
    _nudges = OpenPlanStepsNudgeTest._nudges

    async def _shortcut_run(
        self, findings: list[list[str]], finals: list[str], *, scope: str = "main",
    ) -> tuple[int, AgentRunResult, list[tuple[Any, ...]]]:
        from unittest import mock

        answers = iter(findings)
        seen: list[tuple[Any, ...]] = []

        def fake(root: Any, changed: list[str], **kwargs: Any) -> list[str]:
            seen.append((changed, kwargs.get("since")))
            return next(answers, [])

        with mock.patch("navin.agent.shortcut_check.shortcut_findings", side_effect=fake):
            calls, result = await self._run([[]], finals, scope=scope)
        return calls, result, seen

    async def test_skip_added_by_the_turn_is_named_and_the_turn_goes_on(self) -> None:
        calls, result, seen = await self._shortcut_run(
            [[self._FINDING], [self._FINDING]], ["All green.", "Fixed the cause."],
        )
        nudges = self._nudges(result)
        self.assertEqual(len(nudges), 1)
        self.assertIn(self._FINDING, nudges[0])
        self.assertIn("restore the removed assertions", nudges[0])
        self.assertEqual(seen[0][0], ["api/stats.py"])
        self.assertIsNotNone(seen[0][1])
        # One reminder per turn: a second finding does not loop the turn.
        self.assertEqual(calls, 4)
        self.assertEqual(result.final_content, "Fixed the cause.")

    async def test_clean_diff_or_subagent_slice_ends_normally(self) -> None:
        calls, result, _ = await self._shortcut_run([[]], ["All green.", "x"])
        self.assertEqual(self._nudges(result), [])
        self.assertEqual(calls, 3)

        calls, result, seen = await self._shortcut_run([[self._FINDING]], ["All green.", "x"], scope="subagent")
        self.assertEqual(self._nudges(result), [])
        self.assertEqual(seen, [])
        self.assertEqual(calls, 3)


class AskCoverageNudgeTest(unittest.IsolatedAsyncioTestCase):
    """A final answer that drops a listed ask sends the turn back once."""

    _MARKER = "does not cover these asks"
    _ASKS = ("add the export endpoint", "translate the labels into arabic")

    async def _run(self, finals: list[str]) -> tuple[int, AgentRunResult]:
        responses = [
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id="1", name="apply_patch", arguments={"path": "api/export.py"})],
            ),
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(
                    id="2", name="exec", arguments={"command": "python -m pytest tests -q"},
                )],
            ),
        ] + [LLMResponse(content=text, finish_reason="stop") for text in finals]
        provider = _FakeProvider(responses)
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(_ExecTool())
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "1. add the export\n2. translate"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=10,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                requires_verify_before_done=True,
                explicit_asks=self._ASKS,
            )
        )
        return provider.calls, result

    def _nudges(self, result: AgentRunResult) -> list[str]:
        return [
            str(m.get("content"))
            for m in result.messages
            if m.get("role") == "user" and self._MARKER in str(m.get("content"))
        ]

    async def test_dropped_ask_is_named_and_the_turn_goes_on(self) -> None:
        calls, result = await self._run(["Added the export endpoint.", "Labels translated into Arabic."])

        nudges = self._nudges(result)
        self.assertEqual(len(nudges), 1)
        self.assertIn("1. translate the labels into arabic", nudges[0])
        self.assertNotIn("add the export endpoint", nudges[0])
        self.assertEqual(calls, 4)
        self.assertEqual(result.final_content, "Labels translated into Arabic.")

    async def test_answer_covering_every_ask_ends_the_turn(self) -> None:
        calls, result = await self._run(
            ["Added the export endpoint and translated the labels into Arabic.", "x"],
        )
        self.assertEqual(self._nudges(result), [])
        self.assertEqual(calls, 3)

    async def test_the_reminder_is_sent_only_once(self) -> None:
        calls, result = await self._run(["Added the export endpoint.", "Only the endpoint.", "never"])
        self.assertEqual(len(self._nudges(result)), 1)
        self.assertEqual(calls, 4)
        self.assertEqual(result.final_content, "Only the endpoint.")


if __name__ == "__main__":
    unittest.main()


class GoAheadIsNotAskedAgainTest(unittest.IsolatedAsyncioTestCase):
    """"/forge go" answered "On attaque la suite, ou tu as autre chose en tete ?"."""

    async def _run(self, responses: list[LLMResponse], *, go_ahead: bool) -> tuple[_FakeProvider, AgentRunResult]:
        provider = _FakeProvider(responses)
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(_ExecTool())
        tools.register(_ReadFileTool())
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "go"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=20,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                requires_verify_before_done=True,
                go_ahead=go_ahead,
            )
        )
        return provider, result

    async def test_asking_again_after_go_is_sent_back_into_the_work(self) -> None:
        from navin.utils.runtime import GO_AHEAD_CONTINUE_PROMPT

        ask = LLMResponse(
            content="On attaque le registre de scanners, ou tu as autre chose en tete ?",
            finish_reason="stop",
        )
        provider, result = await self._run(
            [ask, *_edit_then_exec_then_done("python -m pytest tests -q")], go_ahead=True,
        )
        nudges = [m for m in result.messages if m.get("content") == GO_AHEAD_CONTINUE_PROMPT]
        self.assertEqual(len(nudges), 1)
        self.assertEqual(provider.calls, 4)
        self.assertEqual(result.final_content, "Done.")

    async def test_the_go_ahead_reminder_is_sent_once(self) -> None:
        ask = LLMResponse(content="Tu veux vraiment que je commence ?", finish_reason="stop")
        provider, result = await self._run([ask, ask], go_ahead=True)
        self.assertEqual(provider.calls, 2)

    async def test_a_real_request_is_not_treated_as_a_go(self) -> None:
        ask = LLMResponse(content="Quelle base de donnees veux-tu ?", finish_reason="stop")
        provider, _ = await self._run([ask], go_ahead=False)
        self.assertEqual(provider.calls, 1)


class RemainingWorkRepeatTest(unittest.IsolatedAsyncioTestCase):
    """An answer to the reminder that repeats "I start when you say go" gets one firmer push."""

    async def test_text_only_answer_to_the_reminder_is_pushed_once_more(self) -> None:
        from navin.utils.runtime import REMAINING_WORK_REPEAT_PROMPT

        edit, run, _ = _edit_then_exec_then_done("python -m pytest tests -q")
        wait = LLMResponse(
            content="La prochaine étape est le registre de scanners. Je la démarre dès que tu me dis go.",
            finish_reason="stop",
        )
        provider = _FakeProvider([edit, run, wait, wait, wait])
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(_ExecTool())
        tools.register(_ReadFileTool())
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "demarre les dev"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=20,
                max_tool_result_chars=2000,
                hook=AgentHook(),
                requires_verify_before_done=True,
            )
        )
        firm = [m for m in result.messages if m.get("content") == REMAINING_WORK_REPEAT_PROMPT]
        self.assertEqual(len(firm), 1)
        # edit, test, stop, stop after the reminder, stop after the firm push: then it ends.
        self.assertEqual(provider.calls, 5)


class BackgroundClaimDetectionTest(unittest.TestCase):
    """A final answer saying that finished background work still runs is recognised."""

    def test_claims_are_recognised_and_other_sentences_are_not(self) -> None:
        from navin.utils.runtime import claims_background_work

        for text in (
            # The report of 2026-10-29, written after the last subagent had finished.
            "Le chantier des données (DLP, RAG, mémoire, multimodal, qualité, audit, risque, "
            "incidents) tourne encore. L'intégration finale vient ensuite.",
            "Les deux audits sont toujours en cours, je fais la synthèse dès qu\u2019ils auront terminé.",
            "J'attends le retour du sous-agent sécurité.",
            "Le plus gros chantier tourne encore.",
            "The data audit is still running; I will integrate it next.",
            "Their results will arrive on their own.",
            "I'll report back when they finish.",
            "Waiting for the audit results.",
        ):
            self.assertTrue(claims_background_work(text), text)
        for text in (
            # Things that do run on their own.
            "Le serveur tourne toujours sur le port 5180.",
            "La CI tourne encore sur GitHub.",
            "Le conteneur docker tourne toujours.",
            # Negations, questions, quotes, code, finished work.
            "Rien ne tourne encore en arrière-plan.",
            "Aucun sous-agent n'est encore en cours.",
            "Nothing is still running.",
            "Est-ce que le build tourne encore ?",
            "Le mot « tourne encore » est détecté.",
            "```\nstill running\n```\nDone.",
            "Tout est terminé : 3 fichiers modifiés, tests verts.",
            "",
        ):
            self.assertFalse(claims_background_work(text), text)

    def test_background_work_is_read_from_the_current_request_only(self) -> None:
        from navin.utils.runtime import REMAINING_WORK_CONTINUE_PROMPT, started_background_work

        def call(name: str, arguments: str) -> dict[str, Any]:
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": name, "type": "function", "function": {"name": name, "arguments": arguments}},
            ]}

        ask = {"role": "user", "content": "audite les trois chantiers"}
        spawn = call("spawn", '{"task": "audit"}')
        result = {"role": "user", "content": "[Subagent 'audit' completed successfully]\n\nResult:\nok"}
        self.assertTrue(started_background_work([ask, spawn]))
        self.assertTrue(started_background_work([ask, {"role": "assistant", "content": "ok"}, result]))
        # A reminder Navin wrote does not open a new request.
        nudge = {"role": "user", "content": REMAINING_WORK_CONTINUE_PROMPT}
        self.assertTrue(started_background_work([ask, spawn, nudge]))
        # A subagent of an older request says nothing about the new one.
        later = {"role": "user", "content": "renomme le bouton"}
        self.assertFalse(started_background_work([ask, spawn, {"role": "assistant", "content": "ok"}, later]))
        self.assertTrue(started_background_work([ask, call("exec", '{"command": "npm run build", "background": true}')]))
        self.assertFalse(started_background_work([ask, call("exec", '{"command": "ls"}')]))

    def test_the_correction_is_a_hidden_navin_prompt(self) -> None:
        from navin.session.history_visibility import is_runtime_nudge_message
        from navin.utils.runtime import BACKGROUND_CLAIM_CONTINUE_PROMPT, is_harness_nudge

        self.assertTrue(is_harness_nudge(BACKGROUND_CLAIM_CONTINUE_PROMPT))
        self.assertTrue(is_runtime_nudge_message(
            {"role": "user", "content": BACKGROUND_CLAIM_CONTINUE_PROMPT + "\n\nReply in French."}
        ))


class _StreamingFakeProvider(_FakeProvider):
    """Streams each answer as one delta, like a real streaming provider."""

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        response = await self.chat_with_retry(**kwargs)
        if response.content and kwargs.get("on_content_delta") is not None:
            await kwargs["on_content_delta"](response.content)
        return response


class _StreamEndRecorder(AgentHook):
    def __init__(self) -> None:
        super().__init__()
        self.ends: list[tuple[bool, bool]] = []

    def wants_streaming(self) -> bool:
        return True

    async def on_stream_end(self, context: Any, *, resuming: bool) -> None:
        self.ends.append((resuming, context.stream_discarded))


class BackgroundClaimNudgeTest(unittest.IsolatedAsyncioTestCase):
    """'X is still running' once nothing runs any more is corrected once."""

    _CLAIM = "Le chantier des données tourne encore. L'intégration finale vient ensuite."
    _DONE = "Les trois chantiers sont terminés et intégrés."

    async def _run(
        self,
        finals: list[str],
        *,
        idle: bool | Exception = True,
        scope: str = "main",
        hook: AgentHook | None = None,
        provider_cls: type[_FakeProvider] = _FakeProvider,
    ) -> tuple[int, AgentRunResult, list[int]]:
        provider = provider_cls([LLMResponse(content=text, finish_reason="stop") for text in finals])
        checked: list[int] = []

        def background_idle(messages: list[dict[str, Any]]) -> bool:
            checked.append(len(messages))
            if isinstance(idle, Exception):
                raise idle
            return idle

        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "audite les trois chantiers"}],
                tools=ToolRegistry(),
                runtime=_runtime(provider),
                max_iterations=6,
                max_tool_result_chars=2000,
                hook=hook or AgentHook(),
                user_hook_scope=scope,
                background_idle=background_idle,
            )
        )
        return provider.calls, result, checked

    @staticmethod
    def _nudges(result: AgentRunResult) -> list[dict[str, Any]]:
        from navin.utils.runtime import BACKGROUND_CLAIM_CONTINUE_PROMPT

        return [
            m for m in result.messages
            if m.get("role") == "user"
            and str(m.get("content")).startswith(BACKGROUND_CLAIM_CONTINUE_PROMPT)
        ]

    async def test_a_stale_claim_is_corrected_once(self) -> None:
        calls, result, _checked = await self._run([self._CLAIM, self._DONE])
        self.assertEqual(len(self._nudges(result)), 1)
        self.assertEqual(calls, 2)
        self.assertEqual(result.final_content, self._DONE)

    async def test_the_correction_is_sent_only_once(self) -> None:
        calls, result, _checked = await self._run([self._CLAIM, self._CLAIM, "never"])
        self.assertEqual(len(self._nudges(result)), 1)
        self.assertEqual(calls, 2)
        self.assertEqual(result.final_content, self._CLAIM)

    async def test_work_that_really_runs_is_left_alone(self) -> None:
        calls, result, checked = await self._run([self._CLAIM, "never"], idle=False)
        self.assertEqual(self._nudges(result), [])
        self.assertEqual(calls, 1)
        self.assertEqual(len(checked), 1)

    async def test_a_subagent_or_a_broken_check_is_left_alone(self) -> None:
        for kwargs in ({"scope": "subagent"}, {"idle": RuntimeError("manager gone")}):
            calls, result, _checked = await self._run([self._CLAIM, "never"], **kwargs)
            self.assertEqual(self._nudges(result), [], kwargs)
            self.assertEqual(calls, 1, kwargs)

    async def test_an_answer_without_the_claim_never_asks(self) -> None:
        calls, result, checked = await self._run([self._DONE, "never"])
        self.assertEqual(calls, 1)
        self.assertEqual(checked, [])
        self.assertEqual(result.final_content, self._DONE)

    async def test_the_stale_answer_is_withdrawn_from_the_screen(self) -> None:
        hook = _StreamEndRecorder()
        _calls, result, _checked = await self._run(
            [self._CLAIM, self._DONE], hook=hook, provider_cls=_StreamingFakeProvider,
        )
        self.assertEqual(result.final_content, self._DONE)
        self.assertEqual(hook.ends, [(True, True), (False, False)])
