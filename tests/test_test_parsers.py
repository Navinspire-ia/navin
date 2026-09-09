# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Counts alone do not tell an agent what to fix, and can be flatly wrong.

Three runners were parsed by a counts-only regex. For `unittest` that regex read
`Ran 4 tests` and never looked at `FAILED (failures=1, errors=1)`, so a suite with
two broken tests was reported as four passing - a false green, the one outcome a
verification loop must never produce. The fixtures below are real output captured
from jest 30, cargo 1.97 and CPython's unittest.
"""

from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from navin.quality.testing import (
    _parse_cargo_text,
    _parse_jest_json,
    _parse_unittest_text,
    runner_table,
)

_UNITTEST_OUTPUT = """\
test_casse (tests.test_demo.DemoTest.test_casse) ... FAIL
test_leve (tests.test_demo.DemoTest.test_leve) ... ERROR
test_ok (tests.test_demo.DemoTest.test_ok) ... ok
test_ignore (tests.test_demo.DemoTest.test_ignore) ... skipped 'pas pret'

======================================================================
ERROR: test_leve (tests.test_demo.DemoTest.test_leve)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "{root}/tests/test_demo.py", line 5, in test_leve
    raise RuntimeError("connexion refusee")
RuntimeError: connexion refusee

======================================================================
FAIL: test_casse (tests.test_demo.DemoTest.test_casse)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "{root}/tests/test_demo.py", line 4, in test_casse
    self.assertEqual(2, 3, "les totaux divergent")
AssertionError: 2 != 3 : les totaux divergent

----------------------------------------------------------------------
Ran 4 tests in 0.001s

FAILED (failures=1, errors=1, skipped=1)
"""

_CARGO_OUTPUT = """\
running 4 tests
test tests::pas_pret ... ignored
test tests::addition_correcte ... ok
test tests::panique_explicite ... FAILED
test tests::totaux_divergent ... FAILED

failures:

---- tests::panique_explicite stdout ----

thread 'tests::panique_explicite' (555381) panicked at src/lib.rs:14:30:
connexion refusee
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

---- tests::totaux_divergent stdout ----

thread 'tests::totaux_divergent' (555382) panicked at src/lib.rs:11:29:
assertion `left == right` failed: les totaux divergent
  left: 4
 right: 5


failures:
    tests::panique_explicite
    tests::totaux_divergent

test result: FAILED. 1 passed; 2 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.00s

error: test failed, to rerun pass `--lib`
"""


class UnittestParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.output = _UNITTEST_OUTPUT.replace("{root}", str(self.root))

    def _parse(self, output: str | None = None):
        return _parse_unittest_text(output if output is not None else self.output, self.root)

    def test_a_broken_suite_is_not_reported_as_passing(self) -> None:
        """The regression that mattered: two red tests read as four green ones."""
        passed, failed, skipped, total, _ = self._parse()
        self.assertEqual((passed, failed, skipped, total), (1, 2, 1, 4))

    def test_each_failure_is_named_with_its_reason(self) -> None:
        _, _, _, _, failures = self._parse()
        by_name = {f.name: f for f in failures}
        self.assertEqual(set(by_name), {"test_casse", "test_leve"})
        self.assertIn("les totaux divergent", by_name["test_casse"].message)
        self.assertIn("connexion refusee", by_name["test_leve"].message)

    def test_the_epilogue_is_not_mistaken_for_a_reason(self) -> None:
        """The last block runs to the end of the output, verdict line included."""
        _, _, _, _, failures = self._parse()
        for failure in failures:
            self.assertNotIn("FAILED (failures=", failure.message)

    def test_an_error_is_distinguished_from_a_failure(self) -> None:
        kinds = {f.name: f.kind for f in self._parse()[4]}
        self.assertEqual(kinds["test_leve"], "error")
        self.assertEqual(kinds["test_casse"], "failure")

    def test_the_traceback_location_is_project_relative(self) -> None:
        for failure in self._parse()[4]:
            self.assertEqual(failure.file, "tests/test_demo.py")
            self.assertGreater(failure.line, 0)

    def test_a_green_suite_stays_green(self) -> None:
        output = "test_ok (t.T.test_ok) ... ok\n\nRan 1 test in 0.001s\n\nOK\n"
        passed, failed, skipped, total, failures = self._parse(output)
        self.assertEqual((passed, failed, skipped, total), (1, 0, 0, 1))
        self.assertEqual(failures, [])

    def test_a_skip_only_verdict_is_counted(self) -> None:
        output = "Ran 2 tests in 0.001s\n\nOK (skipped=2)\n"
        passed, failed, skipped, total, _ = self._parse(output)
        self.assertEqual((passed, failed, skipped, total), (0, 0, 2, 2))

    def test_a_crash_before_the_verdict_still_counts_the_blocks(self) -> None:
        """Killed mid-run, the blocks are the only record of what broke."""
        truncated = self.output.split("----------------------------------------------------------------------\nRan")[0]
        _, failed, _, _, failures = self._parse(truncated)
        self.assertEqual(failed, 2)
        self.assertEqual(len(failures), 2)


class CargoParserTest(unittest.TestCase):
    def test_counts_come_from_the_result_line(self) -> None:
        passed, failed, skipped, total, _ = _parse_cargo_text(_CARGO_OUTPUT)
        self.assertEqual((passed, failed, skipped, total), (1, 2, 1, 4))

    def test_each_panic_is_located(self) -> None:
        failures = _parse_cargo_text(_CARGO_OUTPUT)[4]
        by_name = {f.name: f for f in failures}
        self.assertEqual(by_name["tests::panique_explicite"].file, "src/lib.rs")
        self.assertEqual(by_name["tests::panique_explicite"].line, 14)
        self.assertEqual(by_name["tests::totaux_divergent"].line, 11)

    def test_the_compared_values_are_kept(self) -> None:
        """`assertion left == right failed` alone does not say what the values were."""
        failures = _parse_cargo_text(_CARGO_OUTPUT)[4]
        message = next(f.message for f in failures if "divergent" in f.name)
        self.assertIn("left: 4", message)
        self.assertIn("right: 5", message)

    def test_the_name_recap_is_not_read_as_a_panic(self) -> None:
        failures = _parse_cargo_text(_CARGO_OUTPUT)[4]
        self.assertEqual(len(failures), 2)

    def test_counts_are_summed_across_binaries(self) -> None:
        """A workspace runs one binary per target, each with its own result line."""
        doubled = _CARGO_OUTPUT + _CARGO_OUTPUT.replace("2 failed", "0 failed")
        passed, failed, _, _, _ = _parse_cargo_text(doubled)
        self.assertEqual(passed, 2)
        self.assertEqual(failed, 2)

    def test_a_green_run_reports_no_failures(self) -> None:
        green = (
            "running 1 test\ntest tests::ok ... ok\n\n"
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
        )
        passed, failed, _, _, failures = _parse_cargo_text(green)
        self.assertEqual((passed, failed), (1, 0))
        self.assertEqual(failures, [])


class JestParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.report = self.root / "report.json"

    def _write(self, payload: dict) -> None:
        self.report.write_text(json.dumps(payload), encoding="utf-8")

    def _suite(self, **overrides) -> dict:
        suite = {
            "name": str(self.root / "tests/demo.test.js"),
            "status": "failed",
            "assertionResults": [
                {"status": "passed", "title": "ok", "ancestorTitles": ["totaux"]},
                {
                    "status": "failed",
                    "title": "casse",
                    "ancestorTitles": ["totaux"],
                    "location": {"line": 3, "column": 3},
                    "failureMessages": [
                        "Error: expect(received).toBe(expected)\n\nExpected: 3\n"
                        "Received: 2\n"
                        f"    at Object.toBe ({self.root}/tests/demo.test.js:3:35)\n"
                        f"    at completed ({self.root}/node_modules/jest-circus/x.js:1:1)\n"
                        "    at processTicksAndRejections (node:internal/process/task_queues:105:5)"
                    ],
                },
                {"status": "pending", "title": "plus tard", "ancestorTitles": ["totaux"]},
            ],
        }
        suite.update(overrides)
        return suite

    def _payload(self, **overrides) -> dict:
        payload = {
            "numPassedTests": 1,
            "numFailedTests": 1,
            "numPendingTests": 1,
            "numTodoTests": 0,
            "numTotalTests": 3,
            "testResults": [self._suite()],
        }
        payload.update(overrides)
        return payload

    def test_counts_and_one_named_failure(self) -> None:
        self._write(self._payload())
        passed, failed, skipped, total, failures = _parse_jest_json(self.report, self.root)
        self.assertEqual((passed, failed, skipped, total), (1, 1, 1, 3))
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].name, "casse")
        self.assertEqual(failures[0].file, "tests/demo.test.js")
        self.assertEqual(failures[0].line, 3)

    def test_the_describe_block_is_carried_as_the_classname(self) -> None:
        self._write(self._payload())
        self.assertEqual(_parse_jest_json(self.report, self.root)[4][0].classname, "totaux")

    def test_runner_internals_are_stripped_from_the_message(self) -> None:
        """A dozen jest-circus frames crowd out the assertion that matters."""
        self._write(self._payload())
        message = _parse_jest_json(self.report, self.root)[4][0].message
        self.assertIn("Expected: 3", message)
        self.assertNotIn("jest-circus", message)
        self.assertNotIn("node:internal", message)

    def test_the_line_is_recovered_from_the_stack_without_a_location(self) -> None:
        suite = self._suite()
        suite["assertionResults"][1].pop("location")
        self._write(self._payload(testResults=[suite]))
        self.assertEqual(_parse_jest_json(self.report, self.root)[4][0].line, 3)

    def test_a_suite_that_fails_to_compile_still_reports_a_reason(self) -> None:
        """No assertion ran, so only the suite-level message explains the failure."""
        self._write(
            self._payload(
                numPassedTests=0,
                numFailedTests=1,
                numPendingTests=0,
                numTotalTests=1,
                testResults=[
                    self._suite(
                        assertionResults=[],
                        message="Cannot find module './missing'",
                    )
                ],
            )
        )
        failures = _parse_jest_json(self.report, self.root)[4]
        self.assertEqual(len(failures), 1)
        self.assertIn("Cannot find module", failures[0].message)
        self.assertEqual(failures[0].kind, "error")

    def test_a_missing_report_is_not_an_exception(self) -> None:
        self.assertEqual(
            _parse_jest_json(self.root / "absent.json", self.root), (0, 0, 0, 0, [])
        )

    def test_a_truncated_report_is_not_an_exception(self) -> None:
        self.report.write_text("{ not json", encoding="utf-8")
        self.assertEqual(_parse_jest_json(self.report, self.root), (0, 0, 0, 0, []))


class RunnerTableTest(unittest.TestCase):
    """The table and the parsers have to agree, or a report is silently dropped."""

    def test_every_configured_parser_is_implemented(self) -> None:
        known = {"junit_xml", "go_json", "jest_json", "cargo_text", "unittest_text", "summary"}
        for name, spec in runner_table().items():
            with self.subTest(runner=name):
                self.assertIn(str(spec.get("parser", "summary")), known)

    def test_a_runner_asking_for_a_report_file_gets_one(self) -> None:
        """An arg mentioning {report} is dropped when the parser has no temp file."""
        from navin.quality.testing import _REPORT_SUFFIXES

        for name, spec in runner_table().items():
            wants_report = any("{report}" in str(arg) for arg in spec.get("args", []))
            with self.subTest(runner=name):
                if wants_report:
                    self.assertIn(str(spec.get("parser")), _REPORT_SUFFIXES)

    def test_no_runner_is_left_on_counts_only(self) -> None:
        """`summary` cannot name a failing test, so nothing should still use it."""
        on_summary = [
            name
            for name, spec in runner_table().items()
            if str(spec.get("parser", "summary")) == "summary"
        ]
        self.assertEqual(on_summary, [])


class EndToEndTest(unittest.TestCase):
    """Fixtures can drift from reality; this runs the real thing when present."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def test_a_real_unittest_suite_reports_its_failures(self) -> None:
        from navin.quality.testing import _run_one

        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").touch()
        (self.root / "tests" / "test_demo.py").write_text(
            textwrap.dedent("""\
                import unittest

                class DemoTest(unittest.TestCase):
                    def test_ok(self):
                        self.assertEqual(1, 1)

                    def test_casse(self):
                        self.assertEqual(2, 3, "les totaux divergent")
            """),
            encoding="utf-8",
        )
        outcome = _run_one("unittest", runner_table()["unittest"], self.root, None)
        self.assertTrue(outcome.ran)
        self.assertEqual((outcome.passed, outcome.failed), (1, 1))
        self.assertFalse(outcome.ok)
        self.assertEqual(len(outcome.failures), 1)
        self.assertIn("les totaux divergent", outcome.failures[0].message)

    def test_a_suite_that_discovers_nothing_is_not_a_pass(self) -> None:
        """`unittest discover` finds nothing unless the directory is a package."""
        from navin.quality.testing import _run_one

        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_demo.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n        self.assertEqual(1, 2)\n",
            encoding="utf-8",
        )
        outcome = _run_one("unittest", runner_table()["unittest"], self.root, None)
        self.assertFalse(outcome.ok)
        self.assertIn("no tests ran", outcome.summary())


if __name__ == "__main__":
    unittest.main()
