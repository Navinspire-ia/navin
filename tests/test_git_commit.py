"""Tests for the source-control panel's Commit / Commit & Push endpoint."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from navin.security.workspace_access import default_workspace_scope
from navin.webui.project_search import (
    ProjectSearchError,
    git_changes_payload,
    git_commit_payload,
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class GitCommitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "test@navin.local")
        _git(self.root, "config", "user.name", "Navin Test")
        (self.root / "a.txt").write_text("one\n")

    @property
    def scope(self):
        return default_workspace_scope(self.root, True)

    def test_commit_stages_everything_and_commits(self):
        payload = git_commit_payload(self.scope, "first commit", push=False)
        self.assertTrue(payload["committed"])
        self.assertFalse(payload["pushed"])
        self.assertEqual(payload["branch"], "main")
        log = _git(self.root, "log", "--oneline")
        self.assertIn("first commit", log.stdout)
        # The working tree is clean afterwards.
        changes = git_changes_payload(self.scope)
        self.assertEqual(changes["files"], [])

    def test_empty_message_is_refused(self):
        with self.assertRaises(ProjectSearchError) as ctx:
            git_commit_payload(self.scope, "   ", push=False)
        self.assertEqual(ctx.exception.status, 400)

    def test_nothing_to_commit_is_not_an_error(self):
        git_commit_payload(self.scope, "first", push=False)
        payload = git_commit_payload(self.scope, "second", push=False)
        self.assertFalse(payload["committed"])
        self.assertFalse(payload["pushed"])

    def test_push_without_remote_surfaces_gits_message(self):
        git_commit_payload(self.scope, "first", push=False)
        (self.root / "a.txt").write_text("two\n")
        with self.assertRaises(ProjectSearchError) as ctx:
            git_commit_payload(self.scope, "second", push=True)
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("push", ctx.exception.message.lower())

    def test_bare_push_does_not_create_a_commit(self):
        git_commit_payload(self.scope, "first", push=False)
        (self.root / "a.txt").write_text("two\n")
        with self.assertRaises(ProjectSearchError):
            # No remote configured, so the push itself fails ...
            git_commit_payload(self.scope, "", push=True, commit=False)
        # ... but the pending change was never committed by the bare push.
        changes = git_changes_payload(self.scope)
        self.assertEqual(len(changes["files"]), 1)

    def test_changes_payload_reports_branch_state(self):
        git_commit_payload(self.scope, "first", push=False)
        payload = git_changes_payload(self.scope)
        self.assertEqual(payload["branch"], "main")
        self.assertFalse(payload["has_upstream"])
        self.assertEqual(payload["ahead"], 0)
        self.assertEqual(payload["behind"], 0)

    def test_push_to_local_remote_reports_ahead_then_zero(self):
        # A file:// bare repo stands in for origin; ahead goes 1 -> 0 on push.
        remote = Path(tempfile.mkdtemp(prefix="navin-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        _git(remote, "init", "-q", "--bare")
        _git(self.root, "remote", "add", "origin", str(remote))

        payload = git_commit_payload(self.scope, "first", push=True)
        self.assertTrue(payload["committed"])
        self.assertTrue(payload["pushed"])
        self.assertTrue(payload["has_upstream"])
        self.assertEqual(payload["ahead"], 0)


if __name__ == "__main__":
    unittest.main()
