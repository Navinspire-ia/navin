# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the WebUI Git import helper."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.webui.git_clone import (
    GitCloneError,
    clone_git_project,
    normalize_git_url,
    repo_name_from_url,
)


class NormalizeGitUrlTest(unittest.TestCase):
    def test_https_passthrough(self) -> None:
        self.assertEqual(
            normalize_git_url("https://github.com/org/repo.git"),
            "https://github.com/org/repo.git",
        )

    def test_owner_repo_shorthand(self) -> None:
        self.assertEqual(
            normalize_git_url("org/repo"),
            "https://github.com/org/repo",
        )

    def test_host_without_scheme(self) -> None:
        self.assertEqual(
            normalize_git_url("github.com/org/repo"),
            "https://github.com/org/repo",
        )

    def test_strips_query_and_fragment(self) -> None:
        self.assertEqual(
            normalize_git_url("https://github.com/org/repo?tab=readme#install"),
            "https://github.com/org/repo",
        )

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(GitCloneError):
            normalize_git_url("not a url")


class RepoNameTest(unittest.TestCase):
    def test_from_https(self) -> None:
        self.assertEqual(
            repo_name_from_url("https://github.com/org/my-app.git"),
            "my-app",
        )

    def test_from_ssh(self) -> None:
        self.assertEqual(
            repo_name_from_url("git@github.com:org/my_app.git"),
            "my_app",
        )


class CloneGitProjectTest(unittest.TestCase):
    def test_clone_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            fake = mock.Mock(
                returncode=0,
                stdout="",
                stderr="",
            )

            def _run(argv, **_kwargs):
                # Mimic git clone creating the target folder.
                Path(argv[-1]).mkdir(parents=True, exist_ok=True)
                return fake

            with mock.patch("navin.webui.git_clone.shutil.which", return_value="/usr/bin/git"):
                with mock.patch("navin.webui.git_clone.subprocess.run", side_effect=_run) as run:
                    result = clone_git_project(
                        "https://github.com/org/demo.git",
                        parent=str(parent),
                    )
            self.assertEqual(result["name"], "demo")
            self.assertEqual(result["path"], str(parent / "demo"))
            self.assertFalse(result["reused"])
            self.assertEqual(run.call_args.args[0][:3], ["git", "clone", "--depth"])

    def test_reuses_existing_same_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "demo"
            target.mkdir()
            (target / "README.md").write_text("hi", encoding="utf-8")

            def _run(argv, **_kwargs):
                # remote get-url origin
                return mock.Mock(
                    returncode=0,
                    stdout="https://github.com/org/demo.git\n",
                    stderr="",
                )

            with mock.patch("navin.webui.git_clone.shutil.which", return_value="/usr/bin/git"):
                with mock.patch("navin.webui.git_clone.subprocess.run", side_effect=_run):
                    result = clone_git_project(
                        "org/demo",
                        parent=str(parent),
                    )
            self.assertTrue(result["reused"])
            self.assertEqual(result["path"], str(target))

    def test_rejects_conflicting_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "demo"
            target.mkdir()
            (target / "other.txt").write_text("x", encoding="utf-8")

            def _run(_argv, **_kwargs):
                return mock.Mock(returncode=1, stdout="", stderr="")

            with mock.patch("navin.webui.git_clone.shutil.which", return_value="/usr/bin/git"):
                with mock.patch("navin.webui.git_clone.subprocess.run", side_effect=_run):
                    with self.assertRaises(GitCloneError) as ctx:
                        clone_git_project(
                            "https://github.com/org/demo.git",
                            parent=str(parent),
                        )
            self.assertIn("already exists", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
