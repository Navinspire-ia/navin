# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Offline smoke evals + scoreboard/gate against the mock model."""

from __future__ import annotations

import unittest
from pathlib import Path

from navin.evals.runner import (
    MockModel,
    load_dataset,
    release_gate,
    run_dataset,
    scoreboard,
)

DATASETS = Path(__file__).resolve().parents[1] / "datasets"
SMOKE = DATASETS / "smoke.jsonl"
CODE = DATASETS / "code_agent_v1.jsonl"


class EvalsHarnessTest(unittest.TestCase):
    def test_dataset_loads(self) -> None:
        cases = load_dataset(SMOKE)
        self.assertGreaterEqual(len(cases), 3)
        self.assertTrue(all(c.expect_contains for c in cases))

    def test_smoke_dataset_passes_with_mock_model(self) -> None:
        results = run_dataset(SMOKE, model=MockModel())
        failed = [r for r in results if not r.passed]
        self.assertEqual(
            failed,
            [],
            msg=[(r.case_id, r.missing, r.output) for r in failed],
        )

    def test_mock_model_can_fail_a_case(self) -> None:
        cases = load_dataset(SMOKE)
        bad = {case.input: "irrelevant output" for case in cases}
        model = MockModel(replies=bad)
        results = run_dataset(SMOKE, model=model)
        self.assertTrue(any(not r.passed for r in results))

    def test_runner_reports_missing_needles(self) -> None:
        model = MockModel(replies={"hello / ping the agent": "navin mock reply"})
        results = run_dataset(SMOKE, model=model)
        ping = next(r for r in results if r.case_id == "smoke-ping")
        self.assertFalse(ping.passed)
        self.assertIn("pong", ping.missing)

    def test_code_corpus_loads_with_categories(self) -> None:
        cases = load_dataset(CODE)
        self.assertGreaterEqual(len(cases), 10)
        cats = {c.category for c in cases}
        self.assertIn("bugfix", cats)
        self.assertTrue(any(c.expect_contains for c in cases))

    def test_scoreboard_and_release_gate(self) -> None:
        results = run_dataset(CODE, model=MockModel())
        board = scoreboard(results)
        self.assertGreater(board.overall_total, 0)
        self.assertTrue(board.by_category)
        gate = release_gate(board, min_overall=0.5, min_per_category=0.0)
        self.assertTrue(gate.ok, gate.reasons)
        strict = release_gate(board, min_overall=1.01, min_per_category=1.01)
        self.assertFalse(strict.ok)


if __name__ == "__main__":
    unittest.main()
