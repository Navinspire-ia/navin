# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""An edit that breaks the file should say so in the same breath.

The verification tooling was already good; nothing triggered it. A model that
forgot to call ``verify`` reported the edit as done and the error surfaced a
turn later. These cover the findings now riding back on the write itself, and
just as importantly the quiet path: a clean edit must read exactly as it did
before this existed, or every successful write grows noise.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.agent.tools import edit_feedback
from navin.quality.linters import Diagnostic, LinterResult


def diagnostic(path: str, *, severity: str = "error", message: str = "boom") -> Diagnostic:
    return Diagnostic(
        path=path,
        line=1,
        col=1,
        end_line=1,
        end_col=1,
        severity=severity,
        code="E999",
        message=message,
        tool="ruff",
    )


def report(paths: list[Path], workspace: Path, results: list[LinterResult]) -> str:
    with patch.object(edit_feedback, "_MAX_FILES", 10), \
         patch("navin.quality.linters.lint_file", return_value=results):
        return edit_feedback.diagnostics_after_write(paths, workspace=workspace)


class QuietPathTest(unittest.TestCase):
    """The common case: nothing to say, so say nothing."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_clean_file_adds_nothing(self) -> None:
        target = self.workspace / "a.py"
        target.write_text("x = 1\n")
        self.assertEqual(report([target], self.workspace, [LinterResult("ruff", ran=True)]), "")

    def test_a_linter_that_could_not_run_is_not_reported_as_clean_or_broken(self) -> None:
        # "ruff not installed" is not a finding about the user's code.
        skipped = LinterResult("ruff", ran=False, skipped_reason="ruff not installed")
        skipped.diagnostics = [diagnostic("a.py")]
        target = self.workspace / "a.py"
        target.write_text("x = 1\n")
        self.assertEqual(report([target], self.workspace, [skipped]), "")

    def test_no_paths_costs_nothing(self) -> None:
        self.assertEqual(edit_feedback.diagnostics_after_write([], workspace=self.workspace), "")

    def test_a_missing_workspace_is_not_an_error(self) -> None:
        self.assertEqual(
            edit_feedback.diagnostics_after_write([Path("/tmp/a.py")], workspace=None), ""
        )


class FindingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.target = self.workspace / "a.py"
        self.target.write_text("x = 1\n")

    def test_a_problem_is_named_with_its_location(self) -> None:
        result = LinterResult("ruff", ran=True)
        result.diagnostics = [diagnostic("a.py", message="Undefined name `foo`")]
        output = report([self.target], self.workspace, [result])
        self.assertIn("Diagnostics for the files just written:", output)
        self.assertIn("a.py:1:1 error [E999] Undefined name `foo`", output)

    def test_errors_are_listed_before_warnings(self) -> None:
        # A truncated list should drop the notes, never the failures.
        result = LinterResult("ruff", ran=True)
        result.diagnostics = [
            diagnostic("a.py", severity="warning", message="unused"),
            diagnostic("a.py", severity="error", message="syntax"),
        ]
        output = report([self.target], self.workspace, [result])
        self.assertLess(output.index("syntax"), output.index("unused"))

    def test_an_avalanche_is_capped_and_says_so(self) -> None:
        result = LinterResult("ruff", ran=True)
        result.diagnostics = [
            diagnostic("a.py", message=f"problem {index}") for index in range(40)
        ]
        output = report([self.target], self.workspace, [result])
        self.assertEqual(len(output.splitlines()), edit_feedback._MAX_DIAGNOSTICS + 2)
        self.assertIn("and 28 more", output)

    def test_information_level_notes_are_left_out(self) -> None:
        result = LinterResult("ruff", ran=True)
        result.diagnostics = [diagnostic("a.py", severity="info", message="fyi")]
        self.assertEqual(report([self.target], self.workspace, [result]), "")

    def test_a_finding_with_nothing_to_say_is_dropped(self) -> None:
        # pyflakes_syntax reports a bare position with no message; on its own it
        # tells the model there is a problem without saying which.
        result = LinterResult("pyflakes_syntax", ran=True)
        result.diagnostics = [diagnostic("a.py", message="")]
        self.assertEqual(report([self.target], self.workspace, [result]), "")

    def test_a_file_outside_the_workspace_is_skipped(self) -> None:
        # Linters are configured against the project root; their verdict on a
        # path outside it would be meaningless.
        result = LinterResult("ruff", ran=True)
        result.diagnostics = [diagnostic("elsewhere.py")]
        with TemporaryDirectory() as other:
            stray = Path(other) / "elsewhere.py"
            stray.write_text("x = 1\n")
            self.assertEqual(report([stray], self.workspace, [result]), "")

    def test_a_linter_that_raises_does_not_break_the_edit(self) -> None:
        with patch("navin.quality.linters.lint_file", side_effect=RuntimeError("nope")):
            self.assertEqual(
                edit_feedback.diagnostics_after_write([self.target], workspace=self.workspace),
                "",
            )

    def test_one_file_is_linted_once_even_if_named_twice(self) -> None:
        result = LinterResult("ruff", ran=True)
        result.diagnostics = [diagnostic("a.py")]
        with patch("navin.quality.linters.lint_file", return_value=[result]) as lint:
            edit_feedback.diagnostics_after_write(
                [self.target, self.target], workspace=self.workspace
            )
        self.assertEqual(lint.call_count, 1)


if __name__ == "__main__":
    unittest.main()
