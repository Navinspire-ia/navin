"""write_stdin applies the exec deny/approval policy to stdin payloads.

Before this guard, exec checked the command it started but write_stdin sent
raw text into an already-running shell with no check at all: a background
``bash`` session plus one stdin line was a clean bypass of the deny rules and
of ask-every-command. These tests pin the closed hole.
"""

from __future__ import annotations

import unittest
from unittest import mock

from navin.agent.approval import ApprovalDecision
from navin.agent.tools.exec_session import WriteStdinTool
from navin.agent.tools.shell import StdinCommandGuard


class _ExplodingManager:
    """A session manager that must never be reached when the guard refuses."""

    def __init__(self) -> None:
        self.write_called = False

    async def write(self, **kwargs):  # pragma: no cover - reaching it is the bug
        self.write_called = True
        raise AssertionError("write_stdin reached the manager despite a refusal")


class _FakeExecCfg:
    def __init__(self, *, deny=None, allow=None, builtins=False) -> None:
        self.deny_patterns = deny or []
        self.allow_patterns = allow or []
        self.builtin_deny_rules = builtins


class _FakeApprovalsCfg:
    def __init__(self, *, enabled=False, exec_ask="destructive") -> None:
        self.enabled = enabled
        self.exec_ask = exec_ask


class StdinGuardVerdictTest(unittest.IsolatedAsyncioTestCase):
    """The pure policy: what may go down the pipe."""

    async def test_a_denied_line_is_refused(self) -> None:
        guard = StdinCommandGuard.from_config(
            _FakeExecCfg(deny=[r"rm\s+-rf"]), _FakeApprovalsCfg(),
        )
        refusal = await guard.check("rm -rf /tmp/x\n", session_command="bash")
        assert refusal is not None
        self.assertIn("deny pattern", refusal)

    async def test_the_denied_line_is_caught_even_in_the_middle(self) -> None:
        guard = StdinCommandGuard.from_config(
            _FakeExecCfg(deny=[r"rm\s+-rf"]), _FakeApprovalsCfg(),
        )
        refusal = await guard.check(
            "echo hello\nrm -rf /\necho done\n", session_command="bash",
        )
        self.assertIsNotNone(refusal)

    async def test_harmless_input_passes(self) -> None:
        guard = StdinCommandGuard.from_config(
            _FakeExecCfg(deny=[r"rm\s+-rf"]), _FakeApprovalsCfg(),
        )
        self.assertIsNone(await guard.check("print(1 + 1)\n", session_command="python -i"))

    async def test_a_pure_poll_is_never_guarded(self) -> None:
        guard = StdinCommandGuard.from_config(
            _FakeExecCfg(deny=[r".*"]), _FakeApprovalsCfg(),
        )
        self.assertIsNone(await guard.check("", session_command="bash"))
        self.assertIsNone(await guard.check(None, session_command="bash"))
        self.assertIsNone(await guard.check("\n\n  \n", session_command="bash"))

    async def test_allow_patterns_exempt_a_line_like_in_exec(self) -> None:
        guard = StdinCommandGuard.from_config(
            _FakeExecCfg(deny=[r"rm\s+-rf"], allow=[r"rm -rf ./build"]),
            _FakeApprovalsCfg(),
        )
        self.assertIsNone(
            await guard.check("rm -rf ./build\n", session_command="bash")
        )

    async def test_builtin_approvable_rule_goes_through_the_approval_gate(self) -> None:
        guard = StdinCommandGuard.from_config(
            _FakeExecCfg(builtins=True), _FakeApprovalsCfg(enabled=True),
        )
        with mock.patch(
            "navin.agent.approval.request_approval",
            new=mock.AsyncMock(return_value=ApprovalDecision(
                allowed=False, reason="The user refused this operation.",
            )),
        ) as ask:
            refusal = await guard.check("rm -rf /\n", session_command="bash")
        self.assertIsNotNone(refusal)
        self.assertTrue(ask.await_count >= 1)

    async def test_an_approved_destructive_line_goes_through(self) -> None:
        guard = StdinCommandGuard.from_config(
            _FakeExecCfg(builtins=True), _FakeApprovalsCfg(enabled=True),
        )
        with mock.patch(
            "navin.agent.approval.request_approval",
            new=mock.AsyncMock(return_value=ApprovalDecision(allowed=True)),
        ):
            self.assertIsNone(await guard.check("rm -rf /tmp/x\n", session_command="bash"))

    async def test_ask_every_command_asks_before_sending(self) -> None:
        guard = StdinCommandGuard.from_config(
            _FakeExecCfg(), _FakeApprovalsCfg(enabled=True, exec_ask="always"),
        )
        self.assertTrue(guard.ask_every_command)
        with mock.patch(
            "navin.agent.approval.request_approval",
            new=mock.AsyncMock(return_value=ApprovalDecision(
                allowed=False, reason="no",
            )),
        ):
            refusal = await guard.check("ls\n", session_command="bash")
        self.assertIsNotNone(refusal)


class WriteStdinToolWiringTest(unittest.IsolatedAsyncioTestCase):
    """The tool refuses before the manager ever sees the payload."""

    async def test_a_refusal_never_reaches_the_session(self) -> None:
        manager = _ExplodingManager()
        tool = WriteStdinTool(
            manager=manager,  # type: ignore[arg-type]
            stdin_guard=StdinCommandGuard.from_config(
                _FakeExecCfg(deny=[r"rm\s+-rf"]), _FakeApprovalsCfg(),
            ),
        )
        with mock.patch.object(
            WriteStdinTool, "_session_command",
            new=mock.AsyncMock(return_value="bash"),
        ):
            result = await tool.execute(session_id="s1", chars="rm -rf /\n")
        self.assertIn("deny pattern", str(result))
        self.assertFalse(manager.write_called)

    async def test_without_a_guard_the_tool_behaves_as_before(self) -> None:
        # Direct construction (tests, embedders) stays permissive; the guard is
        # wired in create() from the live config.
        tool = WriteStdinTool(manager=_ExplodingManager())  # type: ignore[arg-type]
        self.assertIsNone(tool._stdin_guard)


if __name__ == "__main__":
    unittest.main()
