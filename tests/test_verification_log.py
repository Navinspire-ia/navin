# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The verification log is the proof behind "the tests passed".

Only the quality tools write it, the board's done-gate reads it. If recording
silently broke, every agent close would be refused (or worse, a stale pass
would keep unlocking closes), so the writing hooks and the refusal logic get
pinned here.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from navin.agent.tools.quality import _record_test_outcomes, _record_verify_report
from navin.quality import verification_log as vlog
from navin.quality.testing import TestOutcome
from navin.quality.verify import (
    VERDICT_CLEAN,
    VERDICT_NO_CHANGES,
    VERDICT_TEST_FAILURES,
    VerificationReport,
)


class _LogTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class RecordRoundTripTest(_LogTestBase):
    def test_record_then_read_back(self) -> None:
        vlog.record_verification(
            self.root, source="verify", ok=True, tests_ran=True, summary="clean",
        )
        last = vlog.last_verification(self.root)
        assert last is not None
        self.assertTrue(last["ok"])
        self.assertTrue(last["tests_ran"])
        self.assertEqual(last["source"], "verify")

    def test_log_is_bounded(self) -> None:
        for i in range(30):
            vlog.record_verification(self.root, source="test_run", ok=True, summary=str(i))
        entries = json.loads(vlog._log_path(self.root).read_text(encoding="utf-8"))
        self.assertLessEqual(len(entries), 20)
        self.assertEqual(entries[-1]["summary"], "29")

    def test_corrupt_log_reads_as_empty_and_recovers(self) -> None:
        path = vlog._log_path(self.root)
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(vlog.last_verification(self.root))
        vlog.record_verification(self.root, source="verify", ok=True)
        self.assertIsNotNone(vlog.last_verification(self.root))


class RefusalLogicTest(_LogTestBase):
    def test_no_run_recorded_refuses(self) -> None:
        problem = vlog.refusal_to_close_without_proof(self.root)
        assert problem is not None
        self.assertIn("no verification run", problem)

    def test_fresh_green_run_allows(self) -> None:
        vlog.record_verification(self.root, source="verify", ok=True, tests_ran=True)
        self.assertIsNone(vlog.refusal_to_close_without_proof(self.root))

    def test_red_run_refuses_with_summary(self) -> None:
        vlog.record_verification(
            self.root, source="test_run", ok=False, summary="2 failed",
        )
        problem = vlog.refusal_to_close_without_proof(self.root)
        assert problem is not None
        self.assertIn("FAILED", problem)
        self.assertIn("2 failed", problem)

    def test_stale_green_run_refuses(self) -> None:
        vlog.record_verification(self.root, source="verify", ok=True, tests_ran=True)
        path = vlog._log_path(self.root)
        entries = json.loads(path.read_text(encoding="utf-8"))
        entries[-1]["ts"] -= vlog.DEFAULT_MAX_AGE_S + 60
        path.write_text(json.dumps(entries), encoding="utf-8")
        problem = vlog.refusal_to_close_without_proof(self.root)
        assert problem is not None
        self.assertIn("min old", problem)

    def test_lint_only_pass_refuses_when_tests_required(self) -> None:
        vlog.record_verification(self.root, source="verify", ok=True, tests_ran=False)
        self.assertIsNone(vlog.refusal_to_close_without_proof(self.root))
        problem = vlog.refusal_to_close_without_proof(self.root, require_tests=True)
        assert problem is not None
        self.assertIn("did not run any tests", problem)


class QualityToolHooksTest(_LogTestBase):
    """The verify / test_run tools must actually feed the log."""

    def test_test_run_outcomes_are_recorded(self) -> None:
        outcomes = [
            TestOutcome(runner="pytest", ran=True, passed=45, failed=0, total=45),
        ]
        _record_test_outcomes(self.root, outcomes, target="tests/test_x.py")
        last = vlog.last_verification(self.root)
        assert last is not None
        self.assertTrue(last["ok"])
        self.assertTrue(last["tests_ran"])
        self.assertIn("45 passed", last["summary"])
        self.assertIn("tests/test_x.py", last["summary"])

    def test_failing_test_run_is_recorded_red(self) -> None:
        outcomes = [
            TestOutcome(runner="pytest", ran=True, passed=40, failed=2, total=42),
        ]
        _record_test_outcomes(self.root, outcomes)
        last = vlog.last_verification(self.root)
        assert last is not None
        self.assertFalse(last["ok"])

    def test_suite_that_did_not_run_invalidates_an_earlier_pass(self) -> None:
        vlog.record_verification(self.root, source="test_run", ok=True, tests_ran=True)
        outcomes = [
            TestOutcome(runner="pytest", ran=False, skipped_reason="no tests found"),
        ]
        _record_test_outcomes(self.root, outcomes)
        last = vlog.last_verification(self.root)
        assert last is not None
        self.assertFalse(last["ok"])
        self.assertFalse(last["tests_ran"])
        self.assertIsNotNone(vlog.refusal_to_close_without_proof(self.root, require_tests=True))

    def test_clean_verify_report_is_recorded_green(self) -> None:
        report = VerificationReport(
            verdict=VERDICT_CLEAN,
            test_outcomes=[
                TestOutcome(runner="pytest", ran=True, passed=12, total=12),
            ],
        )
        _record_verify_report(self.root, report)
        last = vlog.last_verification(self.root)
        assert last is not None
        self.assertTrue(last["ok"])
        self.assertTrue(last["tests_ran"])

    def test_failing_verify_report_is_recorded_red(self) -> None:
        report = VerificationReport(
            verdict=VERDICT_TEST_FAILURES,
            test_outcomes=[
                TestOutcome(runner="pytest", ran=True, passed=10, failed=2, total=12),
            ],
        )
        _record_verify_report(self.root, report)
        last = vlog.last_verification(self.root)
        assert last is not None
        self.assertFalse(last["ok"])

    def test_no_changes_verify_records_nothing(self) -> None:
        report = VerificationReport(verdict=VERDICT_NO_CHANGES)
        _record_verify_report(self.root, report)
        self.assertIsNone(vlog.last_verification(self.root))


if __name__ == "__main__":
    unittest.main()
