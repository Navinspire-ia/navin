"""Offline smoke evals against the mock model (no network)."""

from __future__ import annotations

import unittest
from pathlib import Path

from navin.evals.runner import MockModel, load_dataset, run_dataset

DATASET = (
    Path(__file__).resolve().parents[1]
    / "navin"
    / "evals"
    / "datasets"
    / "smoke.jsonl"
)


class EvalsSmokeTest(unittest.TestCase):
    def test_dataset_loads(self) -> None:
        cases = load_dataset(DATASET)
        self.assertGreaterEqual(len(cases), 3)
        self.assertTrue(all(c.expect_contains for c in cases))

    def test_smoke_dataset_passes_with_mock_model(self) -> None:
        results = run_dataset(DATASET, model=MockModel())
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [], msg=[(r.case_id, r.missing, r.output) for r in failed])

    def test_mock_model_can_fail_a_case(self) -> None:
        # Force a wrong reply on a real dataset case to exercise the fail path.
        first = load_dataset(DATASET)[0]
        model = MockModel(replies={first.input: "nope"})
        results = run_dataset(DATASET, model=model)
        self.assertTrue(any(not r.passed for r in results))


if __name__ == "__main__":
    unittest.main()
