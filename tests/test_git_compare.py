# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for comparing two arbitrary files from the explorer."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from navin.security.workspace_access import default_workspace_scope
from navin.webui.project_search import ProjectSearchError, git_diff_payload


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class GitCompareTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        subprocess.run(  # noqa: S603
            ["git", "init", "-q"], cwd=self.root, check=True,  # noqa: S607
        )
        (self.root / "left.txt").write_text("alpha\nbeta\n")
        (self.root / "right.txt").write_text("alpha\ngamma\n")
        (self.root / "same.txt").write_text("alpha\nbeta\n")

    def compare(self, file: str, against: str | None):
        return git_diff_payload(
            default_workspace_scope(self.root, True), file, against,
        )

    def test_two_different_files_produce_a_diff(self):
        payload = self.compare("right.txt", "left.txt")
        self.assertEqual(payload["path"], "right.txt")
        self.assertEqual(payload["against"], "left.txt")
        self.assertFalse(payload["untracked"])
        self.assertIn("-beta", payload["diff"])
        self.assertIn("+gamma", payload["diff"])

    def test_two_identical_files_produce_an_empty_diff(self):
        # git exits 0 here and 1 above, so an empty body has to be the signal
        # rather than the exit status.
        self.assertEqual(self.compare("same.txt", "left.txt")["diff"], "")

    def test_without_a_second_path_it_still_diffs_against_head(self):
        payload = self.compare("left.txt", None)
        self.assertIsNone(payload["against"])
        self.assertTrue(payload["untracked"])

    def test_a_second_path_outside_the_project_is_refused(self):
        with self.assertRaises(ProjectSearchError) as ctx:
            self.compare("left.txt", "../../etc/passwd")
        self.assertEqual(ctx.exception.status, 403)

    def test_an_empty_second_path_is_treated_as_absent(self):
        self.assertIsNone(self.compare("left.txt", "   ")["against"])


if __name__ == "__main__":
    unittest.main()
