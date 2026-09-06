"""The agent could see the repository but not act on it.

``navin.utils.git_state`` already reports the branch and the dirty tree every
turn. Every actual git operation - reading the diff it had just written, looking
up who last touched a line, recording a commit - went through ``exec`` and was
re-parsed from output meant for a human.

These tests drive real repositories rather than fixture strings, because the
failures worth catching here are the ones a handwritten fixture would be written
to agree with: a path with a space, a rename record, a hook that rejects a
commit, a path pointing outside the repository.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.agent.tools.base import ToolResult
from navin.agent.tools.file_state import FileStates
from navin.agent.tools.git import GitTool
from navin.utils.git_state import clear_cache


def _run(coro):
    return asyncio.run(coro)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


class _RepoTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(clear_cache)
        self.root = Path(self._tmp.name).resolve()
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "Test")
        _git(self.root, "config", "commit.gpgsign", "false")

    def _write(self, name: str, body: str) -> Path:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def _commit(self, message: str = "c") -> None:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", message)

    def _tool(self) -> GitTool:
        return GitTool(
            workspace=self.root, allowed_dir=self.root, file_states=FileStates()
        )

    def _call(self, **kwargs) -> str:
        clear_cache()
        return str(_run(self._tool().execute(**kwargs)))

    def _log_subjects(self) -> list[str]:
        out = _git(self.root, "log", "--pretty=format:%s").stdout
        return [line for line in out.splitlines() if line]


class StatusTest(_RepoTest):
    def test_a_clean_tree_says_so_and_names_the_branch(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        out = self._call(action="status")
        self.assertIn("branch main", out)
        self.assertIn("working tree clean", out)

    def test_every_changed_path_is_listed_by_bucket(self) -> None:
        self._write("tracked.py", "one\n")
        self._commit()
        self._write("tracked.py", "two\n")
        self._write("fresh.py", "new\n")
        _git(self.root, "add", "fresh.py")
        out = self._call(action="status")
        self.assertIn("staged (1):", out)
        self.assertIn("fresh.py", out)
        self.assertIn("unstaged (1):", out)
        self.assertIn("tracked.py", out)

    def test_a_path_with_a_space_survives_the_listing(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        self._write("with space.txt", "x\n")
        self.assertIn("with space.txt", self._call(action="status"))

    def test_an_unfinished_merge_is_announced(self) -> None:
        """Walking into a half-finished merge is how an agent loses a resolution."""
        self._write("a.txt", "base\n")
        self._commit("base")
        _git(self.root, "switch", "-q", "-c", "other")
        self._write("a.txt", "other\n")
        self._commit("other")
        _git(self.root, "switch", "-q", "main")
        self._write("a.txt", "main\n")
        self._commit("main")
        _git(self.root, "merge", "other")  # conflicts, leaving MERGE_HEAD behind
        out = self._call(action="status")
        self.assertIn("merge is in progress", out)
        self.assertIn("conflicted", out)


class DiffTest(_RepoTest):
    def setUp(self) -> None:
        super().setUp()
        self._write("mod.py", "one\n")
        self._commit()

    def test_the_working_tree_diff_shows_the_change(self) -> None:
        self._write("mod.py", "two\n")
        out = self._call(action="diff")
        self.assertIn("-one", out)
        self.assertIn("+two", out)

    def test_the_index_is_a_separate_question(self) -> None:
        self._write("mod.py", "two\n")
        _git(self.root, "add", "mod.py")
        self.assertIn("+two", self._call(action="diff", staged=True))
        self.assertIn("No changes in the working tree", self._call(action="diff"))

    def test_an_empty_diff_says_where_to_look_instead(self) -> None:
        """A bare "no changes" sends the agent hunting for a bug that is not there."""
        self._write("mod.py", "two\n")
        _git(self.root, "add", "mod.py")
        out = self._call(action="diff")
        self.assertIn("staged=true", out)

    def test_stat_summarizes_instead_of_printing_the_patch(self) -> None:
        self._write("mod.py", "two\n")
        out = self._call(action="diff", stat=True)
        self.assertIn("mod.py", out)
        self.assertNotIn("+two", out)

    def test_paths_scope_the_diff(self) -> None:
        self._write("other.py", "committed\n")
        self._commit("add other")
        self._write("mod.py", "two\n")
        self._write("other.py", "changed\n")
        out = self._call(action="diff", paths=["mod.py"])
        self.assertIn("mod.py", out)
        self.assertNotIn("other.py", out)

    def test_a_path_outside_the_repository_is_refused(self) -> None:
        out = self._call(action="diff", paths=["../escape.py"])
        self.assertIn("Error", out)

    def test_a_ref_can_be_used_as_the_base(self) -> None:
        self._write("mod.py", "two\n")
        self._commit("second")
        out = self._call(action="diff", commit="HEAD~1")
        self.assertIn("+two", out)


class HistoryTest(_RepoTest):
    def setUp(self) -> None:
        super().setUp()
        self._write("mod.py", "one\n")
        self._commit("first")
        self._write("mod.py", "two\n")
        self._commit("second")

    def test_log_lists_recent_subjects_newest_first(self) -> None:
        out = self._call(action="log", limit=5)
        self.assertLess(out.index("second"), out.index("first"))

    def test_log_can_be_scoped_to_a_path(self) -> None:
        self._write("untouched.py", "x\n")
        self._commit("third")
        out = self._call(action="log", paths=["mod.py"])
        self.assertNotIn("third", out)

    def test_show_prints_the_patch_of_one_commit(self) -> None:
        out = self._call(action="show", commit="HEAD")
        self.assertIn("second", out)
        self.assertIn("+two", out)

    def test_blame_attributes_a_line_range(self) -> None:
        out = self._call(action="blame", path="mod.py", line_start=1, line_end=1)
        self.assertIn("two", out)

    def test_blame_without_a_path_says_what_is_missing(self) -> None:
        self.assertIn("requires 'path'", self._call(action="blame"))

    def test_branches_marks_the_current_head(self) -> None:
        _git(self.root, "switch", "-q", "-c", "feature")
        out = self._call(action="branches")
        self.assertIn("* feature", out)


class RecordingTest(_RepoTest):
    def setUp(self) -> None:
        super().setUp()
        self._write("mod.py", "one\n")
        self._commit("first")

    def test_add_then_commit_records_the_change(self) -> None:
        self._write("mod.py", "two\n")
        self.assertIn("now staged", self._call(action="add", paths=["mod.py"]))
        self._call(action="commit", message="second")
        self.assertEqual(self._log_subjects()[0], "second")

    def test_add_without_paths_refuses_rather_than_staging_everything(self) -> None:
        self._write("junk.log", "noise\n")
        out = self._call(action="add")
        self.assertIn("requires 'paths'", out)
        self.assertEqual(_git(self.root, "diff", "--cached", "--name-only").stdout, "")

    def test_commit_needs_a_message(self) -> None:
        self._write("mod.py", "two\n")
        _git(self.root, "add", "mod.py")
        self.assertIn("non-empty 'message'", self._call(action="commit", message="  "))

    def test_committing_nothing_explains_the_two_ways_forward(self) -> None:
        self._write("mod.py", "two\n")
        out = self._call(action="commit", message="second")
        self.assertIn("nothing is staged", out)
        self.assertIn("all=true", out)

    def test_all_stages_tracked_changes_but_not_new_files(self) -> None:
        self._write("mod.py", "two\n")
        self._write("fresh.py", "new\n")
        self._call(action="commit", message="second", all=True)
        self.assertEqual(self._log_subjects()[0], "second")
        self.assertIn("fresh.py", _git(self.root, "status", "--porcelain").stdout)

    def _reject_hook(self) -> None:
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho refused by hook\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

    def test_a_rejecting_hook_is_reported_rather_than_swallowed(self) -> None:
        self._reject_hook()
        self._write("mod.py", "two\n")
        _git(self.root, "add", "mod.py")
        out = self._call(action="commit", message="second")
        self.assertIn("hook", out.lower())
        self.assertEqual(self._log_subjects(), ["first"])

    def test_no_verify_gets_past_a_hook_when_asked(self) -> None:
        """A broken hook should not be able to strand an autonomous turn."""
        self._reject_hook()
        self._write("mod.py", "two\n")
        _git(self.root, "add", "mod.py")
        self._call(action="commit", message="second", no_verify=True)
        self.assertEqual(self._log_subjects()[0], "second")

    def test_allow_empty_records_a_commit_with_nothing_staged(self) -> None:
        self._call(action="commit", message="marker", allow_empty=True)
        self.assertEqual(self._log_subjects()[0], "marker")

    def test_committing_over_a_conflict_is_refused(self) -> None:
        _git(self.root, "switch", "-q", "-c", "other")
        self._write("mod.py", "other\n")
        self._commit("other")
        _git(self.root, "switch", "-q", "main")
        self._write("mod.py", "main\n")
        self._commit("main")
        _git(self.root, "merge", "other")
        out = self._call(action="commit", message="merge", all=True)
        self.assertIn("conflicted", out)

    def test_switch_creates_a_branch_and_reports_the_new_state(self) -> None:
        out = self._call(action="switch", name="feature", create=True)
        self.assertIn("branch feature", out)
        self.assertEqual(
            _git(self.root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
            "feature",
        )

    def test_stash_parks_the_tree_and_pop_brings_it_back(self) -> None:
        self._write("mod.py", "two\n")
        self._call(action="stash", message="wip")
        self.assertEqual((self.root / "mod.py").read_text(encoding="utf-8"), "one\n")
        self.assertIn("wip", self._call(action="stash_list"))
        self._call(action="stash_pop")
        self.assertEqual((self.root / "mod.py").read_text(encoding="utf-8"), "two\n")


class DestructiveGuardTest(_RepoTest):
    def setUp(self) -> None:
        super().setUp()
        self._write("mod.py", "one\n")
        self._commit("first")

    def test_restore_discards_only_the_named_file(self) -> None:
        self._write("mod.py", "two\n")
        self._write("other.py", "kept\n")
        self._call(action="restore", paths=["mod.py"])
        self.assertEqual((self.root / "mod.py").read_text(encoding="utf-8"), "one\n")
        self.assertEqual((self.root / "other.py").read_text(encoding="utf-8"), "kept\n")

    def test_restore_can_take_the_whole_tree_when_that_is_the_ask(self) -> None:
        """An autonomous agent that can reset --hard can also restore a tree."""
        self._write("mod.py", "two\n")
        self._call(action="restore", paths=["."])
        self.assertEqual((self.root / "mod.py").read_text(encoding="utf-8"), "one\n")

    def test_restore_without_paths_says_how_to_mean_everything(self) -> None:
        out = self._call(action="restore")
        self.assertIn("requires 'paths'", out)
        self.assertIn("['.']", out)

    def test_staged_restore_unstages_without_touching_the_file(self) -> None:
        self._write("mod.py", "two\n")
        _git(self.root, "add", "mod.py")
        self._call(action="restore", paths=["mod.py"], staged=True)
        self.assertEqual((self.root / "mod.py").read_text(encoding="utf-8"), "two\n")
        self.assertEqual(_git(self.root, "diff", "--cached", "--name-only").stdout, "")

    def test_an_unknown_action_names_the_valid_ones(self) -> None:
        out = self._call(action="psuh")
        self.assertIn("Valid values", out)


class ResetTest(_RepoTest):
    def setUp(self) -> None:
        super().setUp()
        self._write("mod.py", "one\n")
        self._commit("first")
        self._write("mod.py", "two\n")
        self._commit("second")

    def test_mixed_reset_uncommits_but_keeps_the_file(self) -> None:
        self._call(action="reset", commit="HEAD~1")
        self.assertEqual(self._log_subjects(), ["first"])
        self.assertEqual((self.root / "mod.py").read_text(encoding="utf-8"), "two\n")

    def test_soft_reset_keeps_the_change_staged(self) -> None:
        self._call(action="reset", commit="HEAD~1", mode="soft")
        self.assertIn("mod.py", _git(self.root, "diff", "--cached", "--name-only").stdout)

    def test_hard_reset_throws_the_tree_away_and_says_so(self) -> None:
        self._write("mod.py", "uncommitted\n")
        out = self._call(action="reset", commit="HEAD", mode="hard")
        self.assertEqual((self.root / "mod.py").read_text(encoding="utf-8"), "two\n")
        self.assertIn("Discarded uncommitted changes in 1", out)

    def test_an_unknown_mode_is_named_rather_than_passed_through(self) -> None:
        out = self._call(action="reset", mode="nuclear")
        self.assertIn("mode must be one of", out)

    def test_reset_points_paths_at_restore_instead(self) -> None:
        """"reset -- <paths>" unstages, which reads nothing like mode=hard."""
        out = self._call(action="reset", paths=["mod.py"], mode="hard")
        self.assertIn("action=restore", out)
        self.assertEqual(self._log_subjects(), ["second", "first"])


class OptionInjectionTest(_RepoTest):
    """A ref that starts with a dash is an option in disguise.

    "--output=x" as a diff base makes git write a file wherever it likes,
    sidestepping the workspace path checks the tool advertises; every value
    that ends up as a positional ref must go through the same refusal.
    """

    def setUp(self) -> None:
        super().setUp()
        self._write("mod.py", "one\n")
        self._commit("first")

    def test_a_diff_base_that_looks_like_a_flag_is_refused(self) -> None:
        out = self._call(action="diff", commit="--output=pwned.txt")
        self.assertIn("not a valid name", out)
        self.assertFalse((self.root / "pwned.txt").exists())

    def test_a_show_commit_that_looks_like_a_flag_is_refused(self) -> None:
        out = self._call(action="show", commit="--output=pwned.txt")
        self.assertIn("not a valid name", out)
        self.assertFalse((self.root / "pwned.txt").exists())

    def test_a_switch_name_that_looks_like_a_flag_is_refused(self) -> None:
        out = self._call(action="switch", name="--output=pwned.txt")
        self.assertIn("not a valid name", out)
        self.assertEqual(
            _git(self.root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
            "main",
        )


class MergeTest(_RepoTest):
    def setUp(self) -> None:
        super().setUp()
        self._write("base.py", "base\n")
        self._commit("base")

    def _diverge(self, *, conflicting: bool) -> None:
        _git(self.root, "switch", "-q", "-c", "feature")
        self._write("base.py" if conflicting else "feature.py", "feature\n")
        self._commit("feature work")
        _git(self.root, "switch", "-q", "main")
        self._write("base.py" if conflicting else "main.py", "main\n")
        self._commit("main work")

    def test_a_clean_merge_brings_the_branch_in(self) -> None:
        self._diverge(conflicting=False)
        self._call(action="merge", name="feature")
        self.assertTrue((self.root / "feature.py").exists())
        self.assertIn("feature work", self._log_subjects())

    def test_merge_never_waits_for_an_editor(self) -> None:
        """A merge commit message would open $EDITOR and hang the turn."""
        self._diverge(conflicting=False)
        out = self._call(action="merge", name="feature")
        self.assertNotIn("Error", out)
        self.assertEqual(
            _git(self.root, "rev-list", "--count", "HEAD").stdout.strip(), "4"
        )

    def test_a_conflict_names_the_files_and_the_next_call(self) -> None:
        self._diverge(conflicting=True)
        out = self._call(action="merge", name="feature")
        self.assertIn("conflicted", out)
        self.assertIn("base.py", out)
        self.assertIn("step=continue", out)
        self.assertIn("step=abort", out)

    def test_abort_returns_to_the_state_before_the_merge(self) -> None:
        self._diverge(conflicting=True)
        self._call(action="merge", name="feature")
        self._call(action="merge", step="abort")
        self.assertEqual((self.root / "base.py").read_text(encoding="utf-8"), "main\n")
        self.assertEqual(_git(self.root, "status", "--porcelain").stdout, "")

    def test_continue_finishes_the_merge_once_resolved(self) -> None:
        self._diverge(conflicting=True)
        self._call(action="merge", name="feature")
        self._write("base.py", "resolved\n")
        self._call(action="add", paths=["base.py"])
        self._call(action="merge", step="continue")
        self.assertIn("feature work", self._log_subjects())
        self.assertEqual(_git(self.root, "status", "--porcelain").stdout, "")

    def test_continue_over_an_unresolved_file_says_which_one(self) -> None:
        self._diverge(conflicting=True)
        self._call(action="merge", name="feature")
        out = self._call(action="merge", step="continue")
        self.assertIn("still conflicted", out)
        self.assertIn("base.py", out)

    def test_continue_without_a_merge_in_progress_is_explained(self) -> None:
        out = self._call(action="merge", step="continue")
        self.assertIn("no merge is in progress", out)

    def test_starting_a_merge_during_one_is_refused_with_the_way_out(self) -> None:
        self._diverge(conflicting=True)
        self._call(action="merge", name="feature")
        out = self._call(action="merge", name="feature")
        self.assertIn("already in progress", out)

    def test_merge_needs_a_source(self) -> None:
        self.assertIn("requires 'name'", self._call(action="merge"))

    def test_squash_stages_without_committing(self) -> None:
        self._diverge(conflicting=False)
        out = self._call(action="merge", name="feature", squash=True)
        self.assertIn("action=commit", out)
        self.assertNotIn("feature work", self._log_subjects())
        self.assertIn("feature.py", _git(self.root, "diff", "--cached", "--name-only").stdout)

    def test_a_name_that_looks_like_a_flag_is_refused(self) -> None:
        self.assertIn("not a valid name", self._call(action="merge", name="--abort"))


class RebaseTest(_RepoTest):
    def setUp(self) -> None:
        super().setUp()
        self._write("base.py", "base\n")
        self._commit("base")
        _git(self.root, "switch", "-q", "-c", "feature")

    def test_a_clean_rebase_replays_onto_the_base(self) -> None:
        self._write("feature.py", "f\n")
        self._commit("feature work")
        _git(self.root, "switch", "-q", "main")
        self._write("main.py", "m\n")
        self._commit("main work")
        _git(self.root, "switch", "-q", "feature")
        self._call(action="rebase", name="main")
        self.assertEqual(self._log_subjects()[:2], ["feature work", "main work"])

    def test_a_dirty_tree_is_reported_with_the_two_ways_forward(self) -> None:
        self._write("base.py", "dirty\n")
        out = self._call(action="rebase", name="main")
        self.assertIn("uncommitted changes", out)
        self.assertIn("action=stash", out)

    def test_a_conflicted_rebase_can_be_aborted(self) -> None:
        self._write("base.py", "feature\n")
        self._commit("feature work")
        _git(self.root, "switch", "-q", "main")
        self._write("base.py", "main\n")
        self._commit("main work")
        _git(self.root, "switch", "-q", "feature")
        out = self._call(action="rebase", name="main")
        self.assertIn("step=continue", out)
        self._call(action="rebase", step="abort")
        self.assertEqual((self.root / "base.py").read_text(encoding="utf-8"), "feature\n")

    def test_skip_is_a_rebase_step_not_a_merge_one(self) -> None:
        self.assertIn("applies to rebase", self._call(action="merge", step="skip"))

    def test_an_unknown_step_is_named(self) -> None:
        self.assertIn("step must be one of", self._call(action="rebase", step="carry-on"))


class RemoteTest(_RepoTest):
    """Push and pull against a second repository on disk, not a network."""

    def setUp(self) -> None:
        super().setUp()
        self._write("mod.py", "one\n")
        self._commit("first")
        self._remote_dir = TemporaryDirectory()
        self.addCleanup(self._remote_dir.cleanup)
        self.remote = Path(self._remote_dir.name).resolve()
        # -b main matters: a bare repo whose HEAD names a branch that was never
        # pushed clones with nothing checked out, which fails as a broken test
        # rather than as the behaviour under test.
        _git(self.remote, "init", "-q", "--bare", "-b", "main")
        _git(self.root, "remote", "add", "origin", str(self.remote))

    def _clone(self, path: Path) -> None:
        """A second working copy of the remote, standing in for a colleague."""
        result = _git(path.parent, "clone", "-q", str(self.remote), str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        _git(path, "config", "user.email", "o@example.com")
        _git(path, "config", "user.name", "Other")

    def _commit_elsewhere(self, name: str = "theirs.py") -> None:
        with TemporaryDirectory() as other:
            clone = Path(other).resolve()
            self._clone(clone)
            (clone / name).write_text("theirs\n", encoding="utf-8")
            _git(clone, "add", "-A")
            _git(clone, "commit", "-qm", "from elsewhere")
            pushed = _git(clone, "push", "-q")
            self.assertEqual(pushed.returncode, 0, pushed.stderr)

    def _remote_log(self, branch: str = "main") -> list[str]:
        out = _git(self.remote, "log", branch, "--pretty=format:%s").stdout
        return [line for line in out.splitlines() if line]

    def test_push_publishes_and_sets_the_upstream_by_itself(self) -> None:
        out = self._call(action="push")
        self.assertNotIn("Error", out)
        self.assertEqual(self._remote_log(), ["first"])
        self.assertEqual(
            _git(self.root, "rev-parse", "--abbrev-ref", "@{upstream}").stdout.strip(),
            "origin/main",
        )

    def test_fetch_reports_where_the_branch_now_stands(self) -> None:
        self._call(action="push")
        out = self._call(action="fetch")
        self.assertIn("branch main", out)
        self.assertIn("origin/main", out)

    def test_pull_brings_down_a_commit_made_elsewhere(self) -> None:
        self._call(action="push")
        self._commit_elsewhere()
        self._call(action="pull")
        self.assertTrue((self.root / "theirs.py").exists())

    def test_pull_rebases_local_work_on_top_rather_than_merging(self) -> None:
        self._call(action="push")
        self._commit_elsewhere()
        self._write("mine.py", "mine\n")
        self._commit("mine")
        self._call(action="pull")
        self.assertEqual(self._log_subjects()[:2], ["mine", "from elsewhere"])
        self.assertEqual(
            _git(self.root, "rev-list", "--merges", "--count", "HEAD").stdout.strip(), "0"
        )

    def test_a_rejected_push_explains_the_two_choices(self) -> None:
        self._call(action="push")
        self._commit_elsewhere()
        self._write("mod.py", "mine\n")
        self._commit("mine")
        out = self._call(action="push")
        self.assertIn("action=pull", out)
        self.assertIn("force=true", out)

    def test_force_overwrites_the_remote_branch(self) -> None:
        self._call(action="push")
        _git(self.root, "reset", "-q", "--hard", "HEAD")
        self._write("mod.py", "rewritten\n")
        _git(self.root, "commit", "-q", "--amend", "-m", "rewritten")
        out = self._call(action="push", force=True)
        self.assertNotIn("Error", out)
        self.assertEqual(self._remote_log(), ["rewritten"])

    def test_pushing_without_a_remote_says_how_to_add_one(self) -> None:
        _git(self.root, "remote", "remove", "origin")
        out = self._call(action="push")
        self.assertIn("no remote", out)
        self.assertIn("action=remote", out)

    def test_an_unknown_remote_lists_the_real_ones(self) -> None:
        out = self._call(action="fetch", remote="upstream")
        self.assertIn("no remote named 'upstream'", out)
        self.assertIn("origin", out)


class NonInteractiveTest(_RepoTest):
    def test_git_can_never_stop_at_an_editor_or_a_prompt(self) -> None:
        """Either would block the turn until the timeout with nobody to answer."""
        from navin.agent.tools.git import _git_env

        env = _git_env()
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_EDITOR"], "true")
        self.assertNotIn("EDITOR", env)
        self.assertNotIn("VISUAL", env)
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])

    def test_settings_forge_tokens_reach_git_https_helpers(self) -> None:
        from navin.agent.tools.git import _GIT_FORGE_TOKENS, _git_env

        token = _GIT_FORGE_TOKENS.set({
            "gitlab.com": "glpat-from-settings",
            "git.example": "tea-from-settings",
        })
        try:
            with patch.dict(
                os.environ,
                {
                    "GITLAB_TOKEN": "",
                    "GL_TOKEN": "",
                    "GLAB_TOKEN": "",
                    "FORGEJO_TOKEN": "",
                    "FORGEJO_ACCESS_TOKEN": "",
                    "GITEA_TOKEN": "",
                    "GITEA_SERVER_TOKEN": "",
                    "TEA_TOKEN": "",
                },
                clear=False,
            ):
                env = _git_env()
        finally:
            _GIT_FORGE_TOKENS.reset(token)
        self.assertEqual(env.get("GITLAB_TOKEN"), "glpat-from-settings")
        self.assertEqual(env.get("FORGEJO_TOKEN") or env.get("TEA_TOKEN"), "tea-from-settings")


class RoutingTest(_RepoTest):
    def test_reads_are_parallel_safe_and_writes_are_not(self) -> None:
        tool = self._tool()
        self.assertTrue(tool.call_concurrency_safe({"action": "diff"}))
        self.assertFalse(tool.call_concurrency_safe({"action": "commit"}))

    def test_outside_a_repository_the_error_says_what_to_do(self) -> None:
        with TemporaryDirectory() as plain:
            root = Path(plain).resolve()
            tool = GitTool(workspace=root, allowed_dir=root, file_states=FileStates())
            out = str(_run(tool.execute(action="status")))
            self.assertIn("not inside a git repository", out)

    def test_a_failed_query_is_an_error_result_the_loop_can_see(self) -> None:
        self._write("mod.py", "one\n")
        self._commit()
        result = _run(self._tool().execute(action="show", commit="does-not-exist"))
        self.assertIsInstance(result, ToolResult)
        self.assertTrue(result.is_error)


class CloneFolderNameTest(unittest.TestCase):
    def test_https_github_drops_git_suffix(self) -> None:
        from navin.agent.tools.git import _clone_folder_name

        self.assertEqual(
            _clone_folder_name("https://github.com/acme/app.git"), "app"
        )

    def test_gitlab_ssh(self) -> None:
        from navin.agent.tools.git import _clone_folder_name

        self.assertEqual(
            _clone_folder_name("git@gitlab.com:group/proj.git"), "proj"
        )

    def test_forgejo_https(self) -> None:
        from navin.agent.tools.git import _clone_folder_name

        self.assertEqual(
            _clone_folder_name("https://forge.example/org/navin.git"), "navin"
        )


class BootstrapRepoTest(unittest.TestCase):
    """init / clone / remote work without an existing repository or a shell."""

    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self.addCleanup(clear_cache)

    def test_init_creates_a_repository(self) -> None:
        with TemporaryDirectory() as plain:
            root = Path(plain).resolve()
            tool = GitTool(workspace=root, allowed_dir=root, file_states=FileStates())
            out = str(_run(tool.execute(action="init")))
            self.assertNotIn("Error", out)
            self.assertTrue((root / ".git").is_dir())

    def test_clone_from_a_local_repository(self) -> None:
        with TemporaryDirectory() as src_dir, TemporaryDirectory() as dest_dir:
            src = Path(src_dir).resolve()
            dest = Path(dest_dir).resolve()
            _git(src, "init", "-q", "-b", "main")
            _git(src, "config", "user.email", "t@example.com")
            _git(src, "config", "user.name", "Test")
            _git(src, "config", "commit.gpgsign", "false")
            (src / "README").write_text("cloned-ok\n", encoding="utf-8")
            _git(src, "add", "-A")
            _git(src, "commit", "-qm", "first")
            tool = GitTool(workspace=dest, allowed_dir=dest, file_states=FileStates())
            out = str(_run(tool.execute(action="clone", remote=str(src))))
            self.assertNotIn("Error", out)
            cloned = dest / src.name
            self.assertTrue((cloned / "README").is_file())
            self.assertEqual(
                (cloned / "README").read_text(encoding="utf-8"), "cloned-ok\n"
            )


class RemoteLifecycleTest(_RepoTest):
    def test_remote_lists_and_adds(self) -> None:
        listed = self._call(action="remote")
        self.assertIn("No remotes", listed)
        out = self._call(
            action="remote",
            create=True,
            name="gitlab",
            remote="https://gitlab.example/acme/app.git",
        )
        self.assertNotIn("Error", out)
        again = self._call(action="remote")
        self.assertIn("gitlab", again)
        self.assertIn("gitlab.example", again)

    def test_adding_an_existing_remote_updates_the_url(self) -> None:
        first = self._call(
            action="remote",
            create=True,
            name="origin",
            remote="https://gitlab.example/acme/app.git",
        )
        self.assertNotIn("Error", first)
        out = self._call(
            action="remote",
            create=True,
            name="origin",
            remote="https://forgejo.example/acme/app.git",
        )
        self.assertNotIn("Error", out)
        listed = self._call(action="remote")
        self.assertIn("forgejo.example", listed)
        self.assertNotIn("gitlab.example", listed)


if __name__ == "__main__":
    unittest.main()
