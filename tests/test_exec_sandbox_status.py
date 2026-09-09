# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The exec tool says how each command is confined, and asks before lifting it.

Cursor shows "Sandboxed" on every command it runs and lets the model ask for
a run outside the sandbox that the user must approve. This is Navin's version
of the same mechanism, checked at the three seams: the prepared command knows
whether the OS sandbox actually applied, the activity card is told before the
process starts, and the result text carries the hint the model needs when a
command failed inside the sandbox.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from navin.agent.approval import (
    ApprovalDecision,
    ApprovalRequest,
    bind_approval_gate,
    reset_approval_gate,
)
from navin.agent.tool_output import (
    bind_tool_call_meta,
    bind_tool_output_emitter,
    emit_tool_meta,
)
from navin.agent.tools import sandbox
from navin.agent.tools.shell import ExecTool, _PreparedCommand


class _Gate:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.seen: list[ApprovalRequest] = []

    async def ask(self, request: ApprovalRequest) -> ApprovalDecision:
        self.seen.append(request)
        return ApprovalDecision(
            allowed=self.allowed,
            reason="" if self.allowed else "The user refused this operation.",
        )


def _with_gate(coro: Any, gate: _Gate | None) -> Any:
    async def go() -> Any:
        token = bind_approval_gate(gate)
        try:
            return await coro
        finally:
            reset_approval_gate(token)

    return asyncio.run(go())


def _landlock_enforced() -> bool:
    binary = sandbox.native_sandbox_binary()
    if binary is None:
        return False
    with tempfile.TemporaryDirectory() as probe:
        result = subprocess.run(
            [binary, "--strict", "--workspace", probe, "--", "true"],
            capture_output=True,
            timeout=10,
        )
    return result.returncode == 0


class ResultNoteTest(unittest.TestCase):
    """The footer under a command's output, sized to what the model needs."""

    def test_a_success_inside_the_sandbox_adds_nothing(self) -> None:
        self.assertEqual(sandbox.sandbox_result_note("native", exit_code=0), "")

    def test_an_unconfined_run_adds_nothing(self) -> None:
        self.assertEqual(sandbox.sandbox_result_note("", exit_code=1), "")

    def test_a_failure_inside_the_sandbox_points_at_the_way_out(self) -> None:
        note = sandbox.sandbox_result_note("native", exit_code=1, output="boom")
        self.assertIn("[Sandboxed:", note)
        self.assertIn("unsandboxed=true", note)
        self.assertNotIn("permission refusal", note)

    def test_a_permission_denial_is_named_as_such(self) -> None:
        note = sandbox.sandbox_result_note(
            "native", exit_code=1, output="bash: /etc/x: Permission denied"
        )
        self.assertIn("permission refusal", note)

    def test_a_lifted_run_is_recorded_even_on_success(self) -> None:
        note = sandbox.sandbox_result_note("", exit_code=0, lifted=True)
        self.assertIn("outside the OS sandbox", note)
        self.assertIn("approved", note)

    def test_a_still_running_session_gets_no_failure_hint(self) -> None:
        self.assertEqual(sandbox.sandbox_result_note("native", exit_code=None), "")


class PreparedCommandKnowsItsConfinementTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _prepare(self, tool: ExecTool, gate: _Gate | None = None, **kwargs: Any) -> _PreparedCommand | str:
        return _with_gate(tool._prepare_command("echo hi", **kwargs), gate)

    def test_no_sandbox_configured_means_unconfined(self) -> None:
        prepared = self._prepare(ExecTool(working_dir=str(self.root)))
        assert isinstance(prepared, _PreparedCommand)
        self.assertEqual(prepared.sandbox, "")
        self.assertFalse(prepared.sandbox_lifted)

    def test_the_native_sandbox_is_reported_only_when_it_wraps(self) -> None:
        if sandbox.native_sandbox_binary() is None:
            self.skipTest("navin-sandbox binary not built")
        prepared = self._prepare(ExecTool(working_dir=str(self.root), sandbox="native"))
        assert isinstance(prepared, _PreparedCommand)
        self.assertEqual(prepared.sandbox, "native")
        self.assertIn("navin-sandbox", prepared.command)

    def test_a_missing_helper_is_not_reported_as_sandboxed(self) -> None:
        from unittest.mock import patch

        with (
            patch.object(sandbox, "native_sandbox_binary", return_value=None),
            patch.object(sandbox, "_compile_native_sandbox", return_value=None),
        ):
            prepared = self._prepare(ExecTool(working_dir=str(self.root), sandbox="native"))
        assert isinstance(prepared, _PreparedCommand)
        self.assertEqual(prepared.sandbox, "")
        self.assertNotIn("navin-sandbox", prepared.command)

    def test_unsandboxed_runs_unconfined_once_the_user_allows(self) -> None:
        gate = _Gate(allowed=True)
        tool = ExecTool(working_dir=str(self.root), sandbox="native")
        prepared = self._prepare(tool, gate, unsandboxed=True)
        assert isinstance(prepared, _PreparedCommand)
        self.assertTrue(prepared.sandbox_lifted)
        self.assertEqual(prepared.sandbox, "")
        self.assertNotIn("navin-sandbox", prepared.command)
        self.assertEqual(len(gate.seen), 1)
        request = gate.seen[0]
        self.assertEqual(request.scope, "exec:sandboxEscape")
        self.assertIn("outside the sandbox", request.action)
        self.assertIn("echo hi", request.detail)

    def test_a_refusal_keeps_the_command_from_running(self) -> None:
        gate = _Gate(allowed=False)
        tool = ExecTool(working_dir=str(self.root), sandbox="native")
        result = self._prepare(tool, gate, unsandboxed=True)
        self.assertIsInstance(result, str)
        self.assertIn("not run outside the sandbox", str(result))
        self.assertIn("refused", str(result))

    def test_nobody_to_ask_means_the_sandbox_stays_on(self) -> None:
        tool = ExecTool(working_dir=str(self.root), sandbox="native")
        result = self._prepare(tool, None, unsandboxed=True)
        self.assertIsInstance(result, str)
        self.assertIn("not run outside the sandbox", str(result))

    def test_the_strict_profile_never_asks_and_never_lifts(self) -> None:
        gate = _Gate(allowed=True)
        tool = ExecTool(working_dir=str(self.root), sandbox="native", sandbox_strict=True)
        result = self._prepare(tool, gate, unsandboxed=True)
        self.assertIsInstance(result, str)
        self.assertIn("strict security profile", str(result))
        self.assertFalse(gate.seen)

    def test_unsandboxed_is_a_no_op_without_a_configured_sandbox(self) -> None:
        gate = _Gate(allowed=False)
        prepared = self._prepare(ExecTool(working_dir=str(self.root)), gate, unsandboxed=True)
        assert isinstance(prepared, _PreparedCommand)
        self.assertFalse(prepared.sandbox_lifted)
        self.assertFalse(gate.seen)


class ActivityCardIsToldFirstTest(unittest.TestCase):
    """The sandbox fact rides the tool call's own event stream, before output."""

    def test_meta_event_carries_the_sandbox_on_the_output_phase(self) -> None:
        seen: list[dict[str, Any]] = []

        async def emitter(payload: dict[str, Any]) -> None:
            seen.append(payload)

        async def go() -> None:
            bind_tool_output_emitter(emitter)
            bind_tool_call_meta("call-1", "exec", {"command": "echo hi"})
            await emit_tool_meta(sandbox="native", sandbox_lifted=None)

        asyncio.run(go())
        self.assertEqual(len(seen), 1)
        payload = seen[0]
        self.assertEqual(payload["phase"], "output")
        self.assertEqual(payload["call_id"], "call-1")
        self.assertEqual(payload["sandbox"], "native")
        self.assertNotIn("sandbox_lifted", payload)
        self.assertNotIn("output", payload)

    def test_nothing_is_emitted_without_facts_or_emitter(self) -> None:
        seen: list[dict[str, Any]] = []

        async def emitter(payload: dict[str, Any]) -> None:
            seen.append(payload)

        async def go() -> None:
            bind_tool_output_emitter(emitter)
            bind_tool_call_meta("call-2", "exec", {})
            await emit_tool_meta(sandbox=None)
            bind_tool_output_emitter(None)
            await emit_tool_meta(sandbox="native")

        asyncio.run(go())
        self.assertEqual(seen, [])

    def test_the_exec_tool_announces_before_running(self) -> None:
        seen: list[dict[str, Any]] = []

        async def emitter(payload: dict[str, Any]) -> None:
            seen.append(payload)

        tool = ExecTool(working_dir=tempfile.gettempdir())

        async def go() -> None:
            bind_tool_output_emitter(emitter)
            bind_tool_call_meta("call-3", "exec", {"command": "true"})
            await tool._announce_sandbox(
                _PreparedCommand(
                    command="true", cwd="/", env={}, timeout=5, shell_program=None,
                    login=False, sandbox="", sandbox_lifted=True,
                )
            )
            await tool._announce_sandbox(
                _PreparedCommand(
                    command="true", cwd="/", env={}, timeout=5, shell_program=None,
                    login=False,
                )
            )

        asyncio.run(go())
        self.assertEqual([p.get("sandbox_lifted") for p in seen], [True])


class SandboxedRunEndToEndTest(unittest.TestCase):
    """With the real helper: a refused write comes back with the hint attached."""

    def setUp(self) -> None:
        if not _landlock_enforced():
            self.skipTest("navin-sandbox binary missing or kernel does not enforce Landlock")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def test_a_denied_write_is_explained_to_the_model(self) -> None:
        tool = ExecTool(working_dir=str(self.root), sandbox="native")
        out = asyncio.run(
            tool.execute(command="echo x > /etc/navin-sandbox-should-not-exist")
        )
        self.assertNotIn("Exit code: 0", out)
        self.assertIn("[Sandboxed:", out)
        self.assertIn("unsandboxed=true", out)
        self.assertFalse(Path("/etc/navin-sandbox-should-not-exist").exists())

    def test_a_clean_run_inside_the_sandbox_has_no_footer(self) -> None:
        tool = ExecTool(working_dir=str(self.root), sandbox="native")
        out = asyncio.run(tool.execute(command="echo READY"))
        self.assertIn("READY", out)
        self.assertNotIn("[Sandboxed:", out)

    def test_an_approved_lift_is_recorded_in_the_result(self) -> None:
        tool = ExecTool(working_dir=str(self.root), sandbox="native")
        out = _with_gate(tool.execute(command="echo FREE", unsandboxed=True), _Gate(allowed=True))
        self.assertIn("FREE", out)
        self.assertIn("outside the OS sandbox", out)


class TerminalPrefixTest(unittest.TestCase):
    def test_the_prefix_has_no_command_tail(self) -> None:
        if sandbox.native_sandbox_binary() is None:
            self.skipTest("navin-sandbox binary not built")
        with tempfile.TemporaryDirectory() as ws:
            argv = sandbox.native_sandbox_argv(ws, ws)
        self.assertIsNotNone(argv)
        assert argv is not None
        self.assertIn("navin-sandbox", argv[0])
        self.assertEqual(argv[1], "--workspace")
        self.assertNotIn("--", argv)
        self.assertNotIn("sh", argv)

    def test_no_helper_means_no_prefix(self) -> None:
        from unittest.mock import patch

        with (
            patch.object(sandbox, "native_sandbox_binary", return_value=None),
            patch.object(sandbox, "_compile_native_sandbox", return_value=None),
        ):
            self.assertIsNone(sandbox.native_sandbox_argv("/tmp", "/tmp"))


if __name__ == "__main__":
    unittest.main()
