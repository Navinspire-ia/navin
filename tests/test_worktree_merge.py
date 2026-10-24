# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A Multitask subagent's work lands in the user's tree, or nothing moves.

The merge is automatic only when it is provably safe: conflict-free, and no
touched file carries uncommitted user edits. Every other case must leave the
user's tree byte for byte as it was and keep the subagent's work findable.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from navin.agent import worktree, worktree_merge
from navin.agent.runner import AgentRunResult
from navin.agent.subagent import SubagentManager
from navin.bus.outbound_events import WorktreeMergeEvent


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file in the user's tree (outside .git) with its bytes."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.root.mkdir()
        git(["init", "-b", "main"], self.root)
        git(["config", "user.email", "test@example.com"], self.root)
        git(["config", "user.name", "Test"], self.root)
        (self.root / "a.py").write_text("x = 1\ny = 2\n")
        (self.root / "b.py").write_text("b = 1\n")
        git(["add", "."], self.root)
        git(["commit", "-m", "first"], self.root)
        self.store = Path(self._tmp.name) / "worktrees"
        patcher = unittest.mock.patch.object(worktree, "worktrees_root", lambda: self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(worktree.clear_pool)
        self.addCleanup(worktree_merge.clear_pending)
        self.git_calls: list[list[str]] = []
        real_run = worktree_merge._run

        def spy(args, **kwargs):
            self.git_calls.append(list(args))
            return real_run(args, **kwargs)

        spy_patch = unittest.mock.patch.object(worktree_merge, "_run", spy)
        spy_patch.start()
        self.addCleanup(spy_patch.stop)

    def checkout(self, task_id: str) -> worktree.Worktree:
        made = worktree.create(self.root, task_id, pool_size=0)
        assert made is not None
        self.addCleanup(lambda: worktree.remove(made) if made.path.exists() else None)
        return made

    def head(self) -> str:
        return git(["rev-parse", "HEAD"], self.root).strip()

    def assert_no_force(self) -> None:
        for args in self.git_calls:
            self.assertNotIn("--force", args, args)
            self.assertNotIn("-f", args, args)
            self.assertNotIn("--hard", args, args)
            self.assertNotEqual(args[:1], ["reset"], args)
            self.assertNotEqual(args[:1], ["checkout"], args)


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class LandTest(_Base):
    def test_a_clean_merge_lands_in_the_users_tree(self) -> None:
        tree = self.checkout("t1")
        (tree.path / "a.py").write_text("x = 1\ny = 3\n")
        (tree.path / "new.py").write_text("n = 1\n")
        before = self.head()
        landed = worktree_merge.land(tree, "edit a")
        assert landed is not None
        result, _commit = landed
        self.assertEqual(result.status, "merged")
        self.assertEqual(result.files, ("a.py", "new.py"))
        self.assertEqual((self.root / "a.py").read_text(), "x = 1\ny = 3\n")
        self.assertEqual((self.root / "new.py").read_text(), "n = 1\n")
        # A real merge commit on the user's branch, the old tip as first parent.
        parents = git(["rev-list", "--parents", "-n", "1", "HEAD"], self.root).split()
        self.assertEqual(parents[1], before)
        self.assertEqual(len(parents), 3)
        self.assertEqual(git(["rev-parse", "--abbrev-ref", "HEAD"], self.root).strip(), "main")
        # Index and files agree with the new HEAD.
        self.assertEqual(git(["status", "--porcelain"], self.root), "")
        self.assert_no_force()

    def test_a_conflict_aborts_with_zero_changes_to_the_user_tree(self) -> None:
        tree = self.checkout("t2")
        (tree.path / "a.py").write_text("x = 1\ny = 'theirs'\n")
        # The user committed a different edit to the same line meanwhile.
        (self.root / "a.py").write_text("x = 1\ny = 'mine'\n")
        git(["commit", "-am", "mine"], self.root)
        before_head, before_files = self.head(), snapshot(self.root)
        before_index = git(["ls-files", "-s"], self.root)
        landed = worktree_merge.land(tree, "edit a")
        assert landed is not None
        result, _commit = landed
        self.assertEqual(result.status, "conflict")
        self.assertEqual(result.conflicts, ("a.py",))
        self.assertEqual(self.head(), before_head)
        self.assertEqual(snapshot(self.root), before_files)
        self.assertEqual(git(["ls-files", "-s"], self.root), before_index)
        self.assertEqual(git(["status", "--porcelain"], self.root), "")
        self.assertFalse((self.root / ".git" / "MERGE_HEAD").exists())
        self.assert_no_force()

    def test_uncommitted_user_edits_on_the_same_file_block(self) -> None:
        tree = self.checkout("t3")
        (tree.path / "b.py").write_text("b = 2\n")
        (self.root / "b.py").write_text("b = 'user wip'\n")
        before_head, before_files = self.head(), snapshot(self.root)
        result, _commit = worktree_merge.land(tree, "edit b")
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "uncommitted")
        self.assertEqual(result.conflicts, ("b.py",))
        self.assertEqual(self.head(), before_head)
        self.assertEqual(snapshot(self.root), before_files)

    def test_an_untracked_user_file_at_the_same_path_blocks(self) -> None:
        tree = self.checkout("t3b")
        (tree.path / "notes.md").write_text("theirs\n")
        (self.root / "notes.md").write_text("mine, never committed\n")
        result, _commit = worktree_merge.land(tree, "notes")
        self.assertEqual(result.status, "blocked")
        self.assertEqual((self.root / "notes.md").read_text(), "mine, never committed\n")

    def test_unrelated_uncommitted_edits_are_allowed_and_kept(self) -> None:
        tree = self.checkout("t4")
        (tree.path / "a.py").write_text("x = 1\ny = 4\n")
        (self.root / "b.py").write_text("b = 'user wip'\n")
        (self.root / "scratch.txt").write_text("draft\n")
        git(["add", "scratch.txt"], self.root)  # staged, also unrelated
        result, _commit = worktree_merge.land(tree, "edit a")
        self.assertEqual(result.status, "merged")
        self.assertEqual((self.root / "a.py").read_text(), "x = 1\ny = 4\n")
        self.assertEqual((self.root / "b.py").read_text(), "b = 'user wip'\n")
        self.assertEqual((self.root / "scratch.txt").read_text(), "draft\n")
        status = git(["status", "--porcelain"], self.root)
        self.assertIn(" M b.py", status)
        self.assertIn("A  scratch.txt", status)

    def test_an_in_progress_merge_blocks(self) -> None:
        tree = self.checkout("t5")
        (tree.path / "a.py").write_text("x = 9\ny = 2\n")
        (self.root / ".git" / "MERGE_HEAD").write_text(self.head() + "\n")
        result, _commit = worktree_merge.land(tree, "edit a")
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "in_progress")

    def test_nothing_tracked_to_land_returns_none(self) -> None:
        tree = self.checkout("t6")
        self.assertIsNone(worktree_merge.land(tree, "noop"))

    def test_concurrent_finishes_merge_serially_under_a_repo_lock(self) -> None:
        trees = [self.checkout(f"c{i}") for i in range(4)]
        for index, tree in enumerate(trees):
            (tree.path / f"f{index}.py").write_text(f"v = {index}\n")
        lock = worktree_merge._repo_lock(self.root)
        active = {"now": 0, "max": 0}
        guard = threading.Lock()
        real = worktree_merge._touched

        def tracked(*args):
            with guard:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            try:
                self.assertTrue(lock.locked())
                return real(*args)
            finally:
                with guard:
                    active["now"] -= 1

        results: list = []
        with unittest.mock.patch.object(worktree_merge, "_touched", tracked):
            threads = [
                threading.Thread(target=lambda t=t: results.append(worktree_merge.land(t, t.task_id)))
                for t in trees
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(active["max"], 1)
        self.assertEqual(sorted(r[0].status for r in results), ["merged"] * 4)
        for index in range(4):
            self.assertEqual((self.root / f"f{index}.py").read_text(), f"v = {index}\n")
        self.assertEqual(git(["status", "--porcelain"], self.root), "")
        self.assert_no_force()

    def test_two_subagents_editing_the_same_line_second_one_conflicts(self) -> None:
        first, second = self.checkout("s1"), self.checkout("s2")
        (first.path / "a.py").write_text("x = 1\ny = 'first'\n")
        (second.path / "a.py").write_text("x = 1\ny = 'second'\n")
        self.assertEqual(worktree_merge.land(first, "one")[0].status, "merged")
        after_first = snapshot(self.root)
        self.assertEqual(worktree_merge.land(second, "two")[0].status, "conflict")
        self.assertEqual(snapshot(self.root), after_first)


class _Bus:
    def __init__(self) -> None:
        self.published: list = []
        self.outbound = asyncio.Queue()

    async def publish_inbound(self, msg) -> None:
        self.published.append(msg)


class _WritingRunner:
    """Writes into whatever workspace the subagent was given, then finishes."""

    def __init__(self, name: str, content: str) -> None:
        self.name, self.content = name, content

    async def run(self, spec) -> AgentRunResult:
        Path(spec.workspace, self.name).write_text(self.content)
        return AgentRunResult(final_content="did the work", messages=[])


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class SubagentIntegrationTest(_Base, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _Base.setUp(self)
        self.bus = _Bus()
        self.manager = SubagentManager(workspace=self.root, bus=self.bus, max_tool_result_chars=4000)

    async def _spawn(self, runner) -> tuple[str, list]:
        self.manager.runner = runner
        await self.manager.spawn(
            task="edit something", runtime=unittest.mock.Mock(), origin_channel="websocket",
            origin_chat_id="c1", session_key="websocket:c1", isolate=True, label="Edit",
        )
        await asyncio.gather(*list(self.manager._running_tasks.values()))
        events = []
        while not self.bus.outbound.empty():
            event = self.bus.outbound.get_nowait().event
            if isinstance(event, WorktreeMergeEvent):
                events.append(event)
        return self.bus.published[-1].content, events

    async def test_a_finished_multitask_lands_and_the_announcement_says_so(self) -> None:
        announcement, events = await self._spawn(_WritingRunner("a.py", "x = 'merged'\n"))
        self.assertEqual((self.root / "a.py").read_text(), "x = 'merged'\n")
        self.assertIn("Merged automatically", announcement)
        self.assertIn("1 file changed", announcement)
        self.assertEqual([e.status for e in events], ["merged"])
        self.assertEqual(events[0].files, ["a.py"])

    async def test_a_clash_with_user_edits_is_handed_to_the_agent_without_a_question(self) -> None:
        (self.root / "a.py").write_text("user wip\n")
        before = snapshot(self.root)
        announcement, events = await self._spawn(_WritingRunner("a.py", "x = 'theirs'\n"))
        self.assertEqual(snapshot(self.root), before)  # nothing landed over the user's edits
        self.assertIn("Not merged automatically", announcement)
        self.assertIn("Integrate it now yourself, without asking the user", announcement)
        self.assertIn("never discard or overwrite the user's uncommitted work", announcement)
        self.assertNotIn("Resolve, Keep separate or Discard", announcement.split("Summarize this")[0])
        self.assertEqual([e.status for e in events], ["resolving"])
        self.assertEqual(events[0].conflicts, ["a.py"])
        branch = events[0].branch
        self.assertTrue(branch.startswith(worktree_merge.BRANCH_PREFIX))
        self.assertIn(branch, announcement)
        # The work is safe on its branch; no card waits for the user.
        self.assertIn("x = 'theirs'", git(["show", f"{branch}:a.py"], self.root))
        self.assertEqual(worktree_merge.pending_for_session("websocket:c1"), [])

    async def test_a_merge_the_user_has_in_progress_still_offers_actions(self) -> None:
        (self.root / ".git" / "MERGE_HEAD").write_text(self.head() + "\n")
        before = snapshot(self.root)
        announcement, events = await self._spawn(_WritingRunner("a.py", "x = 'theirs'\n"))
        self.assertEqual(snapshot(self.root), before)
        self.assertIn("could not be merged automatically", announcement)
        self.assertIn("Resolve, Keep separate or Discard", announcement.split("Summarize this")[0])
        self.assertIn(events[0].status, {"blocked", "unknown"})  # a card for the user
        self.assertEqual(events[0].reason, "in_progress")
        self.assertTrue(events[0].branch.startswith(worktree_merge.BRANCH_PREFIX))
        pending = worktree_merge.pending_for_session("websocket:c1")
        self.assertEqual(len(pending), 1)
        task_id = pending[0]["task_id"]
        # Another conversation cannot act on it.
        self.assertFalse(worktree_merge.apply_action(task_id, "discard", session_key="websocket:other")["ok"])
        kept = worktree_merge.get_pending(task_id)
        assert kept is not None
        self.assertTrue(kept.checkout.path.exists())
        answer = worktree_merge.apply_action(task_id, "discard", session_key="websocket:c1")
        self.assertEqual(answer["status"], "discarded")
        self.assertFalse(kept.checkout.path.exists())
        self.assertEqual(git(["branch", "--list", events[0].branch], self.root).strip(), "")
        self.assertEqual(snapshot(self.root), before)

    async def test_resolve_returns_a_prompt_and_keep_leaves_the_branch(self) -> None:
        (self.root / ".git" / "MERGE_HEAD").write_text(self.head() + "\n")
        _announcement, events = await self._spawn(_WritingRunner("b.py", "b = 3\n"))
        task_id = events[0].task_id
        resolved = worktree_merge.apply_action(task_id, "resolve", session_key="websocket:c1")
        self.assertEqual(resolved["status"], "resolving")
        self.assertIn(events[0].branch, resolved["resolve_prompt"])
        self.assertFalse(worktree_merge.apply_action(task_id, "keep")["ok"])  # already answered

    async def test_a_failed_run_is_never_merged(self) -> None:
        class _Failing(_WritingRunner):
            async def run(self, spec) -> AgentRunResult:
                Path(spec.workspace, self.name).write_text(self.content)
                return AgentRunResult(final_content=None, messages=[], stop_reason="error",
                                      error="boom")

        announcement, events = await self._spawn(_Failing("a.py", "half done\n"))
        self.assertEqual((self.root / "a.py").read_text(), "x = 1\ny = 2\n")
        self.assertEqual(events, [])
        self.assertIn("isolated checkout", announcement)


class WebsocketActionTest(unittest.IsolatedAsyncioTestCase):
    async def _dispatch(self, envelope: dict, *, attached: bool = True, role: str = "owner"):
        from types import SimpleNamespace

        from navin.channels.websocket import WebSocketChannel

        sent_events: list = []
        pushed: list = []

        async def send_event(_conn, event, **fields):
            sent_events.append((event, fields))

        async def send_merge(chat_id, event):
            pushed.append((chat_id, event))

        fake = SimpleNamespace(
            _send_event=send_event, send_worktree_merge=send_merge,
            _conn_chats={"conn": {"c1"} if attached else set()},
            _role_for=lambda _conn: role,
        )
        await WebSocketChannel._dispatch_worktree_merge_action(fake, "conn", envelope)  # type: ignore[arg-type]
        return sent_events, pushed

    async def test_an_action_is_scoped_to_the_attached_chat_session(self) -> None:
        calls: list = []

        def fake_apply(task_id, action, *, session_key=None):
            calls.append((task_id, action, session_key))
            return {"ok": True, "status": "kept", "label": "L", "branch": "navin/multitask/x"}

        with unittest.mock.patch.object(worktree_merge, "apply_action", fake_apply):
            errors, pushed = await self._dispatch(
                {"type": "worktree_merge_action", "chat_id": "c1", "task_id": "t1", "action": "keep"},
            )
        self.assertEqual(errors, [])
        self.assertEqual(calls, [("t1", "keep", "websocket:c1")])
        self.assertEqual(pushed[0][1].status, "kept")

    async def test_unattached_or_unknown_actions_are_refused(self) -> None:
        with unittest.mock.patch.object(worktree_merge, "apply_action") as apply:
            errors, _ = await self._dispatch(
                {"chat_id": "c1", "task_id": "t1", "action": "keep"}, attached=False,
            )
            self.assertEqual(errors[0][0], "error")
            errors, _ = await self._dispatch({"chat_id": "c1", "task_id": "t1", "action": "force"})
            self.assertEqual(errors[0][0], "error")
            apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
