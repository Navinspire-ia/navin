# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""AI commit-message sanitizer and working-tree context for the SCM IA button."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from navin.security.workspace_access import default_workspace_scope
from navin.webui.git_commit_message import (
    SUBJECT_MAX,
    generate_commit_message_payload,
    sanitize_commit_message,
)
from navin.webui.project_search import ProjectSearchError, git_commit_diff_context


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )


class SanitizeCommitMessageTest(unittest.TestCase):
    def test_plain_subject_is_kept(self):
        self.assertEqual(
            sanitize_commit_message("Add career dashboard shortcut"),
            "Add career dashboard shortcut",
        )

    def test_fences_and_quotes_are_stripped(self):
        self.assertEqual(
            sanitize_commit_message('```\n"Fix the login timeout"\n```'),
            "Fix the login timeout",
        )

    def test_leading_label_is_dropped(self):
        self.assertEqual(
            sanitize_commit_message("Commit message: Update git panel"),
            "Update git panel",
        )

    def test_subject_is_clipped_to_72_on_a_word_boundary(self):
        long_subject = (
            "Rewrite the entire source control panel so every pending file "
            "is described in exhaustive detail for reviewers"
        )
        out = sanitize_commit_message(long_subject)
        self.assertLessEqual(len(out.splitlines()[0]), SUBJECT_MAX)
        self.assertNotIn("\n", out)
        self.assertFalse(out.endswith("."))

    def test_trailing_period_is_removed_from_the_subject(self):
        self.assertEqual(
            sanitize_commit_message("Fix the overflow."),
            "Fix the overflow",
        )

    def test_body_is_dropped_keep_subject_only(self):
        body = " ".join(["word"] * 80)
        out = sanitize_commit_message(f"Fix the overflow\n\n{body}")
        self.assertEqual(out, "Fix the overflow")
        self.assertNotIn("\n", out)

    def test_instruction_echo_is_rejected(self):
        raw = (
            "We need to generate a conventional commit message based on the diff\n"
            "\n"
            "The description should be concrete: what changed and why. Key changes:\n"
            "- Refactored git_commit_message.py: new system prompt with\n"
            "conventional commits, _first_conventional_line"
        )
        self.assertEqual(sanitize_commit_message(raw), "")

    def test_conventional_line_is_kept_when_buried_in_echo(self):
        raw = (
            "We need to generate a conventional commit message based on the diff\n"
            "feat(git): extraire un sujet conventionnel depuis le diff\n"
            "Key changes: ignore this"
        )
        self.assertEqual(
            sanitize_commit_message(raw),
            "feat(git): extraire un sujet conventionnel depuis le diff",
        )

    def test_unicode_dashes_become_hyphens(self):
        self.assertEqual(
            sanitize_commit_message("Fix timeout \u2014 retry the request"),
            "Fix timeout - retry the request",
        )

    def test_empty_input_stays_empty(self):
        self.assertEqual(sanitize_commit_message("   "), "")

    def test_think_blocks_are_stripped(self):
        raw = "<think>plan the subject</think>\nFix the login timeout"
        self.assertEqual(sanitize_commit_message(raw), "Fix the login timeout")

    def test_conventional_line_inside_think_is_kept(self):
        raw = (
            "<think>maybe chore\n"
            "feat(git): generate a conventional commit from the diff\n"
            "ok</think>"
        )
        self.assertEqual(
            sanitize_commit_message(raw),
            "feat(git): generate a conventional commit from the diff",
        )


class FallbackCommitMessageTest(unittest.TestCase):
    def test_one_file_uses_conventional_form(self):
        from navin.webui.git_commit_message import fallback_commit_message

        out = fallback_commit_message({"files": ["webui/src/DevGitPanel.tsx"]})
        self.assertTrue(out.startswith("feat(git):"))
        self.assertIn("commit", out.lower())
        self.assertNotIn("related diff", out)
        self.assertLessEqual(len(out), 72)

    def test_french_fallback_is_infinitive(self):
        from navin.webui.git_commit_message import fallback_commit_message

        out = fallback_commit_message(
            {"files": ["navin/webui/git_commit_message.py"]},
            lang="fr",
        )
        self.assertIn("generer", out)
        self.assertIn("commit", out)
        self.assertNotIn("ajuster", out)
        self.assertNotIn("diff associe", out)
        self.assertLessEqual(len(out), 72)

    def test_multi_file_fallback_names_the_change(self):
        from navin.webui.git_commit_message import fallback_commit_message

        out = fallback_commit_message(
            {
                "files": [
                    "crates/navin-engine",
                    "navin/webui/git_commit_message.py",
                    "webui/src/components/dev/DevGitPanel.tsx",
                ]
            }
        )
        self.assertEqual(
            out,
            "feat(git): generate a conventional commit message from the diff",
        )

    def test_poor_placeholder_is_rejected(self):
        self.assertEqual(
            sanitize_commit_message(
                "feat(webui): adjust navin-engine and the related diff"
            ),
            "",
        )


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class GitCommitDiffContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "test@navin.local")
        _git(self.root, "config", "user.name", "Navin Test")
        (self.root / "a.txt").write_text("one\n")
        _git(self.root, "add", "a.txt")
        _git(self.root, "commit", "-q", "-m", "init")

    @property
    def scope(self):
        return default_workspace_scope(self.root, True)

    def test_tracked_edit_appears_in_the_diff(self):
        (self.root / "a.txt").write_text("two\n")
        ctx = git_commit_diff_context(self.scope)
        self.assertEqual(ctx["files"], ["a.txt"])
        self.assertIn("a.txt", ctx["diff"])
        self.assertIn("two", ctx["diff"])
        self.assertIn("init", ctx["recent"])

    def test_untracked_file_is_previewed(self):
        (self.root / "new.txt").write_text("brand new\n")
        ctx = git_commit_diff_context(self.scope)
        self.assertIn("new.txt", ctx["files"])
        self.assertIn("brand new", ctx["diff"])

    def test_paths_filter_drops_other_files(self):
        (self.root / "a.txt").write_text("two\n")
        (self.root / "b.txt").write_text("other\n")
        ctx = git_commit_diff_context(self.scope, ["a.txt"])
        self.assertEqual(ctx["files"], ["a.txt"])
        self.assertNotIn("other", ctx["diff"])

    def test_clean_tree_is_refused(self):
        with self.assertRaises(ProjectSearchError) as ctx:
            git_commit_diff_context(self.scope)
        self.assertEqual(ctx.exception.status, 409)


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class GenerateCommitMessageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "test@navin.local")
        _git(self.root, "config", "user.name", "Navin Test")
        (self.root / "a.txt").write_text("one\n")
        _git(self.root, "add", "a.txt")
        _git(self.root, "commit", "-q", "-m", "init")
        (self.root / "a.txt").write_text("two\n")

    async def test_empty_model_output_falls_back_to_file_names(self):
        with patch(
            "navin.webui.git_commit_message._ask_commit",
            new=AsyncMock(return_value=("<think>hmm</think>", "", "stub", "fast")),
        ):
            payload = await generate_commit_message_payload(
                default_workspace_scope(self.root, True)
            )
        self.assertIn("a.txt", payload["message"])
        self.assertNotIn("related diff", payload["message"])
        self.assertLessEqual(len(payload["message"].splitlines()[0]), SUBJECT_MAX)

    async def test_model_output_is_sanitized_before_return(self):
        with patch(
            "navin.webui.git_commit_message._ask_commit",
            new=AsyncMock(
                return_value=(
                    "```\nfeat(git): generate a conventional commit from the diff\n```",
                    "We need to generate a conventional commit message",
                    "stub",
                    "fast",
                )
            ),
        ):
            payload = await generate_commit_message_payload(
                default_workspace_scope(self.root, True)
            )
        self.assertEqual(
            payload["message"],
            "feat(git): generate a conventional commit from the diff",
        )
        self.assertEqual(payload["files"], ["a.txt"])
        self.assertEqual(payload["model"], "stub")

    async def test_reasoning_dump_is_ignored_when_content_is_empty(self):
        with patch(
            "navin.webui.git_commit_message._ask_commit",
            new=AsyncMock(
                return_value=(
                    "",
                    "We need to generate a conventional commit message based on the diff\n"
                    "The description should be concrete",
                    "stub",
                    "fast",
                )
            ),
        ):
            payload = await generate_commit_message_payload(
                default_workspace_scope(self.root, True)
            )
        self.assertNotIn("We need to", payload["message"])
        self.assertLessEqual(len(payload["message"].splitlines()), 1)
