"""Phase 2.5: versioned Code agent eval corpus is loadable and categorized."""

from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

CORPUS = (
    Path(__file__).resolve().parents[1]
    / "navin"
    / "evals"
    / "datasets"
    / "code_agent_v1.jsonl"
)

REQUIRED_CATEGORIES = {
    "bugfix",
    "refactor",
    "feature",
    "test_fail",
    "debug",
    "ask",
    "plan",
}


class CodeAgentEvalCorpusTest(unittest.TestCase):
    def _rows(self) -> list[dict]:
        rows: list[dict] = []
        for line in CORPUS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append(json.loads(line))
        return rows

    def test_corpus_exists_and_is_categorized(self) -> None:
        self.assertTrue(CORPUS.is_file(), CORPUS)
        rows = self._rows()
        self.assertGreaterEqual(len(rows), 10)
        categories = {str(row.get("category") or "") for row in rows}
        self.assertTrue(REQUIRED_CATEGORIES <= categories, categories)
        counts = Counter(str(row.get("category") or "") for row in rows)
        self.assertGreaterEqual(counts["bugfix"], 3)
        for row in rows:
            self.assertTrue(row.get("id"))
            self.assertTrue(row.get("prompt"))
            expect = row.get("expect") or {}
            self.assertIsInstance(expect, dict)

    def test_plan_category_has_at_least_five_cases(self) -> None:
        """P2-6: Plan/mission-ledger regressions are covered by the gate corpus."""
        plan_rows = [
            row for row in self._rows() if str(row.get("category")) == "plan"
        ]
        self.assertGreaterEqual(len(plan_rows), 5)
        for row in plan_rows:
            expect = row.get("expect") or {}
            self.assertEqual(expect.get("composer_mode"), "plan", row.get("id"))
            # A plan turn never edits: every case forbids at least apply_patch.
            self.assertIn("apply_patch", expect.get("tools_exclude") or [], row.get("id"))

    def test_plan_scenarios_cover_the_ledger_board_and_spawn_paths(self) -> None:
        joined = " ".join(
            str(row.get("prompt") or "")
            for row in self._rows()
            if str(row.get("category")) == "plan"
        ).lower()
        for needle in ("board", "ledger", "hand off", "spawn results", "blocked"):
            self.assertIn(needle, joined)

    def test_release_gate_passes_with_the_plan_category_required(self) -> None:
        """P2-6 acceptance: the offline gate is 100% including the plan cases."""
        from navin.evals.runner import release_gate, run_dataset, scoreboard

        results = run_dataset(CORPUS)
        failures = [r for r in results if not r.passed]
        self.assertEqual(
            failures,
            [],
            [(r.case_id, r.missing) for r in failures],
        )
        gate = release_gate(
            scoreboard(results),
            min_overall=1.0,
            min_per_category=1.0,
            required_categories=REQUIRED_CATEGORIES,
        )
        self.assertTrue(gate.ok, gate.reasons)


if __name__ == "__main__":
    unittest.main()
