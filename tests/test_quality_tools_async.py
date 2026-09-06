"""The quality tools shell out to linters, test runners and git.

Those subprocesses can run for minutes, and the tools serve a gateway that
multiplexes sessions over one event loop: a blocking call here freezes every
other conversation. These tests pin the off-loop behaviour with slow stubs, and
cover the two tool-level policies that live in the same file - asking before a
git-HEAD rollback discards work, and accepting the absolute spelling of a
workspace path that read_file just answered with.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.agent.approval import (
    ApprovalDecision,
    bind_approval_gate,
    reset_approval_gate,
)
from navin.agent.tools import quality as quality_tools
from navin.agent.tools.cli_apps import CliAppsTool
from navin.apps.cli import CliAppManager
from navin.quality import linters as linters_mod
from navin.quality import testing as testing_mod
from navin.quality import verify as verify_mod

# Long enough that a blocked loop is unmistakable, short enough for a test.
_BLOCK_S = 0.3


async def _heartbeats_during(coro) -> int:
    """How often the event loop turned over while ``coro`` ran.

    A ticker that beats every 10 ms is the stand-in for "every other session".
    If the tool blocks the loop for its whole run, the ticker never gets a
    turn and the count stays near zero; off the loop it beats dozens of times.
    """
    beats = 0

    async def beat() -> None:
        nonlocal beats
        while True:
            beats += 1
            await asyncio.sleep(0.01)

    ticker = asyncio.create_task(beat())
    try:
        await coro
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
    return beats


class _SlowStub:
    """A callable that blocks like a real subprocess, then answers."""

    def __init__(self, result) -> None:
        self._result = result

    def __call__(self, *args, **kwargs):
        time.sleep(_BLOCK_S)
        return self._result


class EventLoopStaysResponsiveTest(unittest.TestCase):
    """Each execute path that shells out must run its blocking work off-loop."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _assert_loop_kept_beating(self, coro) -> None:
        beats = asyncio.run(_heartbeats_during(coro))
        # A blocked loop yields 0-2 beats over the whole stubbed run.
        self.assertGreaterEqual(beats, 5, "the event loop was starved during execute")

    def test_a_slow_lint_does_not_freeze_other_sessions(self) -> None:
        (self.root / "app.py").write_text("x = 1\n", encoding="utf-8")
        tool = quality_tools.LintTool(workspace=str(self.root))
        with mock.patch.object(linters_mod, "lint_file", _SlowStub([])):
            self._assert_loop_kept_beating(tool.execute(action="file", path="app.py"))

    def test_linting_changed_files_does_not_freeze_other_sessions(self) -> None:
        (self.root / "app.py").write_text("x = 1\n", encoding="utf-8")
        tool = quality_tools.LintTool(workspace=str(self.root))
        with (
            mock.patch.object(verify_mod, "changed_files", return_value=["app.py"]),
            mock.patch.object(linters_mod, "lint_file", _SlowStub([])),
        ):
            self._assert_loop_kept_beating(tool.execute(action="changed"))

    def test_a_long_test_suite_does_not_freeze_other_sessions(self) -> None:
        tool = quality_tools.TestRunTool(workspace=str(self.root))
        outcome = testing_mod.TestOutcome(runner="pytest", ran=True, passed=1, exit_code=0)
        with mock.patch.object(testing_mod, "run_tests", _SlowStub([outcome])):
            self._assert_loop_kept_beating(tool.execute(action="run"))

    def test_a_full_verification_does_not_freeze_other_sessions(self) -> None:
        tool = quality_tools.VerifyTool(workspace=str(self.root))
        report = verify_mod.VerificationReport(verdict=verify_mod.VERDICT_NO_CHANGES)
        with mock.patch.object(verify_mod, "verify_changes", _SlowStub(report)):
            self._assert_loop_kept_beating(tool.execute(action="check"))

    def test_a_slow_cli_app_does_not_freeze_other_sessions(self) -> None:
        tool = CliAppsTool(workspace=self.root)

        def slow_run(self, name, **kwargs):
            time.sleep(_BLOCK_S)
            return "done"

        with mock.patch.object(CliAppManager, "run", slow_run):
            self._assert_loop_kept_beating(tool.execute(name="someapp"))


class _Gate:
    """An approval gate with a fixed answer, recording what it was asked."""

    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.requests: list = []

    async def ask(self, request) -> ApprovalDecision:
        self.requests.append(request)
        return ApprovalDecision(
            allowed=self.allowed,
            reason="" if self.allowed else "The user refused this operation.",
        )


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class RollbackApprovalTest(unittest.TestCase):
    """Restoring from HEAD discards uncommitted work, exactly like reset --hard.

    The git tool asks the user before that; verify action=rollback without a
    snapshot must owe the same question, and keep working unattended, where the
    gate deliberately answers with whatever the tool did before it could ask.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        for argv in (
            ["git", "init", "-q", "."],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(argv, cwd=self.root, check=True, capture_output=True)  # noqa: S603
        self.target = self.root / "app.py"
        self.target.write_text("value = 1\n", encoding="utf-8")
        subprocess.run(  # noqa: S603
            ["git", "add", "-A"], cwd=self.root, check=True, capture_output=True
        )
        subprocess.run(  # noqa: S603
            ["git", "commit", "-qm", "init"],
            cwd=self.root, check=True, capture_output=True,
        )
        self.tool = quality_tools.VerifyTool(workspace=str(self.root))

    def _bind(self, gate) -> None:
        token = bind_approval_gate(gate)
        self.addCleanup(reset_approval_gate, token)

    def test_a_refused_rollback_leaves_the_working_tree_untouched(self) -> None:
        self.target.write_text("value = 2\n", encoding="utf-8")
        gate = _Gate(allowed=False)
        self._bind(gate)
        out = asyncio.run(self.tool.execute(action="rollback"))
        self.assertIn("refused", out)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "value = 2\n")
        self.assertEqual(len(gate.requests), 1)
        self.assertEqual(gate.requests[0].tool, "verify")
        self.assertIn("HEAD", gate.requests[0].detail)

    def test_an_approved_rollback_restores_from_head(self) -> None:
        self.target.write_text("value = 2\n", encoding="utf-8")
        gate = _Gate(allowed=True)
        self._bind(gate)
        out = asyncio.run(self.tool.execute(action="rollback"))
        self.assertIn("Restored 1 file(s) from git HEAD", out)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "value = 1\n")
        self.assertEqual(len(gate.requests), 1)

    def test_an_unattended_rollback_still_works(self) -> None:
        """No gate bound is the CLI and the scheduled run; asking must not
        take the operation away from them."""
        self.target.write_text("value = 2\n", encoding="utf-8")
        out = asyncio.run(self.tool.execute(action="rollback"))
        self.assertIn("Restored 1 file(s) from git HEAD", out)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "value = 1\n")

    def test_a_clean_tree_asks_no_question(self) -> None:
        gate = _Gate(allowed=False)
        self._bind(gate)
        out = asyncio.run(self.tool.execute(action="rollback"))
        self.assertNotIn("refused", out)
        self.assertEqual(gate.requests, [])


class LintAcceptsAbsolutePathsTest(unittest.TestCase):
    """lint action=file must accept the path spelling read_file answers with.

    The JSON syntax check is built in, so these run on a bare machine.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.tool = quality_tools.LintTool(workspace=str(self.root))

    def test_an_absolute_path_inside_the_workspace_is_linted(self) -> None:
        target = self.root / "broken.json"
        target.write_text('{"a": 1,}', encoding="utf-8")
        out = asyncio.run(self.tool.execute(action="file", path=str(target)))
        self.assertNotIn("not found", out)
        self.assertIn("error", out)

    def test_an_absolute_path_outside_the_workspace_names_the_root(self) -> None:
        with TemporaryDirectory() as elsewhere:
            foreign = Path(elsewhere).resolve() / "other.json"
            foreign.write_text("{}", encoding="utf-8")
            out = asyncio.run(self.tool.execute(action="file", path=str(foreign)))
        self.assertIn("outside the workspace root", out)
        self.assertIn(str(self.root), out)

    def test_a_root_anchored_relative_path_still_works(self) -> None:
        """Models also write "/src/app.py" meaning the project-relative path;
        the absolute-path handling must not take that lenience away."""
        (self.root / "ok.json").write_text('{"a": 1}', encoding="utf-8")
        out = asyncio.run(self.tool.execute(action="file", path="/ok.json"))
        self.assertNotIn("not found", out)
        self.assertNotIn("outside the workspace", out)


if __name__ == "__main__":
    unittest.main()
