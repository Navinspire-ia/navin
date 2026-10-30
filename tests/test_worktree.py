# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A subagent given its own checkout must not touch the one the user is in.

Concurrent subagents shared a working tree, so two of them editing the same file
interleaved their writes and recorded checkpoint baselines describing a state
neither produced. These cover the checkout that fixes it, and the two ways it
can go wrong in the opposite direction: silently discarding work the subagent
did, or leaving an empty checkout behind on every run.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from navin.agent import worktree


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class WorktreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.root.mkdir()
        git(["init", "-b", "main"], self.root)
        git(["config", "user.email", "test@example.com"], self.root)
        git(["config", "user.name", "Test"], self.root)
        (self.root / "a.py").write_text("x = 1\n")
        git(["add", "."], self.root)
        git(["commit", "-m", "first"], self.root)

        # Keep every checkout inside the temp dir, so a failure cannot litter a
        # real ~/.navin/worktrees.
        self.store = Path(self._tmp.name) / "worktrees"
        patcher = unittest.mock.patch.object(worktree, "worktrees_root", lambda: self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create(self, task_id: str = "abc123") -> worktree.Worktree:
        checkout = worktree.create(self.root, task_id)
        assert checkout is not None
        self.addCleanup(lambda: worktree.remove(checkout))
        return checkout

    def test_the_checkout_has_the_repository_content(self) -> None:
        checkout = self._create()
        self.assertTrue(checkout.path.is_dir())
        self.assertEqual((checkout.path / "a.py").read_text(), "x = 1\n")

    def test_it_lives_outside_the_project(self) -> None:
        # A checkout nested in the repository is something every search tool
        # then has to be taught to skip.
        checkout = self._create()
        self.assertNotIn(self.root, checkout.path.parents)

    def test_an_edit_there_does_not_reach_the_users_tree(self) -> None:
        checkout = self._create()
        (checkout.path / "a.py").write_text("x = 2\n")
        self.assertEqual((self.root / "a.py").read_text(), "x = 1\n")

    def test_the_users_branch_is_not_moved(self) -> None:
        # Detached on purpose: a subagent must never be able to move a branch
        # the user has checked out.
        before = git(["rev-parse", "--abbrev-ref", "HEAD"], self.root).stdout.strip()
        self._create()
        after = git(["rev-parse", "--abbrev-ref", "HEAD"], self.root).stdout.strip()
        self.assertEqual(before, "main")
        self.assertEqual(after, "main")

    def test_two_subagents_get_separate_trees(self) -> None:
        first = self._create("aaa")
        second = self._create("bbb")
        self.assertNotEqual(first.path, second.path)
        (first.path / "a.py").write_text("first\n")
        (second.path / "a.py").write_text("second\n")
        self.assertEqual((first.path / "a.py").read_text(), "first\n")
        self.assertEqual((second.path / "a.py").read_text(), "second\n")

    def test_a_modified_file_is_reported(self) -> None:
        checkout = self._create()
        (checkout.path / "a.py").write_text("x = 2\n")
        changes = checkout.inspect()
        assert changes is not None
        self.assertFalse(changes.empty)
        self.assertIn("a.py", changes.render())

    def test_a_brand_new_file_is_reported_too(self) -> None:
        # Untracked files are the usual output of "write me a module", and a
        # plain `git diff` would not mention them.
        checkout = self._create()
        (checkout.path / "new.py").write_text("y = 2\n")
        changes = checkout.inspect()
        assert changes is not None
        self.assertIn("new.py", changes.render())

    def test_work_the_subagent_committed_still_counts(self) -> None:
        # Committing leaves the tree clean, so a diff against the checkout's own
        # HEAD sees nothing and the caller would delete the only copy.
        checkout = self._create()
        (checkout.path / "a.py").write_text("x = 2\n")
        git(["add", "."], checkout.path)
        git(["commit", "-m", "subagent work"], checkout.path)
        changes = checkout.inspect()
        assert changes is not None
        self.assertFalse(changes.empty)
        self.assertEqual(changes.commits, 1)
        self.assertIn("1 commit", changes.render())

    def test_output_the_project_ignores_still_counts(self) -> None:
        # A build artefact or generated dataset is invisible to `git add`, and
        # deleting the checkout would be the only record of it disappearing.
        # Reported at directory granularity, which is all that is needed to
        # know not to delete, and avoids walking a node_modules to say so.
        (self.root / ".gitignore").write_text("build/\n")
        git(["add", "."], self.root)
        git(["commit", "-m", "ignore build"], self.root)
        checkout = self._create()
        (checkout.path / "build").mkdir()
        (checkout.path / "build" / "out.bin").write_text("artefact\n")
        changes = checkout.inspect()
        assert changes is not None
        self.assertFalse(changes.empty)
        self.assertIn("build/", changes.render())

    def test_work_committed_then_tidied_away_still_counts(self) -> None:
        # The subagent commits, then puts HEAD back where it found it - a
        # reasonable thing for an agent that wants to leave a clean tree. The
        # commit still exists, but no comparison of before and after can see it,
        # and removing the checkout makes it unreachable for the next `git gc`.
        checkout = self._create()
        (checkout.path / "a.py").write_text("x = 2\n")
        git(["add", "."], checkout.path)
        git(["commit", "-m", "subagent work"], checkout.path)
        git(["checkout", "--detach", checkout.base], checkout.path)
        changes = checkout.inspect()
        assert changes is not None
        self.assertFalse(changes.empty)
        self.assertTrue(changes.moved)

    def test_a_repository_without_reflogs_reports_unknown(self) -> None:
        # Without a reflog there is no way to tell a commit that was made and
        # undone from a checkout nobody touched, and the two call for opposite
        # decisions. Leaving a checkout behind is the tolerable half.
        git(["config", "core.logAllRefUpdates", "false"], self.root)
        checkout = self._create()
        git(["reflog", "expire", "--expire=now", "--all"], checkout.path)
        self.assertIsNone(checkout.inspect())

    def test_a_hook_that_dirties_every_new_checkout_is_not_blamed_on_the_subagent(
        self,
    ) -> None:
        # Plenty of repositories generate a local config or marker on checkout.
        # Counted as work, no isolated run on such a repository would ever be
        # cleaned up, and every result would claim there was something to merge.
        (self.root / ".gitignore").write_text("generated.txt\n")
        git(["add", "."], self.root)
        git(["commit", "-m", "ignore generated"], self.root)
        hooks = self.root / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "post-checkout"
        hook.write_text("#!/bin/sh\necho made-by-hook > generated.txt\n")
        hook.chmod(0o755)

        checkout = self._create()
        self.assertTrue((checkout.path / "generated.txt").is_file())
        changes = checkout.inspect()
        assert changes is not None
        self.assertTrue(changes.empty)

    def test_a_deliverable_inside_a_hook_created_directory_is_not_subtracted(self) -> None:
        # The status line for a directory does not change when its contents do,
        # so comparing lines at directory granularity would subtract the
        # subagent's output along with the hook's and delete the only copy.
        (self.root / ".gitignore").write_text("build/\n")
        git(["add", "."], self.root)
        git(["commit", "-m", "ignore build"], self.root)
        hooks = self.root / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "post-checkout"
        hook.write_text("#!/bin/sh\nmkdir -p build\necho hook > build/hook.bin\n")
        hook.chmod(0o755)

        checkout = self._create()
        self.assertTrue((checkout.path / "build" / "hook.bin").is_file())
        (checkout.path / "build" / "report.bin").write_text("deliverable\n")
        changes = checkout.inspect()
        assert changes is not None
        self.assertFalse(changes.empty)
        self.assertIn("report.bin", changes.render())

    def test_editing_the_very_file_a_hook_wrote_still_counts(self) -> None:
        # The status line is identical before and after, so subtracting by line
        # alone would erase the edit from the report and then delete the tree
        # holding it. Local config a hook seeds and the task then adjusts is the
        # everyday shape of this.
        (self.root / ".gitignore").write_text("config.local\n")
        git(["add", "."], self.root)
        git(["commit", "-m", "ignore local config"], self.root)
        hooks = self.root / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "post-checkout"
        hook.write_text("#!/bin/sh\necho seeded > config.local\n")
        hook.chmod(0o755)

        checkout = self._create()
        (checkout.path / "config.local").write_text("adjusted by the subagent\n")
        changes = checkout.inspect()
        assert changes is not None
        self.assertFalse(changes.empty)
        self.assertIn("config.local", changes.render())

    def test_an_untouched_checkout_reports_nothing(self) -> None:
        changes = self._create().inspect()
        assert changes is not None
        self.assertTrue(changes.empty)

    def test_inspecting_does_not_stage_anything(self) -> None:
        # A report that mutates the index breaks `git stash` and makes a plain
        # `git commit` refuse, which is exactly the workflow the result note
        # tells the user to follow.
        checkout = self._create()
        (checkout.path / "new.py").write_text("y = 2\n")
        checkout.inspect()
        staged = git(["diff", "--cached", "--name-only"], checkout.path).stdout
        self.assertEqual(staged.strip(), "")

    def test_a_repository_git_cannot_read_reports_unknown_not_empty(self) -> None:
        # None means "could not find out". Returning an empty result here would
        # tell the caller it is safe to delete.
        checkout = self._create()
        with unittest.mock.patch.object(worktree, "_git", return_value=None):
            self.assertIsNone(checkout.inspect())

    def test_removal_leaves_no_trace(self) -> None:
        checkout = worktree.create(self.root, "gone")
        assert checkout is not None
        worktree.remove(checkout)
        self.assertFalse(checkout.path.exists())
        listed = git(["worktree", "list"], self.root).stdout
        self.assertNotIn("gone", listed)
        # Including the per-repository folder, which would otherwise be left
        # empty behind every project that ever isolated a subagent.
        self.assertFalse(checkout.path.parent.exists())


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class WorktreePoolTest(unittest.TestCase):
    """Empty checkouts must be reusable, not rebuilt from scratch each spawn."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.root.mkdir()
        git(["init", "-b", "main"], self.root)
        git(["config", "user.email", "test@example.com"], self.root)
        git(["config", "user.name", "Test"], self.root)
        (self.root / "a.py").write_text("x = 1\n")
        git(["add", "."], self.root)
        git(["commit", "-m", "first"], self.root)
        self.store = Path(self._tmp.name) / "worktrees"
        patcher = unittest.mock.patch.object(worktree, "worktrees_root", lambda: self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(worktree.clear_pool)

    def test_an_empty_checkout_is_reused_from_the_pool(self) -> None:
        first = worktree.create(self.root, "one", pool_size=2)
        assert first is not None
        self.assertTrue(worktree.release(first, pool_size=2))
        second = worktree.create(self.root, "two", pool_size=2)
        assert second is not None
        self.assertEqual(first.path, second.path)
        self.assertEqual(second.task_id, "two")
        worktree.remove(second)

    def test_release_resets_edits_before_reuse(self) -> None:
        first = worktree.create(self.root, "dirty", pool_size=2)
        assert first is not None
        (first.path / "a.py").write_text("x = 9\n")
        self.assertTrue(worktree.release(first, pool_size=2))
        second = worktree.create(self.root, "clean", pool_size=2)
        assert second is not None
        self.assertEqual((second.path / "a.py").read_text(), "x = 1\n")
        worktree.remove(second)

    def test_the_pool_is_capped(self) -> None:
        first = worktree.create(self.root, "a", pool_size=1)
        second = worktree.create(self.root, "b", pool_size=1)
        assert first is not None and second is not None
        self.assertTrue(worktree.release(first, pool_size=1))
        # Second release exceeds the cap and must remove rather than park.
        path_b = second.path
        self.assertFalse(worktree.release(second, pool_size=1))
        self.assertFalse(path_b.exists())
        # The one pooled entry is still reusable.
        third = worktree.create(self.root, "c", pool_size=1)
        assert third is not None
        self.assertEqual(third.path, first.path)
        worktree.remove(third)

    def test_pool_size_zero_always_removes(self) -> None:
        first = worktree.create(self.root, "a", pool_size=0)
        assert first is not None
        path = first.path
        self.assertFalse(worktree.release(first, pool_size=0))
        self.assertFalse(path.exists())


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
# Platform guard, not a shortcut: Windows checkouts take the no-link path by
# design, which NoDependencyLinkTest checks on every platform.
@unittest.skipUnless(worktree._LINKS_SUPPORTED, "no folder links on this platform")
class DependencyLinkTest(unittest.TestCase):
    """A checkout borrows the user's node_modules instead of arriving without.

    Tracked files only, a JS checkout had no vitest, tsc or vite: the
    subagent's checks could not run, so it was stopped as unvalidated for a
    reason that had nothing to do with its change.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        web = self.root / "web"
        web.mkdir(parents=True)
        git(["init", "-b", "main"], self.root)
        git(["config", "user.email", "test@example.com"], self.root)
        git(["config", "user.name", "Test"], self.root)
        (web / "package.json").write_text("{}\n")
        (web / ".gitignore").write_text("node_modules/\n")
        (web / "app.js").write_text("export const x = 1\n")
        # A package whose node_modules this project does not ignore.
        (self.root / "lib").mkdir()
        (self.root / "lib" / "package.json").write_text("{}\n")
        # A workspace package, linked from web/node_modules like npm does.
        shared = self.root / "packages" / "shared"
        shared.mkdir(parents=True)
        (shared / "package.json").write_text("{}\n")
        (shared / "index.js").write_text("export const v = 'committed'\n")
        git(["add", "."], self.root)
        git(["commit", "-m", "first"], self.root)

        modules = web / "node_modules"
        (modules / "react").mkdir(parents=True)
        (modules / "react" / "index.js").write_text("module.exports = 'react'\n")
        (modules / "@scope" / "pkg").mkdir(parents=True)
        (modules / "@scope" / "pkg" / "index.js").write_text("scoped\n")
        (modules / ".bin").mkdir()
        (modules / ".bin" / "tool").write_text("#!/bin/sh\necho tool\n")
        (modules / ".vite" / "deps").mkdir(parents=True)
        (modules / ".vite" / "deps" / "cache.js").write_text("user cache\n")
        (self.root / "lib" / "node_modules" / "left").mkdir(parents=True)
        (modules / "shared").symlink_to(Path("..") / ".." / "packages" / "shared")
        (modules / "@scope" / "shared").symlink_to(shared)
        # Only in the user's tree, so the checkout has no copy of it.
        local = self.root / "packages" / "local-only"
        local.mkdir()
        (local / "index.js").write_text("local\n")
        (modules / "local-only").symlink_to(local)
        self.modules = modules

        self.store = Path(self._tmp.name) / "worktrees"
        patcher = unittest.mock.patch.object(worktree, "worktrees_root", lambda: self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(worktree.clear_pool)

    def _create(self, task_id: str = "deps", pool_size: int = 0) -> worktree.Worktree:
        checkout = worktree.create(self.root, task_id, pool_size=pool_size)
        assert checkout is not None
        return checkout

    def _users_packages_intact(self) -> None:
        self.assertEqual((self.modules / "react" / "index.js").read_text(), "module.exports = 'react'\n")
        self.assertEqual((self.modules / "@scope" / "pkg" / "index.js").read_text(), "scoped\n")
        self.assertTrue((self.modules / ".bin" / "tool").is_file())
        self.assertEqual((self.modules / ".vite" / "deps" / "cache.js").read_text(), "user cache\n")

    def test_installed_packages_are_reachable_from_the_checkout(self) -> None:
        checkout = self._create()
        self.addCleanup(lambda: worktree.remove(checkout))
        linked = checkout.path / "web" / "node_modules"
        self.assertTrue(linked.is_dir())
        self.assertFalse(linked.is_symlink())
        self.assertTrue((linked / "react").is_symlink())
        self.assertEqual((linked / "react").resolve(), (self.modules / "react").resolve())
        self.assertEqual((linked / "react" / "index.js").read_text(), "module.exports = 'react'\n")
        # Split one level down, so an install there lands in the checkout.
        self.assertFalse((linked / "@scope").is_symlink())
        self.assertTrue((linked / "@scope" / "pkg").is_symlink())
        self.assertFalse((linked / ".bin").is_symlink())
        self.assertTrue((linked / ".bin" / "tool").is_symlink())
        # The user's vite cache stays theirs.
        self.assertFalse((linked / ".vite").exists())
        self.assertEqual(checkout.linked(), ("web/node_modules",))

    def test_a_workspace_package_resolves_to_the_checkouts_copy(self) -> None:
        # A test in the checkout must import the package as the subagent
        # edited it, not the user's version of it.
        checkout = self._create()
        self.addCleanup(lambda: worktree.remove(checkout))
        mine = checkout.path / "packages" / "shared"
        linked = checkout.path / "web" / "node_modules"
        self.assertEqual((linked / "shared").resolve(), mine.resolve())
        self.assertEqual((linked / "@scope" / "shared").resolve(), mine.resolve())
        (mine / "index.js").write_text("export const v = 'edited'\n")
        self.assertEqual((linked / "shared" / "index.js").read_text(), "export const v = 'edited'\n")
        self.assertEqual(
            (self.root / "packages" / "shared" / "index.js").read_text(), "export const v = 'committed'\n",
        )
        # Not carried by the checkout: the user's copy, never a dangling link.
        self.assertEqual((linked / "local-only" / "index.js").read_text(), "local\n")

    def test_a_folder_the_project_would_commit_is_not_linked(self) -> None:
        checkout = self._create()
        self.addCleanup(lambda: worktree.remove(checkout))
        self.assertFalse((checkout.path / "lib" / "node_modules").exists())

    def test_the_links_never_reach_a_commit(self) -> None:
        checkout = self._create()
        self.addCleanup(lambda: worktree.remove(checkout))
        git(["add", "-A"], checkout.path)
        staged = git(["diff", "--cached", "--name-only"], checkout.path).stdout
        self.assertEqual(staged.strip(), "")

    def test_the_links_and_caches_beside_them_are_not_work(self) -> None:
        checkout = self._create()
        self.addCleanup(lambda: worktree.remove(checkout))
        cache = checkout.path / "web" / "node_modules" / ".vite" / "vitest"
        cache.mkdir(parents=True)
        (cache / "results.json").write_text("{}\n")
        changes = checkout.inspect()
        assert changes is not None
        self.assertTrue(changes.empty, changes.render())
        # Real work next to them still counts.
        (checkout.path / "web" / "app.js").write_text("export const x = 2\n")
        changes = checkout.inspect()
        assert changes is not None
        self.assertEqual([line.split()[-1] for line in changes.entries], ["web/app.js"])

    def test_a_resumed_checkout_still_knows_its_links(self) -> None:
        # After a restart the checkout is rebuilt with no creation snapshot
        # and the file-by-file listing, which names every link.
        made = self._create()
        self.addCleanup(lambda: worktree.remove(made))
        resumed = worktree.Worktree(
            path=made.path, origin=made.origin, task_id=made.task_id,
            base=made.base, initial=(), precise=True,
        )
        changes = resumed.inspect()
        assert changes is not None
        self.assertTrue(changes.empty, changes.render())

    def test_deleting_the_folder_in_the_checkout_keeps_the_users_packages(self) -> None:
        checkout = self._create()
        self.addCleanup(lambda: worktree.remove(checkout))
        subprocess.run(["rm", "-rf", "web/node_modules/"], cwd=checkout.path, check=True)
        self._users_packages_intact()

    def test_removing_the_checkout_keeps_the_users_packages(self) -> None:
        checkout = self._create()
        worktree.remove(checkout)
        self.assertFalse(checkout.path.exists())
        self._users_packages_intact()

    def test_a_pooled_checkout_is_cleaned_then_linked_again(self) -> None:
        first = self._create("one", pool_size=2)
        # git clean -fdx runs on release: it must drop the links, not follow them.
        self.assertTrue(worktree.release(first, pool_size=2))
        self._users_packages_intact()
        second = self._create("two", pool_size=2)
        self.addCleanup(lambda: worktree.remove(second))
        self.assertEqual(second.path, first.path)
        self.assertTrue((second.path / "web" / "node_modules" / "react").is_symlink())
        changes = second.inspect()
        assert changes is not None
        self.assertTrue(changes.empty, changes.render())


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class NoDependencyLinkTest(unittest.TestCase):
    """Where folder links are off (Windows), a checkout is made exactly as before."""

    def test_nothing_is_linked_and_nothing_is_hidden(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            (root / "web").mkdir(parents=True)
            git(["init", "-b", "main"], root)
            git(["config", "user.email", "test@example.com"], root)
            git(["config", "user.name", "Test"], root)
            (root / "web" / "package.json").write_text("{}\n")
            (root / "web" / ".gitignore").write_text("node_modules/\n")
            git(["add", "."], root)
            git(["commit", "-m", "first"], root)
            (root / "web" / "node_modules" / "react").mkdir(parents=True)
            store = Path(tmp) / "worktrees"
            with (
                unittest.mock.patch.object(worktree, "worktrees_root", lambda: store),
                unittest.mock.patch.object(worktree, "_LINKS_SUPPORTED", False),
            ):
                checkout = worktree.create(root, "nolinks", pool_size=0)
                assert checkout is not None
                try:
                    self.assertFalse((checkout.path / "web" / "node_modules").exists())
                    self.assertEqual(checkout.linked(), ())
                    # A folder the subagent creates there still counts as its output.
                    (checkout.path / "web" / "new.js").write_text("x\n")
                    changes = checkout.inspect()
                    assert changes is not None
                    self.assertFalse(changes.empty)
                finally:
                    worktree.remove(checkout)
            self.assertTrue((root / "web" / "node_modules" / "react").is_dir())


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class UnsupportedWorkspaceTest(unittest.TestCase):
    """Not every workspace can be isolated, and that is not a failure."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_plain_directory_yields_no_checkout(self) -> None:
        self.assertIsNone(worktree.create(self.root, "abc"))

    def test_a_repository_with_no_commit_yields_no_checkout(self) -> None:
        # There is no HEAD to check out, and nothing to diff against later.
        git(["init", "-b", "main"], self.root)
        self.assertIsNone(worktree.create(self.root, "abc"))


class WorktreeCreateLimitTest(unittest.TestCase):
    """A wave of isolate=true must not all hit ``git worktree add`` at once."""

    def test_creates_are_gated(self) -> None:
        self.assertEqual(worktree._CREATE_LIMIT, 4)
        self.assertGreater(worktree._CREATE_TIMEOUT_S, worktree._TIMEOUT_S)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_the_create_path_passes_the_longer_timeout(self) -> None:
        seen: list[float | None] = []
        real = worktree._git

        def _spy(args, *, cwd, timeout=None):
            if args[:2] == ["worktree", "add"]:
                seen.append(timeout)
            return real(args, cwd=cwd, timeout=timeout)

        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            git(["init", "-b", "main"], root)
            git(["config", "user.email", "test@example.com"], root)
            git(["config", "user.name", "Test"], root)
            (root / "a.py").write_text("x = 1\n")
            git(["add", "."], root)
            git(["commit", "-m", "first"], root)
            store = Path(tmp) / "worktrees"
            with unittest.mock.patch.object(worktree, "worktrees_root", lambda: store):
                with unittest.mock.patch.object(worktree, "_git", _spy):
                    checkout = worktree.create(root, "gate1", pool_size=0)
            self.assertIsNotNone(checkout)
            assert checkout is not None
            worktree.remove(checkout)
        self.assertEqual(seen, [worktree._CREATE_TIMEOUT_S])


if __name__ == "__main__":
    unittest.main()
