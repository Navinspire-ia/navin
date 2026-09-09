# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bake-off harness: champion vs baseline on the shared code corpus."""

from __future__ import annotations

import unittest
from pathlib import Path

from navin.evals.bakeoff import (
    BASELINE_PROFILE,
    NAVIN_PROFILE,
    ProfiledModel,
    run_bakeoff,
)
from navin.evals.runner import MockModel

DATASET = Path(__file__).resolve().parents[1] / "datasets" / "code_agent_v1.jsonl"


class WeakBaseline:
    """Baseline that never mentions verify - simulates a weaker agent loop."""

    def __init__(self) -> None:
        self._inner = MockModel()

    def complete(self, prompt: str) -> str:
        return self._inner.complete(prompt).replace("verify", "done")


class BakeoffTest(unittest.TestCase):
    def test_corpus_matches_plan_sizes(self) -> None:
        rows = [
            line
            for line in DATASET.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertGreaterEqual(len(rows), 50)
        import json

        by_cat: dict[str, int] = {}
        for row in rows:
            cat = str(json.loads(row).get("category"))
            by_cat[cat] = by_cat.get(cat, 0) + 1
        self.assertEqual(by_cat.get("bugfix"), 15)
        self.assertEqual(by_cat.get("refactor"), 10)
        self.assertEqual(by_cat.get("feature"), 10)
        self.assertEqual(by_cat.get("test_fail"), 10)
        self.assertEqual(by_cat.get("debug"), 5)

    def test_same_model_ties_and_passes(self) -> None:
        report = run_bakeoff(
            DATASET, champion=MockModel(), baseline=MockModel(),
        )
        self.assertTrue(report.ok)
        self.assertEqual(report.delta_rate, 0.0)
        self.assertEqual(
            report.champion.overall_total, report.baseline.overall_total,
        )

    def test_detects_weaker_baseline(self) -> None:
        report = run_bakeoff(
            DATASET, champion=MockModel(), baseline=WeakBaseline(),
        )
        self.assertTrue(report.ok)
        self.assertGreater(report.delta_rate, 0.0)
        self.assertLess(
            report.baseline.overall_rate, report.champion.overall_rate,
        )

    def test_detects_weaker_champion(self) -> None:
        report = run_bakeoff(
            DATASET, champion=WeakBaseline(), baseline=MockModel(),
        )
        self.assertFalse(report.ok)
        self.assertLess(report.delta_rate, 0.0)

    def test_profiles_are_distinct(self) -> None:
        self.assertIn("apply_patch", NAVIN_PROFILE)
        self.assertIn("verify", NAVIN_PROFILE)
        self.assertNotIn("apply_patch", BASELINE_PROFILE)
        wrapped = ProfiledModel(MockModel(), NAVIN_PROFILE)
        self.assertIn("apply_patch", wrapped.complete("fix the bug"))


if __name__ == "__main__":
    unittest.main()
