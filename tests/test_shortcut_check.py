# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Shortcut check: skips, silenced checkers, swallowed errors, dropped asserts."""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from navin.agent.shortcut_check import build_shortcut_nudge, is_test_path, shortcut_findings


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> None:
    import os

    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root, check=True, capture_output=True, env={**os.environ, **(env or {})},
    )


class ShortcutFindingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _git(self.root, "init", "-q")
        (self.root / "tests").mkdir()
        (self.root / "app.py").write_text(
            "def total(a, b):\n    return a + b\n", encoding="utf-8",
        )
        (self.root / "tests" / "test_app.py").write_text(
            "from app import total\n\n\ndef test_total():\n"
            "    assert total(1, 2) == 3\n    assert total(0, 0) == 0\n",
            encoding="utf-8",
        )
        _git(self.root, "add", "-A")
        # Dated in the past so a turn starting now does not count it as its own.
        old = "2020-01-01T00:00:00"
        _git(self.root, "commit", "-q", "-m", "init", env={
            "GIT_AUTHOR_DATE": old, "GIT_COMMITTER_DATE": old,
        })

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, rel: str, text: str) -> None:
        (self.root / rel).write_text(text, encoding="utf-8")

    def test_real_fix_has_no_finding(self) -> None:
        self._write("app.py", "def total(a, b):\n    return int(a) + int(b)\n")
        self.assertEqual(shortcut_findings(self.root, ["app.py", "tests/test_app.py"]), [])

    def test_skip_and_dropped_assertion_in_a_test_are_named(self) -> None:
        self._write(
            "tests/test_app.py",
            "import pytest\nfrom app import total\n\n\n@pytest.mark.skip(reason='flaky')\n"
            "def test_total():\n    assert total(1, 2) == 3\n",
        )
        found = shortcut_findings(self.root, [str(self.root / "tests" / "test_app.py")])
        self.assertEqual(len(found), 2, found)
        self.assertIn("tests/test_app.py: test skipped", found[0])
        self.assertEqual(found[1], "tests/test_app.py: 1 assertion(s) removed from the tests")

    def test_silenced_checker_and_swallowed_error_in_source(self) -> None:
        self._write(
            "app.py",
            "def total(a, b):  # type: ignore\n    try:\n        return a + b\n"
            "    except Exception:\n        pass\n",
        )
        found = shortcut_findings(self.root, ["app.py"])
        self.assertEqual([item.split(" (")[0] for item in found], [
            "app.py: checker silenced", "app.py: error swallowed",
        ])

    def test_untracked_test_file_counts_as_added_and_js_skip_is_seen(self) -> None:
        self._write("tests/sum.test.ts", "it.skip('adds', () => { expect(1).toBe(1) })\n")
        found = shortcut_findings(self.root, ["tests/sum.test.ts"])
        self.assertEqual(len(found), 1, found)
        self.assertIn("it.skip", found[0])

    def test_changes_committed_during_the_turn_are_still_checked(self) -> None:
        started = time.time() - 1
        self._write("tests/test_app.py", "def test_total():\n    pass\n")
        _git(self.root, "commit", "-qam", "weaken")
        found = shortcut_findings(self.root, ["tests/test_app.py"], since=started)
        self.assertEqual(found, ["tests/test_app.py: 2 assertion(s) removed from the tests"])

    def test_skip_outside_tests_non_code_and_no_git_are_ignored(self) -> None:
        self._write("README.md", "# noqa\n")
        self._write("notes.py", "# pytest.skip( is documented here\n")
        self.assertEqual(shortcut_findings(self.root, ["README.md", "notes.py"]), [])
        with tempfile.TemporaryDirectory() as bare:
            (Path(bare) / "a.py").write_text("x = 1  # noqa\n", encoding="utf-8")
            self.assertEqual(shortcut_findings(bare, ["a.py"]), [])

    def test_helpers(self) -> None:
        self.assertTrue(is_test_path("tests/test_app.py"))
        self.assertTrue(is_test_path("src/sum.spec.tsx"))
        self.assertFalse(is_test_path("src/app.py"))
        message = build_shortcut_nudge(["a.py: checker silenced (`x`)"])
        self.assertIn("- a.py: checker silenced", message)
        self.assertNotIn("\u2014", message)
        self.assertNotIn("\u2013", message)


if __name__ == "__main__":
    unittest.main()
