"""The three places a tool stops to ask, driven end to end.

Each one is checked three ways, because the third is the one that regresses:
allowed, refused, and unattended. Unattended has to reproduce exactly what the
tool did before it could ask - a shell command that used to be blocked stays
blocked, a git command that used to run keeps running - or this feature quietly
changes what a CLI or scheduled run is able to do.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from navin.agent.approval import (
    ApprovalDecision,
    ApprovalRequest,
    bind_approval_gate,
    reset_approval_gate,
)
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.agent.tools.file_manage import ManageFilesTool
from navin.agent.tools.file_state import FileStates
from navin.agent.tools.filesystem import ReadFileTool
from navin.agent.tools.git import GitTool
from navin.agent.tools.shell import ExecTool
from navin.utils.git_state import clear_cache


class _RecordingGate:
    """Answers every request the same way, and keeps what it was asked."""

    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.seen: list[ApprovalRequest] = []

    async def ask(self, request: ApprovalRequest) -> ApprovalDecision:
        self.seen.append(request)
        return ApprovalDecision(
            allowed=self.allowed,
            reason="" if self.allowed else "The user refused this operation.",
        )


class _GateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _run(self, coro: Any, gate: _RecordingGate | None) -> str:
        async def go() -> str:
            token = bind_approval_gate(gate)
            try:
                return str(await coro)
            finally:
                reset_approval_gate(token)

        return asyncio.run(go())


class ExecApprovalTest(_GateTest):
    """A built-in deny rule becomes a question instead of a wall."""

    def _exec(self, gate: _RecordingGate | None, command: str = "rm -rf junk") -> str:
        # The built-in rules are opt-in, so this whole path only exists for an
        # operator who turned them on; without that, rm -rf simply runs.
        tool = ExecTool(working_dir=str(self.root), builtin_deny_rules=True)
        return self._run(tool.execute(command=command), gate)

    def test_without_the_builtin_rules_nothing_is_asked(self) -> None:
        (self.root / "junk").mkdir()
        gate = _RecordingGate(allowed=False)
        tool = ExecTool(working_dir=str(self.root))
        out = self._run(tool.execute(command="rm -rf junk"), gate)
        self.assertNotIn("blocked", out)
        self.assertFalse((self.root / "junk").exists())
        self.assertFalse(gate.seen)

    def test_an_allow_runs_the_command(self) -> None:
        (self.root / "junk").mkdir()
        gate = _RecordingGate(allowed=True)
        out = self._exec(gate)
        self.assertNotIn("blocked", out)
        self.assertFalse((self.root / "junk").exists())
        self.assertEqual(len(gate.seen), 1)

    def test_a_refusal_keeps_the_original_block_message(self) -> None:
        (self.root / "junk").mkdir()
        out = self._exec(_RecordingGate(allowed=False))
        self.assertIn("blocked by deny pattern filter", out)
        self.assertIn("refused", out)
        self.assertTrue((self.root / "junk").exists())

    def test_unattended_stays_blocked_exactly_as_before(self) -> None:
        (self.root / "junk").mkdir()
        out = self._exec(None)
        self.assertIn("blocked by deny pattern filter", out)
        self.assertTrue((self.root / "junk").exists())

    def test_the_question_names_the_rule_and_the_command(self) -> None:
        gate = _RecordingGate(allowed=False)
        self._exec(gate, "rm -rf build")
        request = gate.seen[0]
        self.assertEqual(request.tool, "exec")
        self.assertIn("recursive", request.reason)
        self.assertIn("rm -rf build", request.detail)
        self.assertEqual(request.scope, "exec:recursiveDelete")

    def test_disk_operations_are_asked_in_the_chat(self) -> None:
        gate = _RecordingGate(allowed=False)
        out = self._exec(gate, "mkfs /dev/sda1")
        self.assertEqual(len(gate.seen), 1)
        self.assertEqual(gate.seen[0].scope, "exec:diskOperations")
        self.assertIn("blocked by deny pattern filter", out)

    def test_rewriting_internal_state_stays_a_hard_stop(self) -> None:
        gate = _RecordingGate(allowed=True)
        out = self._exec(gate, "echo x >> history.jsonl")
        self.assertIn("blocked by deny pattern filter", out)
        self.assertEqual(gate.seen, [])

    def test_a_user_deny_pattern_is_asked_in_the_chat(self) -> None:
        gate = _RecordingGate(allowed=False)
        tool = ExecTool(working_dir=str(self.root), deny_patterns=[r"\bcurl\b"])
        out = self._run(tool.execute(command="curl http://example.com"), gate)
        self.assertEqual(len(gate.seen), 1)
        self.assertEqual(gate.seen[0].scope, "exec:operatorDeny")
        self.assertIn("blocked by deny pattern filter", out)

    def test_a_user_deny_pattern_runs_when_the_user_allows_it(self) -> None:
        gate = _RecordingGate(allowed=True)
        tool = ExecTool(working_dir=str(self.root), deny_patterns=[r"\becho\b"])
        out = self._run(tool.execute(command="echo allowed-deny"), gate)
        self.assertEqual(len(gate.seen), 1)
        self.assertIn("allowed-deny", out)

    def test_ask_every_command_pauses_before_a_safe_command(self) -> None:
        gate = _RecordingGate(allowed=False)
        tool = ExecTool(working_dir=str(self.root), ask_every_command=True)
        out = self._run(tool.execute(command="echo ok"), gate)
        self.assertEqual(len(gate.seen), 1)
        self.assertEqual(gate.seen[0].scope, "exec:command")
        self.assertIn("echo ok", gate.seen[0].detail)
        self.assertIn("refused", out.lower())

    def test_ask_every_command_runs_when_allowed(self) -> None:
        gate = _RecordingGate(allowed=True)
        tool = ExecTool(working_dir=str(self.root), ask_every_command=True)
        out = self._run(tool.execute(command="echo ok"), gate)
        self.assertEqual(len(gate.seen), 1)
        self.assertIn("ok", out)
        self.assertNotIn("refused", out.lower())

    def test_ask_every_command_unattended_still_runs(self) -> None:
        tool = ExecTool(working_dir=str(self.root), ask_every_command=True)
        out = self._run(tool.execute(command="echo ok"), None)
        self.assertIn("ok", out)

    def test_an_allow_pattern_still_short_circuits_without_asking(self) -> None:
        (self.root / "junk").mkdir()
        gate = _RecordingGate(allowed=False)
        tool = ExecTool(working_dir=str(self.root), allow_patterns=[r"rm -rf junk"])
        out = self._run(tool.execute(command="rm -rf junk"), gate)
        self.assertNotIn("blocked", out)
        self.assertEqual(gate.seen, [])

    def test_a_workspace_escape_is_asked_in_the_chat(self) -> None:
        gate = _RecordingGate(allowed=False)
        tool = ExecTool(working_dir=str(self.root), restrict_to_workspace=True)
        out = self._run(tool.execute(command="cat ../secrets"), gate)
        self.assertEqual(len(gate.seen), 1)
        self.assertEqual(gate.seen[0].scope, "exec:workspaceEscape")
        self.assertIn("blocked by safety guard", out)

    def test_a_workspace_escape_runs_when_the_user_allows_it(self) -> None:
        outside = Path.home() / "navin-approval-escape.txt"
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        outside.write_text("escaped-ok\n", encoding="utf-8")
        gate = _RecordingGate(allowed=True)
        tool = ExecTool(working_dir=str(self.root), restrict_to_workspace=True)
        out = self._run(tool.execute(command=f"cat {outside}"), gate)
        self.assertEqual(len(gate.seen), 1)
        self.assertIn("escaped-ok", out)
        self.assertNotIn("blocked by safety guard", out)

    def test_an_outside_working_dir_is_asked_in_the_chat(self) -> None:
        extra = TemporaryDirectory()
        self.addCleanup(extra.cleanup)
        elsewhere = Path(extra.name).resolve()
        gate = _RecordingGate(allowed=False)
        tool = ExecTool(working_dir=str(self.root), restrict_to_workspace=True)
        out = self._run(
            tool.execute(command="pwd", working_dir=str(elsewhere)),
            gate,
        )
        self.assertEqual(len(gate.seen), 1)
        self.assertEqual(gate.seen[0].scope, "exec:workspaceEscape")
        self.assertIn("outside the configured workspace", out)


class FileApprovalTest(_GateTest):
    """A path outside the project is a card, not a silent wall."""

    def test_a_read_outside_the_project_is_asked_in_the_chat(self) -> None:
        outside = Path.home() / "navin-approval-read.txt"
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        outside.write_text("file-ok\n", encoding="utf-8")
        gate = _RecordingGate(allowed=False)
        tool = ReadFileTool(
            workspace=self.root,
            allowed_dir=self.root,
            restrict_to_workspace=True,
            file_states=FileStates(),
        )
        out = self._run(tool.execute(path=str(outside)), gate)
        self.assertEqual(len(gate.seen), 1)
        self.assertIn(str(outside), gate.seen[0].detail)
        self.assertNotIn("file-ok", out)

    def test_a_read_outside_the_project_runs_when_allowed(self) -> None:
        outside = Path.home() / "navin-approval-read.txt"
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        outside.write_text("file-ok\n", encoding="utf-8")
        gate = _RecordingGate(allowed=True)
        tool = ReadFileTool(
            workspace=self.root,
            allowed_dir=self.root,
            restrict_to_workspace=True,
            file_states=FileStates(),
        )
        out = self._run(tool.execute(path=str(outside)), gate)
        self.assertEqual(len(gate.seen), 1)
        self.assertIn("file-ok", out)


class ManageFilesApprovalTest(_GateTest):
    """Destroying work that exists nowhere else is put to the user."""

    def setUp(self) -> None:
        super().setUp()
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self.addCleanup(clear_cache)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        self._git("config", "commit.gpgsign", "false")
        (self.root / "kept.py").write_text("one\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "first")
        # Tracked, modified, uncommitted: the one state nothing can restore.
        (self.root / "kept.py").write_text("work in progress\n", encoding="utf-8")

        self._ctx = bind_request_context(RequestContext(
            channel="websocket", chat_id="c1", session_key="websocket:c1"
        ))
        self.addCleanup(lambda: reset_request_context(self._ctx))

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True, check=False
        )

    def _delete(self, gate: _RecordingGate | None, name: str = "kept.py") -> str:
        clear_cache()
        tool = ManageFilesTool(
            workspace=self.root, allowed_dir=self.root, file_states=FileStates()
        )
        return self._run(tool.execute(action="delete", paths=[name]), gate)

    def test_an_allow_deletes_the_file(self) -> None:
        gate = _RecordingGate(allowed=True)
        out = self._delete(gate)
        self.assertIn("Deleted", out)
        self.assertFalse((self.root / "kept.py").exists())
        self.assertEqual(len(gate.seen), 1)

    def test_a_refusal_leaves_the_file_alone(self) -> None:
        out = self._delete(_RecordingGate(allowed=False))
        self.assertIn("Error", out)
        self.assertTrue((self.root / "kept.py").exists())
        self.assertEqual(
            (self.root / "kept.py").read_text(encoding="utf-8"), "work in progress\n"
        )

    def test_unattended_still_deletes_and_still_warns(self) -> None:
        out = self._delete(None)
        self.assertIn("uncommitted changes", out)
        self.assertIn("Deleted", out)
        self.assertFalse((self.root / "kept.py").exists())

    def test_a_clean_file_is_deleted_without_a_question(self) -> None:
        (self.root / "clean.py").write_text("two\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "second")
        gate = _RecordingGate(allowed=False)
        out = self._delete(gate, "clean.py")
        self.assertIn("Deleted", out)
        self.assertEqual(gate.seen, [], "a recoverable delete asked anyway")

    def test_overwriting_unsaved_work_with_a_move_asks_too(self) -> None:
        (self.root / "fresh.py").write_text("new\n", encoding="utf-8")
        clear_cache()
        tool = ManageFilesTool(
            workspace=self.root, allowed_dir=self.root, file_states=FileStates()
        )
        gate = _RecordingGate(allowed=False)
        out = self._run(
            tool.execute(
                action="move", path="fresh.py", destination="kept.py", overwrite=True
            ),
            gate,
        )
        self.assertIn("Error", out)
        self.assertEqual(len(gate.seen), 1)
        self.assertEqual(
            (self.root / "kept.py").read_text(encoding="utf-8"), "work in progress\n"
        )

    def test_the_question_says_what_is_at_stake(self) -> None:
        gate = _RecordingGate(allowed=False)
        self._delete(gate)
        request = gate.seen[0]
        self.assertEqual(request.tool, "manage_files")
        self.assertIn("kept.py", request.detail)
        self.assertIn("cannot be recovered", request.consequence)
        self.assertEqual(request.scope, "", "losing work must not be remembered")


class GitApprovalTest(_GateTest):
    """The two git commands git itself cannot undo."""

    def setUp(self) -> None:
        super().setUp()
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self.addCleanup(clear_cache)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        self._git("config", "commit.gpgsign", "false")
        (self.root / "mod.py").write_text("one\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "first")

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True, check=False
        )

    def _reset(self, gate: _RecordingGate | None) -> str:
        clear_cache()
        tool = GitTool(
            workspace=self.root, allowed_dir=self.root, file_states=FileStates()
        )
        return self._run(tool.execute(action="reset", commit="HEAD", mode="hard"), gate)

    def test_an_allow_discards_the_tree(self) -> None:
        (self.root / "mod.py").write_text("uncommitted\n", encoding="utf-8")
        gate = _RecordingGate(allowed=True)
        out = self._reset(gate)
        self.assertIn("Discarded uncommitted changes", out)
        self.assertEqual((self.root / "mod.py").read_text(encoding="utf-8"), "one\n")
        self.assertEqual(len(gate.seen), 1)

    def test_a_refusal_keeps_the_tree(self) -> None:
        (self.root / "mod.py").write_text("uncommitted\n", encoding="utf-8")
        out = self._reset(_RecordingGate(allowed=False))
        self.assertIn("refused", out)
        self.assertEqual(
            (self.root / "mod.py").read_text(encoding="utf-8"), "uncommitted\n"
        )

    def test_unattended_discards_it_as_it_always_did(self) -> None:
        (self.root / "mod.py").write_text("uncommitted\n", encoding="utf-8")
        out = self._reset(None)
        self.assertIn("Discarded uncommitted changes", out)
        self.assertEqual((self.root / "mod.py").read_text(encoding="utf-8"), "one\n")

    def test_a_clean_tree_is_reset_without_a_question(self) -> None:
        gate = _RecordingGate(allowed=False)
        out = self._reset(gate)
        self.assertNotIn("refused", out)
        self.assertEqual(gate.seen, [], "a hard reset with nothing to lose asked anyway")

    def test_a_soft_reset_is_never_a_question(self) -> None:
        (self.root / "mod.py").write_text("uncommitted\n", encoding="utf-8")
        clear_cache()
        tool = GitTool(
            workspace=self.root, allowed_dir=self.root, file_states=FileStates()
        )
        gate = _RecordingGate(allowed=False)
        self._run(tool.execute(action="reset", commit="HEAD", mode="soft"), gate)
        self.assertEqual(gate.seen, [])

    def test_the_question_quotes_the_command(self) -> None:
        (self.root / "mod.py").write_text("uncommitted\n", encoding="utf-8")
        gate = _RecordingGate(allowed=False)
        self._reset(gate)
        request = gate.seen[0]
        self.assertEqual(request.tool, "git")
        self.assertIn("git reset --hard", request.detail)
        self.assertTrue(request.allow_when_unattended)


class GitPushApprovalTest(_GateTest):
    """A force push, against a real bare remote."""

    def setUp(self) -> None:
        super().setUp()
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self.addCleanup(clear_cache)
        remote_dir = TemporaryDirectory()
        self.addCleanup(remote_dir.cleanup)
        self.remote = Path(remote_dir.name).resolve() / "remote.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", str(self.remote)],
            capture_output=True,
            check=False,
        )
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        self._git("config", "commit.gpgsign", "false")
        self._git("remote", "add", "origin", str(self.remote))
        (self.root / "mod.py").write_text("one\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "first")
        self._git("push", "-q", "--set-upstream", "origin", "main")
        # Rewrite the published commit, so a plain push would be rejected.
        (self.root / "mod.py").write_text("rewritten\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "--amend", "-m", "rewritten")

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True, check=False
        )

    def _push(self, gate: _RecordingGate | None, **kwargs: Any) -> str:
        clear_cache()
        tool = GitTool(
            workspace=self.root, allowed_dir=self.root, file_states=FileStates()
        )
        return self._run(tool.execute(action="push", force=True, **kwargs), gate)

    def _remote_log(self) -> list[str]:
        # Naming the branch matters: a bare repo's HEAD may point at an unborn
        # 'master' while everything was pushed to 'main'.
        out = subprocess.run(
            ["git", "log", "main", "--pretty=format:%s"],
            cwd=str(self.remote),
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return [line for line in out.splitlines() if line]

    def test_an_allow_overwrites_the_remote(self) -> None:
        gate = _RecordingGate(allowed=True)
        out = self._push(gate)
        self.assertNotIn("Error", out)
        self.assertEqual(self._remote_log(), ["rewritten"])
        self.assertEqual(len(gate.seen), 1)

    def test_a_refusal_leaves_the_remote_alone(self) -> None:
        out = self._push(_RecordingGate(allowed=False))
        self.assertIn("refused", out)
        self.assertEqual(self._remote_log(), ["first"])

    def test_unattended_pushes_as_it_always_did(self) -> None:
        out = self._push(None)
        self.assertNotIn("Error", out)
        self.assertEqual(self._remote_log(), ["rewritten"])

    def test_a_plain_push_is_never_a_question(self) -> None:
        self._git("reset", "-q", "--hard", "origin/main")
        (self.root / "extra.py").write_text("two\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "second")
        clear_cache()
        tool = GitTool(
            workspace=self.root, allowed_dir=self.root, file_states=FileStates()
        )
        gate = _RecordingGate(allowed=False)
        out = self._run(tool.execute(action="push"), gate)
        self.assertNotIn("refused", out)
        self.assertEqual(gate.seen, [])

    def test_a_lease_force_can_be_remembered_but_a_blind_one_cannot(self) -> None:
        gate = _RecordingGate(allowed=False)
        self._push(gate)
        self.assertEqual(gate.seen[0].scope, "git:push-force")

        gate = _RecordingGate(allowed=False)
        self._push(gate, stale=True)
        self.assertEqual(gate.seen[0].scope, "")
        self.assertIn("someone else", gate.seen[0].reason)


if __name__ == "__main__":
    unittest.main()
