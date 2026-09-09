# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Test Explorer collection and API payloads.

The explorer promises that a test shown in the tree runs with the exact
arguments the runner table defines. These tests pin the pytest id parsing,
the suite filtering (no unittest duplicates next to pytest), and the API
guardrails (unknown runner, missing project, bad targets).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from navin.quality.test_explorer import _collect_pytest, collect_tests
from navin.quality.testing import _runner_argv, runner_table
from navin.webui.test_explorer_api import (
    TestExplorerError,
    collect_payload,
    run_payload,
)


class _Scope:
    """Minimal WorkspaceScope stand-in: only project_path is consumed."""

    def __init__(self, project_path: str) -> None:
        self.project_path = project_path


def _python_project(root: Path) -> None:
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_math.py").write_text(
        textwrap.dedent(
            """
            class TestAlgebra:
                def test_add(self):
                    assert 1 + 1 == 2

            def test_top_level():
                assert True
            """
        ),
        encoding="utf-8",
    )


class CollectPytestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="navin-texp-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _python_project(self.root)

    def _launch(self) -> list[str]:
        launch = _runner_argv(runner_table()["pytest"], self.root)
        self.assertIsNotNone(launch, "pytest must be runnable in the dev env")
        assert launch is not None
        return launch

    def test_ids_files_suites_and_names_are_parsed(self) -> None:
        tests, error = _collect_pytest(self._launch(), self.root)
        self.assertEqual("", error)
        by_name = {t["name"]: t for t in tests}
        self.assertIn("test_add", by_name)
        self.assertIn("test_top_level", by_name)

        add = by_name["test_add"]
        self.assertEqual("tests/test_math.py", add["file"])
        self.assertEqual("TestAlgebra", add["suite"])
        self.assertEqual("tests/test_math.py::TestAlgebra::test_add", add["id"])
        self.assertEqual(add["id"], add["run_target"])

        top = by_name["test_top_level"]
        self.assertEqual("", top["suite"])

    def test_collected_id_is_runnable_as_a_target(self) -> None:
        from navin.quality.testing import run_tests

        tests, _ = _collect_pytest(self._launch(), self.root)
        target = next(t["run_target"] for t in tests if t["name"] == "test_add")
        outcomes = run_tests(self.root, runners=["pytest"], target=target)
        self.assertEqual(1, len(outcomes))
        self.assertTrue(outcomes[0].ran)
        self.assertEqual(1, outcomes[0].passed)
        self.assertEqual(0, outcomes[0].failed)

    def test_empty_project_reports_no_error(self) -> None:
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        tests, error = _collect_pytest(self._launch(), empty)
        self.assertEqual([], tests)
        # Exit code 5 (nothing collected) is a state, not a failure.
        self.assertEqual("", error)


class CollectTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="navin-texp-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _python_project(self.root)

    def test_unittest_fallback_is_hidden_when_pytest_is_available(self) -> None:
        payload = collect_tests(self.root)
        runners = [s["runner"] for s in payload["suites"]]
        self.assertIn("pytest", runners)
        self.assertNotIn("unittest", runners)

    def test_unavailable_suites_are_listed_with_a_reason(self) -> None:
        payload = collect_tests(self.root)
        cargo = next(s for s in payload["suites"] if s["runner"] == "cargo_test")
        self.assertFalse(cargo["available"])
        self.assertTrue(cargo["reason"])
        self.assertEqual([], cargo["tests"])

    def test_pytest_suite_carries_collected_tests(self) -> None:
        payload = collect_tests(self.root)
        pytest_suite = next(s for s in payload["suites"] if s["runner"] == "pytest")
        self.assertTrue(pytest_suite["available"])
        self.assertTrue(pytest_suite["collect_supported"])
        self.assertEqual(2, len(pytest_suite["tests"]))


class ApiPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="navin-texp-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _python_project(self.root)

    def test_missing_project_is_a_400(self) -> None:
        with self.assertRaises(TestExplorerError) as ctx:
            collect_payload(_Scope(""))  # type: ignore[arg-type]
        self.assertEqual(400, ctx.exception.status)

    def test_path_project_root_does_not_500(self) -> None:
        payload = collect_payload(_Scope(self.root))  # type: ignore[arg-type]
        self.assertEqual(str(self.root.resolve()), payload["root"])
        self.assertTrue(any(row["runner"] == "pytest" for row in payload["suites"]))

    def test_unknown_runner_is_a_400(self) -> None:
        with self.assertRaises(TestExplorerError) as ctx:
            run_payload(_Scope(str(self.root)), runner="rspec")  # type: ignore[arg-type]
        self.assertEqual(400, ctx.exception.status)

    def test_absurd_target_is_a_400(self) -> None:
        with self.assertRaises(TestExplorerError) as ctx:
            run_payload(
                _Scope(str(self.root)),  # type: ignore[arg-type]
                runner="pytest",
                target="x" * 3000,
            )
        self.assertEqual(400, ctx.exception.status)

    def test_run_returns_structured_outcomes(self) -> None:
        payload = run_payload(
            _Scope(str(self.root)),  # type: ignore[arg-type]
            runner="pytest",
            target="tests/test_math.py::test_top_level",
        )
        self.assertEqual("pytest", payload["runner"])
        self.assertEqual(1, len(payload["outcomes"]))
        outcome = payload["outcomes"][0]
        self.assertTrue(outcome["ran"])
        self.assertEqual(1, outcome["passed"])
        self.assertEqual([], outcome["failures"])


if __name__ == "__main__":
    unittest.main()
