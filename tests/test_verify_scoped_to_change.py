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


class NoSuiteForTheChangeTest(unittest.TestCase):
    """The gate must be satisfiable: a change no suite or linter can see used to
    leave validation pending forever, so the agent retried until cut off."""

    def test_a_language_no_suite_covers_runs_nothing_instead_of_everything(self) -> None:
        with mock.patch.object(testing, "runner_table", return_value=TABLE), \
             mock.patch.object(testing, "_primary_runners", return_value=["pytest"]):
            self.assertEqual(testing.runners_for_changes(Path("."), ["frontend/A.tsx"]), [])

    def _state_after_edit(self, path: str):
        from navin.agent.code_validation import CodeValidationState

        state = CodeValidationState()
        state.observe(
            "write_file", {"path": path, "content": "x"}, "ok", status="ok",
            require_verify=True, validate_code=True, is_test_command=lambda _c: False,
        )
        return state

    def _verify(self, state, evidence) -> None:
        from navin.agent.tools.base import ToolResult

        state.observe(
            "verify", {"action": "check"}, ToolResult("report", verification=evidence), status="ok",
            require_verify=True, validate_code=True, is_test_command=lambda _c: False,
        )

    def test_a_docs_edit_never_arms_the_gate(self) -> None:
        self.assertFalse(self._state_after_edit("README.md").pending)

    def test_verify_with_nothing_applicable_closes_the_gate(self) -> None:
        from navin.quality.evidence import VerificationEvidence

        state = self._state_after_edit("web/styles.css")
        self.assertTrue(state.pending)
        self._verify(state, VerificationEvidence(summary="No linter ran.; No tests ran."))
        self.assertFalse(state.pending)

    def test_code_with_no_suite_does_not_demand_new_tests(self) -> None:
        from navin.quality.evidence import VerificationEvidence

        state = self._state_after_edit("scripts/tool.py")
        self.assertTrue(state.needs_tests)
        self._verify(state, VerificationEvidence(checks_ok=True, summary="Lint clean", no_test_suite=True))
        self.assertFalse(state.pending)

    def test_a_red_check_still_blocks(self) -> None:
        from navin.quality.evidence import VerificationEvidence

        state = self._state_after_edit("web/styles.css")
        self._verify(state, VerificationEvidence(checks_ok=False, summary="Lint: 2 errors"))
        self.assertTrue(state.pending)


class ChangedFilesTestsOnlyTest(unittest.TestCase):
    """The end-of-task check runs the tests of the changed files, nothing else.

    Measured: a CLI display task ran unrelated suites, one failed on slowness, and
    the gate asked twice to "repair" it; JSON and TSX edits ran cargo and pytest.
    """

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel: str, text: str = "") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_a_python_change_maps_to_its_own_tests(self) -> None:
        self._write("pkg/widgets.py")
        self._write("tests/test_widgets.py")
        self._write("tests/test_clipboard.py", "from pkg import widgets\n")
        self._write("tests/test_billing.py", "from pkg import billing\n")
        self._write("tests/test_paste.py")
        targets = testing.related_test_targets(self.root, ["tests/test_paste.py", "pkg/widgets.py"])
        self.assertEqual(targets, ["tests/test_paste.py", "tests/test_widgets.py", "tests/test_clipboard.py"])

    def test_a_hub_module_keeps_only_the_tests_named_after_it(self) -> None:
        self._write("pkg/app.py")
        self._write("tests/test_app.py")
        for index in range(7):
            self._write(f"tests/test_feature{index}.py", "import pkg.app\n")
        self.assertEqual(testing.related_test_targets(self.root, ["pkg/app.py"]), ["tests/test_app.py"])

    def test_each_test_file_is_read_once_and_reused_while_unchanged(self) -> None:
        # Re-reading every test for each changed module froze the desktop
        # engine ~100 s per lookup on a WSL tree read from Windows.
        from unittest import mock

        self._write("pkg/alpha.py")
        self._write("pkg/beta.py")
        self._write("tests/test_uses_both.py", "from pkg import alpha\nfrom pkg import beta\n")
        self._write("tests/test_other.py", "import os\n")
        reads: list[str] = []
        real_read = Path.read_text

        def counting_read(path, *args, **kwargs):
            reads.append(Path(path).name)
            return real_read(path, *args, **kwargs)

        testing._TEST_TEXTS.clear()
        self.addCleanup(testing._TEST_TEXTS.clear)
        with mock.patch.object(Path, "read_text", counting_read):
            first = testing.related_test_targets(self.root, ["pkg/alpha.py", "pkg/beta.py"])
            self.assertEqual(first, ["tests/test_uses_both.py"])
            self.assertEqual(sorted(reads), ["test_other.py", "test_uses_both.py"], "one read per file")
            reads.clear()
            testing.related_test_targets(self.root, ["pkg/alpha.py"])
            self.assertEqual(reads, [], "unchanged tests are not read again")
            self._write("tests/test_other.py", "from pkg import alpha\n")
            self.assertEqual(
                testing.related_test_targets(self.root, ["pkg/alpha.py"]),
                ["tests/test_other.py", "tests/test_uses_both.py"],
            )
            self.assertEqual(reads, ["test_other.py"], "an edited test is read again")

    def test_an_exhausted_scan_budget_keeps_the_tests_named_after_the_module(self) -> None:
        from unittest import mock

        self._write("pkg/app.py")
        self._write("tests/test_app.py")
        self._write("tests/test_loader.py", "import pkg.app\n")
        testing._TEST_TEXTS.clear()
        self.addCleanup(testing._TEST_TEXTS.clear)
        with mock.patch.object(testing, "_IMPORT_SCAN_BUDGET_S", -1.0):
            self.assertEqual(testing.related_test_targets(self.root, ["pkg/app.py"]), ["tests/test_app.py"])
        self.assertEqual(
            testing.related_test_targets(self.root, ["pkg/app.py"]),
            ["tests/test_app.py", "tests/test_loader.py"],
        )

    def test_a_typescript_change_maps_to_its_sibling_test(self) -> None:
        self._write("site/front/src/lib/card.ts")
        self._write("site/front/src/lib/card.test.ts")
        self._write("site/front/src/lib/other.test.ts")
        self.assertEqual(
            testing.related_test_targets(self.root, ["site/front/src/lib/card.ts"]),
            ["site/front/src/lib/card.test.ts"],
        )

    def test_json_and_tsx_edits_never_start_cargo_or_pytest(self) -> None:
        self._write("site/front/messages/fr.json", "{}")
        self._write("site/front/src/components/table.tsx")
        self._write("site/back/src/lib/promo.test.ts")
        with mock.patch.object(testing, "_primary_runners", return_value=["pytest", "cargo_test"]), \
             mock.patch.object(testing, "_run_one") as run_one, \
             mock.patch.object(testing, "run_tests") as run_suite:
            outcomes, notes = testing.run_tests_for_changes(self.root, [
                "site/front/messages/fr.json",
                "site/front/src/components/table.tsx",
                "site/back/src/lib/promo.test.ts",
            ])
        run_one.assert_not_called()
        run_suite.assert_not_called()
        self.assertEqual(outcomes, [])
        self.assertIn("No installed test runner reaches site/back/src/lib/promo.test.ts", notes[0])

    def test_files_no_test_covers_run_nothing_and_say_so(self) -> None:
        self._write("pkg/lonely.py")
        with mock.patch.object(testing, "_primary_runners", return_value=["pytest"]), \
             mock.patch.object(testing, "run_tests") as run_suite:
            outcomes, notes = testing.run_tests_for_changes(self.root, ["pkg/lonely.py", "README.md"])
        run_suite.assert_not_called()
        self.assertEqual(outcomes, [])
        self.assertIn("No test file covers the changed files", notes[0])

    def test_tests_of_one_runner_run_in_a_single_process(self) -> None:
        self._write("tests/test_a.py")
        self._write("tests/test_b.py")
        outcome = testing.TestOutcome(runner="pytest", ran=True, passed=2, total=2, exit_code=0)
        with mock.patch.object(testing, "_runner_for_target", return_value=("pytest", self.root)), \
             mock.patch.object(testing, "_primary_runners", return_value=["pytest"]), \
             mock.patch.object(testing, "_run_one", return_value=outcome) as run_one:
            outcomes, notes = testing.run_tests_for_changes(self.root, ["tests/test_a.py", "tests/test_b.py"])
        self.assertEqual(outcomes, [outcome])
        self.assertEqual(notes, [])
        self.assertEqual(run_one.call_args.args[3], ["tests/test_a.py", "tests/test_b.py"])

    def test_every_target_reaches_the_runner_command_line(self) -> None:
        import subprocess

        self._write("tests/test_a.py")
        spec = testing.runner_table()["pytest"]
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(testing, "_runner_argv", return_value=["pytest"]), \
             mock.patch.object(testing, "_parse_junit_xml", return_value=(2, 0, 0, 2, [])), \
             mock.patch.object(testing.subprocess, "run", return_value=done) as run:
            outcome = testing._run_one("pytest", spec, self.root, ["tests/test_a.py", "tests/test_b.py"])
        self.assertTrue(outcome.ok)
        self.assertEqual(run.call_args.args[0][-2:], ["tests/test_a.py", "tests/test_b.py"])

    def test_verify_of_a_json_edit_is_not_red(self) -> None:
        from navin.quality.evidence import verify_evidence
        from navin.quality.verify import verify_changes

        self._write("messages/fr.json", '{"a": 1}')
        report = verify_changes(self.root, ["messages/fr.json"])
        self.assertEqual(report.test_outcomes, [])
        self.assertIsNone(verify_evidence(report).tests_ok)
        self.assertNotEqual(report.verdict, VERDICT_TEST_FAILURES)
