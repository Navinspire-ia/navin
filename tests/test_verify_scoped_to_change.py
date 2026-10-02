# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""verify judges the change it was asked about, not the whole repository.

Measured in a CLI session: a CSS/TSX restyle ran the backend pytest suite,
whose webhook-secret failures came back as "fix the failing tests above". The
agent then spent the turn on JWT secrets and a Playwright install.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from navin.quality import testing
from navin.quality.verify import VERDICT_TEST_FAILURES, VerificationReport

TABLE = {"pytest": {"language": "python"}, "vitest": {"language": "javascript"}}


class RunnerScopeTest(unittest.TestCase):
    def _runners(self, changed: list[str]) -> list[str] | None:
        with mock.patch.object(testing, "runner_table", return_value=TABLE), \
             mock.patch.object(testing, "_primary_runners", return_value=["pytest", "vitest"]):
            return testing.runners_for_changes(Path("."), changed)

    def test_a_frontend_change_runs_only_frontend_suites(self) -> None:
        self.assertEqual(self._runners(["frontend/src/app/globals.css", "frontend/src/A.tsx"]), ["vitest"])

    def test_a_backend_change_runs_only_python(self) -> None:
        self.assertEqual(self._runners(["backend/api.py"]), ["pytest"])

    def test_a_mixed_change_runs_both(self) -> None:
        self.assertEqual(self._runners(["backend/api.py", "frontend/A.tsx"]), ["pytest", "vitest"])

    def test_an_unknown_file_kind_keeps_every_suite(self) -> None:
        self.assertIsNone(self._runners(["frontend/A.tsx", "docker-compose.yml"]))


def _report(changed: list[str], failure_file: str) -> VerificationReport:
    outcome = testing.TestOutcome(
        runner="pytest", ran=True, failed=1, total=1, exit_code=1,
        failures=[testing.TestFailure(name="test_webhook", file=failure_file)],
    )
    return VerificationReport(verdict=VERDICT_TEST_FAILURES, changed_paths=changed, test_outcomes=[outcome])


class UnrelatedFailureTest(unittest.TestCase):
    def test_failures_far_from_the_change_are_not_to_be_chased(self) -> None:
        report = _report(["frontend/src/app/globals.css"], "backend/tests/test_webhooks.py")
        self.assertTrue(report.failures_look_unrelated())
        self.assertIn("Do not chase them", report.recommendation())

    def test_failures_next_to_the_change_must_be_fixed(self) -> None:
        report = _report(["backend/services/webhooks.py"], "backend/tests/test_webhooks.py")
        self.assertFalse(report.failures_look_unrelated())
        self.assertIn("fix the failing tests above", report.recommendation())

    def test_a_root_level_change_can_break_anything(self) -> None:
        self.assertFalse(_report(["conftest.py"], "backend/tests/test_x.py").failures_look_unrelated())


class UiEditsNeedChecksNotTestsTest(unittest.TestCase):
    """A sidebar/navbar restyle closes on a green type check or build."""

    def _state_after_edit(self, path: str):
        from navin.agent.code_validation import CodeValidationState

        state = CodeValidationState()
        state.observe(
            "edit_file", {"path": path}, "ok", status="ok",
            require_verify=False, validate_code=True, is_test_command=lambda _c: False,
        )
        return state

    def test_a_component_restyle_does_not_demand_tests(self) -> None:
        self.assertFalse(self._state_after_edit("frontend/src/components/admin/AdminShell.tsx").needs_tests)

    def test_logic_still_demands_tests(self) -> None:
        self.assertTrue(self._state_after_edit("frontend/src/lib/billing.ts").needs_tests)
        self.assertTrue(self._state_after_edit("backend/api.py").needs_tests)

    def test_an_edited_component_test_must_run(self) -> None:
        self.assertTrue(self._state_after_edit("frontend/src/AdminShell.test.tsx").needs_tests)

    def test_the_restyle_still_needs_a_check(self) -> None:
        state = self._state_after_edit("frontend/src/components/admin/AdminShell.tsx")
        self.assertTrue(state.pending)


if __name__ == "__main__":
    unittest.main()
