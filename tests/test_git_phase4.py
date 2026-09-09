# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 4 source-control: selective stage, branch ops, conflict flags."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.security.workspace_access import default_workspace_scope
from navin.webui.github_pr_api import (
    _summarize_checks,
    github_ci_status_payload,
    github_fix_ci_prompt_payload,
    github_pr_create_payload,
    github_pr_view_payload,
)
from navin.webui.project_search import (
    ProjectSearchError,
    git_branch_payload,
    git_changes_payload,
    git_commit_payload,
    git_conflict_action_payload,
    git_diff_payload,
    git_discard_payload,
    git_fetch_payload,
    git_pull_payload,
    git_stage_payload,
    git_stash_payload,
    git_sync_payload,
    git_undo_last_commit_payload,
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class GitPhase4Test(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "test@navin.local")
        _git(self.root, "config", "user.name", "Navin Test")
        (self.root / "a.txt").write_text("one\n")
        (self.root / "b.txt").write_text("bee\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-m", "init")

    @property
    def scope(self):
        return default_workspace_scope(self.root, True)

    def test_stage_selected_paths_only(self) -> None:
        (self.root / "a.txt").write_text("two\n")
        (self.root / "b.txt").write_text("buzz\n")
        payload = git_stage_payload(self.scope, ["a.txt"], stage=True)
        staged = {row["path"]: row["staged"] for row in payload["files"]}
        self.assertTrue(staged["a.txt"])
        self.assertFalse(staged["b.txt"])

    def test_unstage_path(self) -> None:
        (self.root / "a.txt").write_text("two\n")
        git_stage_payload(self.scope, ["a.txt"], stage=True)
        payload = git_stage_payload(self.scope, ["a.txt"], stage=False)
        staged = {row["path"]: row["staged"] for row in payload["files"]}
        self.assertFalse(staged.get("a.txt", False))

    def test_commit_with_paths_leaves_other_dirty(self) -> None:
        (self.root / "a.txt").write_text("two\n")
        (self.root / "b.txt").write_text("buzz\n")
        result = git_commit_payload(
            self.scope, "only a", push=False, paths=["a.txt"],
        )
        self.assertTrue(result["committed"])
        changes = git_changes_payload(self.scope)
        paths = {row["path"] for row in changes["files"]}
        self.assertEqual(paths, {"b.txt"})

    def test_commit_staged_only_skips_unstaged(self) -> None:
        (self.root / "a.txt").write_text("two\n")
        (self.root / "b.txt").write_text("buzz\n")
        git_stage_payload(self.scope, ["a.txt"], stage=True)
        result = git_commit_payload(self.scope, "staged a", push=False, paths=[])
        self.assertTrue(result["committed"])
        changes = git_changes_payload(self.scope)
        paths = {row["path"] for row in changes["files"]}
        self.assertEqual(paths, {"b.txt"})

    def test_branch_create_and_checkout(self) -> None:
        created = git_branch_payload(self.scope, op="create", name="feature/x")
        self.assertEqual(created["branch"], "feature/x")
        listed = git_branch_payload(self.scope, op="list")
        self.assertIn("feature/x", listed["branches"] or [])
        git_branch_payload(self.scope, op="checkout", name="main")
        self.assertEqual(git_branch_payload(self.scope, op="list")["current"], "main")

    def test_branch_rejects_bad_name(self) -> None:
        with self.assertRaises(ProjectSearchError):
            git_branch_payload(self.scope, op="create", name="../evil")

    def test_conflict_flags_and_abort(self) -> None:
        _git(self.root, "checkout", "-b", "side")
        (self.root / "a.txt").write_text("side\n")
        _git(self.root, "commit", "-am", "side")
        _git(self.root, "checkout", "main")
        (self.root / "a.txt").write_text("mainline\n")
        _git(self.root, "commit", "-am", "main")
        merge = subprocess.run(  # noqa: S603
            ["git", "-C", str(self.root), "merge", "side"],  # noqa: S607
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(merge.returncode, 0)
        changes = git_changes_payload(self.scope)
        self.assertTrue(changes["merge_in_progress"])
        aborted = git_conflict_action_payload(self.scope, action="abort")
        self.assertTrue(aborted["ok"])
        self.assertFalse(aborted["merge_in_progress"])


class GithubPrApiUnitTest(unittest.TestCase):
    def test_summarize_checks_failure_wins(self) -> None:
        summary = _summarize_checks(
            [
                {"name": "lint", "state": "SUCCESS"},
                {"name": "test", "conclusion": "FAILURE"},
                {"name": "build", "status": "IN_PROGRESS"},
            ]
        )
        self.assertEqual(summary["state"], "failure")
        self.assertEqual(summary["failing"], 1)
        self.assertEqual(summary["passing"], 1)
        self.assertEqual(summary["pending"], 1)

    def test_pr_view_without_gh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope = default_workspace_scope(root, True)
            with patch("navin.webui.github_pr_api.gh_available", return_value=False):
                payload = github_pr_view_payload(scope)
            self.assertFalse(payload["available"])
            self.assertIsNone(payload["pr"])

    def test_ci_and_fix_prompt_without_gh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            scope = default_workspace_scope(root, True)
            with patch("navin.webui.github_pr_api.gh_available", return_value=False):
                ci = github_ci_status_payload(scope)
                fix = github_fix_ci_prompt_payload(scope)
            self.assertFalse(ci["available"])
            self.assertIn("Fix CI", fix["prompt"])

    def test_pr_create_falls_back_to_gh_on_github_without_token(self) -> None:
        """A github.com remote with `gh` signed in keeps its legacy road."""
        from navin.webui.forge_api import ForgeRemote
        from navin.webui.github_pr_api import ForgeContext

        github_no_token = ForgeContext(
            ForgeRemote(kind="github", host="github.com", owner="o", repo="r"),
            "",
            "",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            scope = default_workspace_scope(root, True)
            with patch("navin.webui.github_pr_api.gh_available", return_value=True):
                with patch(
                    "navin.webui.github_pr_api.forge_context",
                    return_value=github_no_token,
                ):
                    with patch(
                        "navin.webui.github_pr_api._branch_state",
                        return_value={"branch": "feature/x"},
                    ):
                        with patch(
                            "navin.webui.github_pr_api._gh",
                            side_effect=[
                                (1, "", "no pull requests found"),
                                (0, "https://github.com/o/r/pull/9\n", ""),
                            ],
                        ):
                            with patch(
                                "navin.webui.project_search._run_git_write",
                                return_value=subprocess.CompletedProcess(
                                    args=["git", "push"],
                                    returncode=0,
                                    stdout="",
                                    stderr="",
                                ),
                            ):
                                with patch(
                                    "navin.webui.github_pr_api._head_sha_short",
                                    return_value="abc123def456",
                                ):
                                    payload = github_pr_create_payload(
                                        scope, title="t", draft=True,
                                    )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["created"])
        self.assertEqual(payload["head_sha"], "abc123def456")
        self.assertIn("pull/9", payload["pr_url"] or "")
        self.assertEqual(payload["token_source"], "gh")


class GitScmActionsTest(GitPhase4Test):
    def test_discard_restores_tracked_file(self) -> None:
        (self.root / "a.txt").write_text("dirty\n")
        payload = git_discard_payload(self.scope, ["a.txt"])
        self.assertEqual(payload["files"], [])
        self.assertEqual((self.root / "a.txt").read_text(), "one\n")

    def test_discard_deletes_untracked_file(self) -> None:
        (self.root / "new.txt").write_text("temp\n")
        payload = git_discard_payload(self.scope, ["new.txt"])
        self.assertEqual(payload["files"], [])
        self.assertFalse((self.root / "new.txt").exists())

    def test_discard_all_clears_working_tree(self) -> None:
        (self.root / "a.txt").write_text("dirty\n")
        (self.root / "new.txt").write_text("temp\n")
        payload = git_discard_payload(self.scope)
        self.assertEqual(payload["files"], [])
        self.assertEqual((self.root / "a.txt").read_text(), "one\n")
        self.assertFalse((self.root / "new.txt").exists())

    def test_stage_all_and_unstage_all(self) -> None:
        (self.root / "a.txt").write_text("two\n")
        (self.root / "b.txt").write_text("buzz\n")
        staged = git_stage_payload(self.scope, None, stage=True, all_files=True)
        self.assertTrue(all(row["staged"] for row in staged["files"]))
        unstaged = git_stage_payload(self.scope, None, stage=False, all_files=True)
        self.assertTrue(all(not row["staged"] for row in unstaged["files"]))

    def test_stash_hides_local_edits(self) -> None:
        (self.root / "a.txt").write_text("stashed\n")
        (self.root / "extra.txt").write_text("also\n")
        payload = git_stash_payload(self.scope)
        self.assertTrue(payload["stashed"])
        self.assertEqual(payload["files"], [])
        self.assertEqual((self.root / "a.txt").read_text(), "one\n")
        self.assertFalse((self.root / "extra.txt").exists())
        self.assertGreaterEqual(payload.get("stash_count") or 0, 1)

    def test_stash_pop_restores_edits(self) -> None:
        (self.root / "a.txt").write_text("stashed\n")
        git_stash_payload(self.scope, op="push")
        payload = git_stash_payload(self.scope, op="pop")
        self.assertTrue(payload["popped"])
        self.assertEqual((self.root / "a.txt").read_text(), "stashed\n")

    def test_amend_rewrites_last_unpushed_commit(self) -> None:
        (self.root / "a.txt").write_text("two\n")
        git_commit_payload(self.scope, "wip", push=False)
        (self.root / "a.txt").write_text("three\n")
        payload = git_commit_payload(self.scope, "real message", push=False, amend=True)
        self.assertTrue(payload["committed"])
        log = _git(self.root, "log", "--oneline")
        self.assertIn("real message", log.stdout)
        self.assertNotIn("wip", log.stdout)

    def test_amend_refused_when_already_pushed(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        _git(remote, "init", "-q", "--bare")
        _git(self.root, "remote", "add", "origin", str(remote))
        git_commit_payload(self.scope, "first extra", push=True)
        (self.root / "a.txt").write_text("nope\n")
        with self.assertRaises(ProjectSearchError) as ctx:
            git_commit_payload(self.scope, "rewrite", push=False, amend=True)
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("remote", ctx.exception.message.lower())

    def test_sync_pulls_then_pushes(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        other_dir = Path(tempfile.mkdtemp(prefix="navin-other-"))
        self.addCleanup(shutil.rmtree, remote, True)
        self.addCleanup(shutil.rmtree, other_dir, True)
        _git(remote, "init", "-q", "--bare")
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        _git(self.root, "remote", "add", "origin", str(remote))
        _git(self.root, "push", "-u", "origin", "HEAD")
        other = other_dir / "repo"
        subprocess.run(  # noqa: S603
            ["git", "clone", "-q", "-b", "main", str(remote), str(other)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        _git(other, "config", "user.email", "test@navin.local")
        _git(other, "config", "user.name", "Navin Test")
        (other / "from-other.txt").write_text("hi\n")
        _git(other, "add", "-A")
        _git(other, "commit", "-m", "from other")
        _git(other, "push", "-q")
        payload = git_sync_payload(self.scope)
        self.assertTrue(payload["synced"])
        self.assertTrue(payload["pulled"])
        self.assertTrue((self.root / "from-other.txt").is_file())

    def test_stash_pop_restores_untracked_and_clears_count(self) -> None:
        (self.root / "extra.txt").write_text("also\n")
        git_stash_payload(self.scope, op="push")
        self.assertFalse((self.root / "extra.txt").exists())
        self.assertGreaterEqual(git_changes_payload(self.scope).get("stash_count") or 0, 1)
        payload = git_stash_payload(self.scope, op="pop")
        self.assertTrue(payload["popped"])
        self.assertEqual((self.root / "extra.txt").read_text(), "also\n")
        self.assertEqual(payload.get("stash_count") or 0, 0)

    def test_stash_pop_empty_is_refused(self) -> None:
        with self.assertRaises(ProjectSearchError) as ctx:
            git_stash_payload(self.scope, op="pop")
        self.assertEqual(ctx.exception.status, 409)

    def test_amend_without_message_keeps_previous_subject(self) -> None:
        (self.root / "a.txt").write_text("two\n")
        git_commit_payload(self.scope, "keep me", push=False)
        (self.root / "a.txt").write_text("three\n")
        payload = git_commit_payload(self.scope, "", push=False, amend=True)
        self.assertTrue(payload["committed"])
        log = _git(self.root, "log", "-1", "--pretty=%s")
        self.assertEqual(log.stdout.strip(), "keep me")
        self.assertEqual((self.root / "a.txt").read_text(), "three\n")

    def test_amend_allowed_when_ahead_of_remote(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        _git(remote, "init", "-q", "--bare")
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        _git(self.root, "remote", "add", "origin", str(remote))
        _git(self.root, "push", "-u", "origin", "HEAD")
        (self.root / "a.txt").write_text("local only\n")
        git_commit_payload(self.scope, "wip local", push=False)
        (self.root / "a.txt").write_text("amended local\n")
        payload = git_commit_payload(self.scope, "real local", push=False, amend=True)
        self.assertTrue(payload["committed"])
        log = _git(self.root, "log", "--oneline")
        self.assertIn("real local", log.stdout)
        self.assertNotIn("wip local", log.stdout)
        self.assertIn("init", log.stdout)

    def test_amend_refused_leaves_working_tree_dirty(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        _git(remote, "init", "-q", "--bare")
        _git(self.root, "remote", "add", "origin", str(remote))
        git_commit_payload(self.scope, "first extra", push=True)
        (self.root / "a.txt").write_text("nope\n")
        with self.assertRaises(ProjectSearchError):
            git_commit_payload(self.scope, "rewrite", push=False, amend=True)
        self.assertEqual((self.root / "a.txt").read_text(), "nope\n")
        log = _git(self.root, "log", "-1", "--pretty=%s")
        self.assertEqual(log.stdout.strip(), "init")

    def test_sync_publishes_branch_without_upstream(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        _git(remote, "init", "-q", "--bare")
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        _git(self.root, "remote", "add", "origin", str(remote))
        payload = git_sync_payload(self.scope)
        self.assertTrue(payload["synced"])
        self.assertFalse(payload["pulled"])
        self.assertTrue(payload["pushed"])
        self.assertTrue(payload["has_upstream"])
        self.assertEqual(payload["ahead"], 0)

    def test_sync_already_up_to_date(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        _git(remote, "init", "-q", "--bare")
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        _git(self.root, "remote", "add", "origin", str(remote))
        _git(self.root, "push", "-u", "origin", "HEAD")
        payload = git_sync_payload(self.scope)
        self.assertTrue(payload["synced"])
        self.assertTrue(payload["pulled"])
        self.assertTrue(payload["pushed"])
        self.assertEqual(payload["ahead"], 0)
        self.assertEqual(payload["behind"], 0)

    def test_sync_pulls_remote_and_pushes_local(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        other_dir = Path(tempfile.mkdtemp(prefix="navin-other-"))
        self.addCleanup(shutil.rmtree, remote, True)
        self.addCleanup(shutil.rmtree, other_dir, True)
        _git(remote, "init", "-q", "--bare")
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        _git(self.root, "remote", "add", "origin", str(remote))
        _git(self.root, "push", "-u", "origin", "HEAD")
        other = other_dir / "repo"
        subprocess.run(  # noqa: S603
            ["git", "clone", "-q", "-b", "main", str(remote), str(other)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        _git(other, "config", "user.email", "test@navin.local")
        _git(other, "config", "user.name", "Navin Test")
        (other / "from-other.txt").write_text("hi\n")
        _git(other, "add", "-A")
        _git(other, "commit", "-m", "from other")
        _git(other, "push", "-q")
        (self.root / "from-self.txt").write_text("mine\n")
        git_commit_payload(self.scope, "from self", push=False)
        payload = git_sync_payload(self.scope)
        self.assertTrue(payload["synced"])
        self.assertTrue(payload["pulled"])
        self.assertTrue(payload["pushed"])
        self.assertTrue((self.root / "from-other.txt").is_file())
        self.assertTrue((self.root / "from-self.txt").is_file())
        log = _git(other, "pull", "-q")
        self.assertTrue((other / "from-self.txt").is_file(), msg=log.stdout + log.stderr)

    def test_sync_conflict_does_not_push(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        other_dir = Path(tempfile.mkdtemp(prefix="navin-other-"))
        self.addCleanup(shutil.rmtree, remote, True)
        self.addCleanup(shutil.rmtree, other_dir, True)
        _git(remote, "init", "-q", "--bare")
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        _git(self.root, "remote", "add", "origin", str(remote))
        _git(self.root, "push", "-u", "origin", "HEAD")
        other = other_dir / "repo"
        subprocess.run(  # noqa: S603
            ["git", "clone", "-q", "-b", "main", str(remote), str(other)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        _git(other, "config", "user.email", "test@navin.local")
        _git(other, "config", "user.name", "Navin Test")
        (other / "a.txt").write_text("theirs\n")
        _git(other, "add", "-A")
        _git(other, "commit", "-m", "theirs")
        _git(other, "push", "-q")
        (self.root / "a.txt").write_text("ours\n")
        git_commit_payload(self.scope, "ours", push=False)
        payload = git_sync_payload(self.scope)
        self.assertFalse(payload["synced"])
        self.assertTrue(payload["merge_in_progress"])
        self.assertFalse(payload.get("pushed"))
        remote_log = _git(remote, "log", "--all", "--pretty=%s")
        self.assertNotIn("ours", remote_log.stdout)

    def test_http_routes_expose_sync_amend_and_stash_pop(self) -> None:
        text = Path(__file__).resolve().parents[1].joinpath(
            "navin", "webui", "ws_http.py",
        ).read_text(encoding="utf-8")
        self.assertIn(r"^/api/sessions/([^/]+)/git-sync$", text)
        self.assertIn(r"^/api/sessions/([^/]+)/git-stash$", text)
        self.assertIn('amend=_query_first(query, "amend") == "1"', text)
        self.assertIn('op=_query_first(query, "op") or "push"', text)

    def test_publish_sets_upstream_on_new_branch(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        _git(remote, "init", "-q", "--bare")
        _git(self.root, "remote", "add", "origin", str(remote))
        git_branch_payload(self.scope, op="create", name="feature-x")
        before = git_changes_payload(self.scope)
        self.assertFalse(before["has_upstream"])
        payload = git_commit_payload(self.scope, "", push=True, commit=False)
        self.assertTrue(payload["pushed"])
        self.assertTrue(payload["has_upstream"])
        self.assertEqual(payload["ahead"], 0)

    def test_fetch_from_local_remote(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        _git(remote, "init", "-q", "--bare")
        _git(self.root, "remote", "add", "origin", str(remote))
        _git(self.root, "push", "-u", "origin", "HEAD")
        payload = git_fetch_payload(self.scope)
        self.assertTrue(payload["fetched"])
        self.assertTrue(payload["is_repo"])

    def test_undo_last_commit_soft_resets(self) -> None:
        (self.root / "a.txt").write_text("two\n")
        git_commit_payload(self.scope, "wip", push=False)
        payload = git_undo_last_commit_payload(self.scope)
        self.assertTrue(payload["undone"])
        log = _git(self.root, "log", "--oneline")
        self.assertNotIn("wip", log.stdout)
        self.assertIn("init", log.stdout)
        changes = git_changes_payload(self.scope)
        paths = {row["path"] for row in changes["files"]}
        self.assertIn("a.txt", paths)

    def test_undo_refused_when_already_pushed(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        _git(remote, "init", "-q", "--bare")
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        _git(self.root, "remote", "add", "origin", str(remote))
        git_commit_payload(self.scope, "first extra", push=True)

        with self.assertRaises(ProjectSearchError) as ctx:
            git_undo_last_commit_payload(self.scope)
        self.assertEqual(ctx.exception.status, 409)

    def test_pull_rebase_onto_remote(self) -> None:
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        other_dir = Path(tempfile.mkdtemp(prefix="navin-other-"))
        self.addCleanup(shutil.rmtree, remote, True)
        self.addCleanup(shutil.rmtree, other_dir, True)
        _git(remote, "init", "-q", "--bare")
        _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        _git(self.root, "remote", "add", "origin", str(remote))
        _git(self.root, "push", "-u", "origin", "HEAD")
        other = other_dir / "repo"
        subprocess.run(  # noqa: S603
            ["git", "clone", "-q", "-b", "main", str(remote), str(other)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        _git(other, "config", "user.email", "test@navin.local")
        _git(other, "config", "user.name", "Navin Test")
        (other / "from-other.txt").write_text("hi\n")
        _git(other, "add", "-A")
        _git(other, "commit", "-m", "from other")
        _git(other, "push", "-q")
        (self.root / "from-self.txt").write_text("mine\n")
        git_commit_payload(self.scope, "from self", push=False)

        payload = git_pull_payload(self.scope, rebase=True)
        self.assertTrue(payload["pulled"])
        self.assertTrue(payload.get("rebased") or not payload.get("rebase_in_progress"))
        self.assertTrue((self.root / "from-other.txt").is_file())
        self.assertTrue((self.root / "from-self.txt").is_file())

    def test_stash_apply_and_drop(self) -> None:
        (self.root / "a.txt").write_text("stashed\n")
        git_stash_payload(self.scope, op="push")
        listed = git_stash_payload(self.scope, op="list")
        self.assertGreaterEqual(listed.get("stash_count") or 0, 1)
        applied = git_stash_payload(self.scope, op="apply", index=0)
        self.assertTrue(applied["applied"])
        self.assertEqual((self.root / "a.txt").read_text(), "stashed\n")
        dropped = git_stash_payload(self.scope, op="drop", index=0)
        self.assertTrue(dropped["dropped"])
        self.assertEqual(dropped.get("stash_count") or 0, 0)

    def test_stash_rejects_unknown_op(self) -> None:
        with self.assertRaises(ProjectSearchError) as ctx:
            git_stash_payload(self.scope, op="explode")
        self.assertEqual(ctx.exception.status, 400)

    def test_diff_ignore_whitespace(self) -> None:
        (self.root / "a.txt").write_text("one  \n")
        normal = git_diff_payload(self.scope, "a.txt")
        ignored = git_diff_payload(self.scope, "a.txt", ignore_whitespace=True)
        self.assertIn("-one", normal["diff"] or "")
        self.assertTrue(ignored.get("ignore_whitespace"))
        # Only trailing whitespace changed relative to "one\n".
        self.assertFalse((ignored["diff"] or "").strip())


if __name__ == "__main__":
    unittest.main()
