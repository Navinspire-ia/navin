"""A truncated code index is never silent.

Past the file cap the index quietly holds a subset of the project, and an
empty lookup then reads as "does not exist" when it only means "not indexed".
Truncation must be visible in discovery, in the refresh summary, and appended
to every code_index lookup so the agent double-checks with grep/find_files.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from navin.agent.tools.code_index import CodeIndexTool
from navin.index.store import RefreshStats, discover_files


class DiscoveryTruncationTest(unittest.TestCase):
    def test_discovery_reports_the_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(3):
                (root / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
            with patch("navin.index.store._MAX_FILES", 2):
                files, _method, truncated = discover_files(root)
            self.assertTrue(truncated)
            self.assertEqual(len(files), 2)

    def test_a_project_under_the_cap_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            _files, _method, truncated = discover_files(root)
            self.assertFalse(truncated)

    def test_refresh_summary_names_the_truncation(self) -> None:
        stats = RefreshStats(
            total=60_000,
            parsed=60_000,
            reused=0,
            removed=0,
            truncated=True,
            duration_ms=1,
            discovery="git",
        )
        self.assertIn("truncated at", stats.summary())


class CoverageNoteTest(unittest.TestCase):
    """Every code_index lookup carries the caveat, not just refresh."""

    @staticmethod
    def _index(*, truncated: bool) -> SimpleNamespace:
        return SimpleNamespace(
            stats=SimpleNamespace(truncated=truncated, total=60_000)
        )

    def test_a_truncated_index_warns_on_every_lookup(self) -> None:
        noted = CodeIndexTool._with_coverage_note(
            self._index(truncated=True), "No definition found for 'foo'."
        )
        self.assertIn("does not prove absence", noted)
        self.assertIn("60000", noted)

    def test_a_complete_index_stays_quiet(self) -> None:
        out = CodeIndexTool._with_coverage_note(
            self._index(truncated=False), "No definition found for 'foo'."
        )
        self.assertNotIn("Warning", out)

    def test_errors_and_non_text_results_pass_through(self) -> None:
        error = "Error: action=search requires 'query'"
        self.assertEqual(
            CodeIndexTool._with_coverage_note(self._index(truncated=True), error),
            error,
        )
        self.assertIsNone(
            CodeIndexTool._with_coverage_note(self._index(truncated=True), None)
        )

    def test_an_index_without_stats_stays_quiet(self) -> None:
        out = CodeIndexTool._with_coverage_note(
            SimpleNamespace(stats=None), "hit: a.py"
        )
        self.assertEqual(out, "hit: a.py")


if __name__ == "__main__":
    unittest.main()
