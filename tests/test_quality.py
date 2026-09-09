# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the quality layer: linters, test runners, and the verify loop.

External tools are not assumed to exist. Tests that need one skip when it is
missing, so the suite stays meaningful on a bare machine while still covering
the parsing and orchestration logic with fixtures.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.quality import linters as linters_mod
from navin.quality import testing as testing_mod
from navin.quality import verify as verify_mod


def _write(root: Path, rel: str, content: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return target


def _has_linter(name: str) -> bool:
    """Resolve exactly like the code under test, not just via PATH.

    A venv-installed tool is invisible to ``shutil.which`` unless the venv bin
    directory happens to be on PATH, which would skip tests that can in fact run.
    """
    spec = linters_mod.linter_table().get(name)
    if spec is None:
        return False
    return linters_mod._binary_for(spec, Path.cwd()) is not None


def _has_runner(name: str) -> bool:
    spec = testing_mod.runner_table().get(name)
    if spec is None:
        return False
    return linters_mod._binary_for(spec, Path.cwd()) is not None


class LinterTableTest(unittest.TestCase):
    def test_packaged_table_is_valid_and_complete(self):
        table = linters_mod.linter_table()
        self.assertIn("ruff", table)
        self.assertIn("eslint", table)
        for name, spec in table.items():
            with self.subTest(linter=name):
                self.assertIn(spec["parser"], {"json_rows", "regex_lines", "none"})
                self.assertIn(spec.get("scope", "file"), {"file", "project"})
                self.assertTrue(spec.get("extensions"), "needs extensions")
                self.assertIsInstance(spec.get("binary"), dict)

    def test_react_extensions_are_covered(self):
        """The regression that started this: no diagnostics for the frontend."""
        for suffix in (".ts", ".tsx", ".jsx", ".js"):
            with self.subTest(suffix=suffix):
                self.assertTrue(
                    linters_mod.linters_for(suffix),
                    f"no linter handles {suffix}",
                )

    def test_scope_filter(self):
        names = {name for name, _ in linters_mod.linters_for(".ts", scope="project")}
        self.assertIn("tsc", names)
        self.assertNotIn("eslint", names)

    def test_fixable_extensions_include_python_and_typescript(self):
        fixable = linters_mod.fixable_extensions()
        self.assertIn(".py", fixable)
        self.assertIn(".tsx", fixable)


class LinterParsingTest(unittest.TestCase):
    """Parsers are covered with recorded tool output, so no tool is required."""

    def test_json_rows_with_dotted_paths(self):
        spec = linters_mod.linter_table()["ruff"]
        payload = json.dumps([
            {
                "location": {"row": 12, "column": 5},
                "end_location": {"row": 12, "column": 9},
                "code": "F401",
                "message": "unused import",
            }
        ])
        rows = linters_mod._parse_json_rows(spec, "ruff", payload, Path("/p"), "a.py")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].line, rows[0].col), (12, 5))
        self.assertEqual(rows[0].code, "F401")
        self.assertEqual(rows[0].severity, "warning")

    def test_error_codes_promote_severity(self):
        spec = linters_mod.linter_table()["ruff"]
        payload = json.dumps([
            {"location": {"row": 1, "column": 1}, "code": "E999", "message": "bad"}
        ])
        rows = linters_mod._parse_json_rows(spec, "ruff", payload, Path("/p"), "a.py")
        self.assertEqual(rows[0].severity, "error")

    def test_nested_rows_and_severity_map(self):
        spec = linters_mod.linter_table()["eslint"]
        payload = json.dumps([
            {
                "filePath": "/p/src/App.tsx",
                "messages": [
                    {
                        "line": 3, "column": 7, "endLine": 3, "endColumn": 12,
                        "ruleId": "no-unused-vars", "message": "unused",
                        "severity": 2,
                    },
                    {
                        "line": 9, "column": 1, "ruleId": "eqeqeq",
                        "message": "use ===", "severity": 1,
                    },
                ],
            }
        ])
        rows = linters_mod._parse_json_rows(spec, "eslint", payload, Path("/p"), "x")
        self.assertEqual([r.severity for r in rows], ["error", "warning"])
        self.assertEqual(rows[0].path, "src/App.tsx")
        self.assertEqual(rows[0].code, "no-unused-vars")

    def test_regex_lines_parses_tsc_output(self):
        spec = linters_mod.linter_table()["tsc"]
        output = (
            "src/lib/api.ts(42,17): error TS2345: Argument of type 'string' is "
            "not assignable to parameter of type 'number'.\n"
            "noise line that should be ignored\n"
        )
        rows = linters_mod._parse_regex_lines(spec, "tsc", output, Path("/p"), "x")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].path, "src/lib/api.ts")
        self.assertEqual((rows[0].line, rows[0].col), (42, 17))
        self.assertEqual(rows[0].code, "TS2345")
        self.assertEqual(rows[0].severity, "error")

    def test_code_prefix_applied(self):
        spec = linters_mod.linter_table()["shellcheck"]
        payload = json.dumps([
            {"line": 4, "column": 1, "code": 2086, "message": "quote it",
             "level": "warning"}
        ])
        rows = linters_mod._parse_json_rows(spec, "shellcheck", payload, Path("/p"), "s.sh")
        self.assertEqual(rows[0].code, "SC2086")
        self.assertEqual(rows[0].severity, "warning")

    def test_malformed_output_is_not_fatal(self):
        spec = linters_mod.linter_table()["ruff"]
        rows = linters_mod._parse_json_rows(spec, "ruff", "not json", Path("/p"), "a.py")
        self.assertEqual(rows, [])


class LintFileTest(unittest.TestCase):
    def test_json_syntax_error_needs_no_external_tool(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "broken.json", '{"a": 1,}')
            results = linters_mod.lint_file(root, "broken.json")
            diagnostics = [d for r in results for d in r.diagnostics]
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].severity, "error")

    def test_valid_json_is_clean(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "ok.json", '{"a": 1}')
            results = linters_mod.lint_file(root, "ok.json")
            self.assertEqual([d for r in results for d in r.diagnostics], [])

    @unittest.skipUnless(_has_linter("ruff"), "ruff not installed")
    def test_ruff_reports_real_diagnostics(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "m.py", "import os\nimport sys\n")
            results = linters_mod.lint_file(root, "m.py")
            codes = {d.code for r in results for d in r.diagnostics}
            self.assertIn("F401", codes)

    @unittest.skipUnless(_has_linter("ruff"), "ruff not installed")
    def test_auto_fix_rewrites_the_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = _write(root, "m.py", "import os\n\nvalue = 1\n")
            linters_mod.fix_file(root, "m.py")
            self.assertNotIn("import os", target.read_text(encoding="utf-8"))

    def test_missing_binary_is_reported_not_raised(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "s.sh", "echo $UNQUOTED\n")
            with patch.object(linters_mod, "_binary_for", return_value=None):
                results = linters_mod.lint_file(root, "s.sh")
            # The product-ui gate is pure Python and legitimately runs with no
            # external binary; the guarantee under test only concerns linters
            # that need one (shellcheck here).
            binary_backed = [r for r in results if r.linter != "product-ui"]
            self.assertTrue(binary_backed)
            self.assertFalse(any(r.ran for r in binary_backed))
            self.assertTrue(all(r.skipped_reason for r in binary_backed))

    def test_timeout_is_reported_as_not_run(self):
        spec = {
            "extensions": [".py"], "scope": "file", "parser": "json_rows",
            "binary": {"name": "sleeper"}, "args": ["{file}"], "timeout_s": 0.01,
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = _write(root, "m.py", "x = 1\n")
            with patch.object(linters_mod, "_binary_for", return_value="/bin/sleep"), \
                 patch.object(
                     linters_mod.subprocess, "run",
                     side_effect=subprocess.TimeoutExpired("sleep", 0.01),
                 ):
                result = linters_mod._run_linter("sleeper", spec, root, target)
        self.assertFalse(result.ran)
        self.assertIn("timed out", result.skipped_reason)

    def test_work_dir_follows_the_matched_config(self):
        """A monorepo config in a subdirectory must set the working directory.

        Regression: running tsc from the repository root made it exit at once
        with "no config found", which was then reported as a clean type check.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "webui/tsconfig.json", "{}")
            spec = linters_mod.linter_table()["tsc"]
            self.assertEqual(linters_mod._work_dir(spec, root), root / "webui")

    def test_work_dir_is_the_root_for_root_level_config(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "tsconfig.json", "{}")
            spec = linters_mod.linter_table()["tsc"]
            self.assertEqual(linters_mod._work_dir(spec, root), root)

    def test_failure_without_diagnostics_is_not_reported_as_clean(self):
        """A tool that refuses to run must never look like a passing check."""
        spec = {
            "extensions": [".py"], "scope": "project", "parser": "regex_lines",
            "regex": "^(?P<file>[^:]+):(?P<line>\\d+): (?P<message>.*)$",
            "binary": {"name": "broken"}, "args": [], "ok_exit_codes": [0, 1, 2],
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(linters_mod, "_binary_for", return_value="/bin/sh"), \
                 patch.object(linters_mod.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="",
                    stderr="error TS5058: cannot read tsconfig.json",
                )
                result = linters_mod._run_linter("broken", spec, root, None)
        self.assertFalse(result.ran)
        self.assertIn("cannot read tsconfig.json", result.skipped_reason)

    def test_paths_are_reported_relative_to_the_root_not_the_work_dir(self):
        spec = linters_mod.linter_table()["tsc"]
        output = "src/lib/api.ts(4,2): error TS2345: nope\n"
        rows = linters_mod._parse_regex_lines(
            spec, "tsc", output, Path("/p"), "x", Path("/p/webui")
        )
        self.assertEqual(rows[0].path, "webui/src/lib/api.ts")

    def test_requires_gate_skips_when_no_config(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "a.tsx", "export const A = () => null;\n")
            results = linters_mod.lint_file(root, "a.tsx")
            eslint = [r for r in results if r.linter == "eslint"]
            self.assertTrue(eslint)
            self.assertFalse(eslint[0].ran)
            self.assertIn("config", eslint[0].skipped_reason)


class TestRunnerDetectionTest(unittest.TestCase):
    def test_pytest_detected_from_tests_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "tests/test_a.py", "def test_a():\n    assert True\n")
            rows = {r["runner"]: r for r in testing_mod.detect_suites(root)}
            self.assertTrue(rows["pytest"]["available"] or "not installed" in rows["pytest"]["reason"])

    def test_nothing_detected_in_empty_project(self):
        with TemporaryDirectory() as tmp:
            rows = testing_mod.detect_suites(Path(tmp))
            self.assertFalse(any(r["available"] for r in rows))

    def test_detect_content_matches_package_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "package.json", '{"devDependencies": {"vitest": "^2"}}')
            spec = testing_mod.runner_table()["vitest"]
            self.assertTrue(testing_mod._detected(spec, root))

    def test_run_tests_without_a_suite_explains_itself(self):
        with TemporaryDirectory() as tmp:
            outcomes = testing_mod.run_tests(Path(tmp))
            self.assertEqual(len(outcomes), 1)
            self.assertFalse(outcomes[0].ran)
            self.assertIn("no test suite detected", outcomes[0].skipped_reason)

    def test_a_suite_living_only_in_a_subdirectory_is_found(self):
        # The candidate list starts with ".", which always exists, so picking the
        # first directory present would always answer the root and report a
        # frontend suite as absent - verify would then skip it silently.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "webui/package.json", '{"devDependencies": {"vitest": "^2"}}')
            spec = testing_mod.runner_table()["vitest"]
            self.assertEqual(testing_mod._workdir_for(spec, root), root / "webui")
            rows = {r["runner"]: r for r in testing_mod.detect_suites(root)}
            self.assertNotIn("no test layout", rows["vitest"]["reason"])

    def test_the_root_still_wins_when_the_suite_lives_there(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "package.json", '{"devDependencies": {"vitest": "^2"}}')
            _write(root, "webui/package.json", "{}")
            spec = testing_mod.runner_table()["vitest"]
            self.assertEqual(testing_mod._workdir_for(spec, root), root)

    def test_an_undetected_suite_falls_back_to_the_first_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = testing_mod.runner_table()["vitest"]
            self.assertEqual(testing_mod._workdir_for(spec, root), root)


class TestResultParsingTest(unittest.TestCase):
    JUNIT = """
    <?xml version="1.0" encoding="utf-8"?>
    <testsuites>
      <testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1">
        <testcase classname="tests.test_m" name="test_ok" file="tests/test_m.py" line="3"/>
        <testcase classname="tests.test_m" name="test_bad" file="tests/test_m.py" line="7">
          <failure message="AssertionError: 4 != 5">long traceback</failure>
        </testcase>
        <testcase classname="tests.test_m" name="test_boom" file="tests/test_m.py" line="11">
          <error message="RuntimeError: boom">trace</error>
        </testcase>
        <testcase classname="tests.test_m" name="test_skip">
          <skipped message="not ready"/>
        </testcase>
      </testsuite>
    </testsuites>
    """

    def test_junit_xml_counts_and_failure_detail(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _write(root, "r.xml", self.JUNIT)
            passed, failed, skipped, total, failures = testing_mod._parse_junit_xml(
                report, root
            )
        self.assertEqual((passed, failed, skipped, total), (1, 2, 1, 4))
        kinds = sorted(f.kind for f in failures)
        self.assertEqual(kinds, ["error", "failure"])
        messages = " ".join(f.message for f in failures)
        self.assertIn("4 != 5", messages)
        self.assertIn("boom", messages)
        self.assertEqual(failures[0].file, "tests/test_m.py")

    def test_junit_parser_tolerates_garbage(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _write(root, "r.xml", "not xml at all")
            result = testing_mod._parse_junit_xml(report, root)
        self.assertEqual(result[:4], (0, 0, 0, 0))

    def test_go_json_events(self):
        output = "\n".join([
            json.dumps({"Action": "run", "Package": "p", "Test": "TestA"}),
            json.dumps({"Action": "pass", "Package": "p", "Test": "TestA"}),
            json.dumps({"Action": "output", "Package": "p", "Test": "TestB",
                        "Output": "want 1 got 2\n"}),
            json.dumps({"Action": "fail", "Package": "p", "Test": "TestB"}),
            json.dumps({"Action": "skip", "Package": "p", "Test": "TestC"}),
        ])
        passed, failed, skipped, total, failures = testing_mod._parse_go_json(output)
        self.assertEqual((passed, failed, skipped, total), (1, 1, 1, 3))
        self.assertEqual(failures[0].name, "TestB")
        self.assertIn("want 1 got 2", failures[0].message)

    def test_counts_for_cargo(self):
        """cargo moved off the summary parser; the tallies must still add up."""
        self.assertEqual(testing_mod.runner_table()["cargo_test"]["parser"], "cargo_text")
        output = "test result: FAILED. 7 passed; 2 failed; 1 ignored; 0 measured"
        passed, failed, skipped, total, _ = testing_mod._parse_cargo_text(output)
        self.assertEqual((passed, failed, skipped, total), (7, 2, 1, 10))

    def test_outcome_ok_requires_zero_failures(self):
        self.assertFalse(
            testing_mod.TestOutcome(runner="x", ran=True, failed=1, exit_code=1).ok
        )
        self.assertTrue(
            testing_mod.TestOutcome(runner="x", ran=True, passed=3, exit_code=0).ok
        )


@unittest.skipUnless(_has_runner("pytest"), "pytest not installed")
class TestExecutionTest(unittest.TestCase):
    """A real pytest run, including the failure path."""

    def _project(self, root: Path, body: str) -> None:
        _write(root, "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
        _write(root, "tests/test_m.py", body)

    def test_passing_suite(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root, "def test_ok():\n    assert 1 == 1\n")
            outcome = testing_mod.run_tests(root, runners=["pytest"])[0]
        self.assertTrue(outcome.ran)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.passed, 1)

    def test_failing_suite_reports_structured_detail(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(
                root,
                "def test_bad():\n    assert 2 + 2 == 5, 'arithmetic is broken'\n",
            )
            outcome = testing_mod.run_tests(root, runners=["pytest"])[0]
        self.assertTrue(outcome.ran)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed, 1)
        self.assertEqual(len(outcome.failures), 1)
        self.assertIn("arithmetic is broken", outcome.failures[0].message)
        self.assertIn("test_bad", outcome.render())

    def test_tests_may_import_an_uninstalled_local_package(self):
        """A plain pkg/ + tests/ project must collect without being installed.

        The pytest console script leaves the working directory off sys.path, so
        this layout used to report "collection failure" on a healthy suite -
        and the verify loop inherited the phantom red.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
            _write(root, "pkg/__init__.py", "")
            _write(root, "pkg/cart.py", "def double(v):\n    return v * 2\n")
            _write(
                root,
                "tests/test_m.py",
                "from pkg.cart import double\n\n\ndef test_double():\n    assert double(2) == 4\n",
            )
            outcome = testing_mod.run_tests(root, runners=["pytest"])[0]
        self.assertTrue(outcome.ran)
        self.assertTrue(outcome.ok, f"suite should be green, got: {outcome.render()}")
        self.assertEqual(outcome.passed, 1)

    def test_a_collection_failure_names_its_cause(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
            _write(root, "tests/test_m.py", "import absent_module_xyz\n\n\ndef test_a():\n    pass\n")
            outcome = testing_mod.run_tests(root, runners=["pytest"])[0]
        self.assertFalse(outcome.ok)
        self.assertIn("absent_module_xyz", outcome.render())

    def test_pytest_launches_through_the_interpreter(self):
        spec = testing_mod.runner_table()["pytest"]
        argv = testing_mod._launch_argv(spec, "/somewhere/.venv/bin/pytest")
        self.assertEqual(argv[1:], ["-m", "pytest"])
        self.assertNotEqual(argv[0], "/somewhere/.venv/bin/pytest")

    def test_failure_messages_combine_summary_and_detail(self):
        combine = testing_mod._failure_message
        self.assertEqual(combine("collection failure", "E   ImportError: boom"), "collection failure: ImportError: boom")
        self.assertEqual(combine("assert 1 == 2", None), "assert 1 == 2")
        self.assertEqual(combine(None, "line one\nlast line"), "last line")
        self.assertEqual(combine("same text", "same text"), "same text")
        self.assertEqual(combine("", ""), "")

    def test_a_runner_without_run_as_module_calls_its_binary(self):
        argv = testing_mod._launch_argv({"binary": {"name": "vitest"}}, "/p/node_modules/.bin/vitest")
        self.assertEqual(argv, ["/p/node_modules/.bin/vitest"])


class ChangeDetectionTest(unittest.TestCase):
    def test_build_artifacts_are_filtered(self):
        self.assertFalse(verify_mod._is_verifiable("build/x.pyc"))
        self.assertFalse(verify_mod._is_verifiable("a/__pycache__/m.cpython-312.pyc"))
        self.assertFalse(verify_mod._is_verifiable("node_modules/p/index.js"))
        self.assertTrue(verify_mod._is_verifiable("navin/quality/linters.py"))
        self.assertTrue(verify_mod._is_verifiable("webui/src/App.tsx"))


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.config = Path(self._tmp.name) / "config" / "config.json"
        patcher = patch.object(
            verify_mod, "_snapshot_dir",
            lambda: self.config.parent / "snapshots",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_snapshot_then_restore_round_trip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = _write(root, "app.py", "original = 1\n")
            created = verify_mod.create_snapshot(root, ["app.py"])
            self.assertTrue(created["id"])

            target.write_text("mutated = 2\n", encoding="utf-8")
            restored = verify_mod.restore_snapshot(root, created["id"])

            self.assertEqual(restored["restored"], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "original = 1\n")

    def test_restore_rejects_snapshot_from_another_project(self):
        with TemporaryDirectory() as tmp_a, TemporaryDirectory() as tmp_b:
            root_a, root_b = Path(tmp_a), Path(tmp_b)
            _write(root_a, "app.py", "a = 1\n")
            created = verify_mod.create_snapshot(root_a, ["app.py"])
            result = verify_mod.restore_snapshot(root_b, created["id"])
        self.assertEqual(result["restored"], 0)
        self.assertIn("different project", result["error"])

    def test_unknown_snapshot_is_an_error_not_a_crash(self):
        with TemporaryDirectory() as tmp:
            result = verify_mod.restore_snapshot(Path(tmp), "nope")
        self.assertEqual(result["restored"], 0)
        self.assertIn("not found", result["error"])

    def test_a_binary_file_is_restored_byte_for_byte(self):
        """A rollback that skips the image the agent overwrote is not a rollback."""
        original = b"\x00\x01\x02\xff"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "blob.bin"
            target.write_bytes(original)
            created = verify_mod.create_snapshot(root, ["blob.bin"])
            target.write_bytes(b"clobbered")
            restored = verify_mod.restore_snapshot(root, created["id"])
            self.assertEqual(restored["restored"], 1)
            self.assertEqual(target.read_bytes(), original)

    def test_a_non_utf8_file_is_restored_in_its_own_encoding(self):
        source = "caf\xe9\n".encode("cp1252")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "legacy.txt"
            target.write_bytes(source)
            created = verify_mod.create_snapshot(root, ["legacy.txt"])
            target.write_bytes(b"clobbered")
            verify_mod.restore_snapshot(root, created["id"])
            self.assertEqual(target.read_bytes(), source)

    def test_crlf_endings_survive_a_rollback(self):
        source = b"alpha\r\nbeta\r\n"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "win.txt"
            target.write_bytes(source)
            created = verify_mod.create_snapshot(root, ["win.txt"])
            target.write_bytes(b"clobbered")
            verify_mod.restore_snapshot(root, created["id"])
            self.assertEqual(target.read_bytes(), source)

    def test_a_snapshot_written_before_base64_still_restores(self):
        """Old snapshots on disk must not become unrestorable after the change."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "legacy.py"
            target.write_text("new\n", encoding="utf-8")
            created = verify_mod.create_snapshot(root, ["legacy.py"])
            path = verify_mod._snapshot_dir() / f"{created['id']}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.pop("encoding", None)
            payload["files"]["legacy.py"] = "old\n"
            path.write_text(json.dumps(payload), encoding="utf-8")
            verify_mod.restore_snapshot(root, created["id"])
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")


class VerdictTest(unittest.TestCase):
    def _lint(self, severity: str | None) -> list[linters_mod.LinterResult]:
        if severity is None:
            return [linters_mod.LinterResult(linter="ruff", ran=True)]
        diagnostic = linters_mod.Diagnostic(
            path="a.py", line=1, col=1, end_line=1, end_col=2,
            severity=severity, code="X", message="m", tool="ruff",
        )
        return [
            linters_mod.LinterResult(linter="ruff", ran=True, diagnostics=[diagnostic])
        ]

    def _tests(self, failed: int) -> list[testing_mod.TestOutcome]:
        return [
            testing_mod.TestOutcome(
                runner="pytest", ran=True, passed=1, failed=failed,
                exit_code=1 if failed else 0,
            )
        ]

    def test_no_changes(self):
        self.assertEqual(
            verify_mod._verdict_for([], [], []), verify_mod.VERDICT_NO_CHANGES
        )

    def test_lint_error_outranks_test_failure(self):
        verdict = verify_mod._verdict_for(self._lint("error"), self._tests(1), ["a.py"])
        self.assertEqual(verdict, verify_mod.VERDICT_LINT_ERRORS)

    def test_test_failure_outranks_warning(self):
        verdict = verify_mod._verdict_for(self._lint("warning"), self._tests(1), ["a.py"])
        self.assertEqual(verdict, verify_mod.VERDICT_TEST_FAILURES)

    def test_warnings_still_pass(self):
        verdict = verify_mod._verdict_for(self._lint("warning"), self._tests(0), ["a.py"])
        self.assertEqual(verdict, verify_mod.VERDICT_LINT_WARNINGS)
        report = verify_mod.VerificationReport(verdict=verdict)
        self.assertTrue(report.ok)

    def test_clean(self):
        verdict = verify_mod._verdict_for(self._lint(None), self._tests(0), ["a.py"])
        self.assertEqual(verdict, verify_mod.VERDICT_CLEAN)

    def test_failing_report_recommends_a_concrete_next_step(self):
        report = verify_mod.VerificationReport(
            verdict=verify_mod.VERDICT_TEST_FAILURES,
            changed_paths=["a.py"],
            test_outcomes=self._tests(1),
        )
        self.assertIn("rollback", report.recommendation())
        self.assertFalse(report.ok)
        self.assertIn("FAIL", report.render())

    def test_lint_error_report_points_at_the_fixer(self):
        report = verify_mod.VerificationReport(
            verdict=verify_mod.VERDICT_LINT_ERRORS,
            changed_paths=["a.py"],
            lint_results=self._lint("error"),
        )
        self.assertIn("verify action=fix", report.recommendation())


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class GitRollbackTest(unittest.TestCase):
    def _repo(self, root: Path) -> None:
        for argv in (
            ["git", "init", "-q", "."],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(argv, cwd=root, check=True, capture_output=True)  # noqa: S603

    def _commit(self, root: Path) -> None:
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)  # noqa: S603
        subprocess.run(  # noqa: S603
            ["git", "commit", "-qm", "init"], cwd=root, check=True, capture_output=True
        )

    def test_changed_files_sees_modifications(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            target = _write(root, "app.py", "value = 1\n")
            self._commit(root)
            target.write_text("value = 2\n", encoding="utf-8")
            self.assertEqual(verify_mod.changed_files(root), ["app.py"])

    def test_rollback_restores_tracked_file_without_a_snapshot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            target = _write(root, "app.py", "value = 1\n")
            self._commit(root)
            target.write_text("broken = ???\n", encoding="utf-8")

            result = verify_mod.restore_from_git(root)

            self.assertEqual(result["restored"], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")

    def test_untracked_files_are_left_alone(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            _write(root, "kept.py", "value = 1\n")
            self._commit(root)
            _write(root, "new.py", "fresh = 1\n")

            result = verify_mod.restore_from_git(root, ["new.py"])

            self.assertEqual(result["restored"], 0)
            self.assertIn("tracked by git", result["error"])
            self.assertTrue((root / "new.py").exists())

    def test_verify_reports_clean_tree_as_nothing_to_do(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            _write(root, "app.py", "value = 1\n")
            self._commit(root)
            report = verify_mod.verify_changes(root, with_tests=False)
        self.assertEqual(report.verdict, verify_mod.VERDICT_NO_CHANGES)
        self.assertTrue(report.ok)


class BinaryResolutionTest(unittest.TestCase):
    """Resolution has to survive Windows, where the bare name is a trap.

    npm writes ``eslint`` and ``eslint.cmd`` side by side; only the second one
    can be launched. Picking the first because it exists on disk is how the
    linters, the language servers, and the test runners all break at once, since
    the three share this resolver.
    """

    def setUp(self) -> None:
        linters_mod._resolve_binary.cache_clear()
        self.addCleanup(linters_mod._resolve_binary.cache_clear)

    # Simulating Windows by patching os.name would turn every Path into a
    # WindowsPath, which cannot be instantiated on POSIX. The suffix list is the
    # only platform-dependent input, so patching that reaches the same code.
    _AS_WINDOWS = (".exe", ".cmd", "")

    def test_the_suffix_list_is_empty_off_windows(self) -> None:
        with patch.object(linters_mod.os, "name", "posix"):
            self.assertEqual(linters_mod._executable_suffixes(), ("",))

    def test_windows_tries_pathext_first_and_the_bare_name_last(self) -> None:
        with (
            patch.object(linters_mod.os, "name", "nt"),
            patch.dict(linters_mod.os.environ, {"PATHEXT": ".EXE;.CMD"}),
        ):
            self.assertEqual(linters_mod._executable_suffixes(), (".exe", ".cmd", ""))

    def test_windows_prefers_the_launchable_shim_over_the_shell_script(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "node_modules/.bin/eslint", "#!/bin/sh\n")
            _write(root, "node_modules/.bin/eslint.cmd", "@echo off\n")
            with patch.object(
                linters_mod, "_executable_suffixes", return_value=self._AS_WINDOWS
            ):
                found = linters_mod._resolve_binary(
                    "eslint", ("node_modules/.bin/eslint",), False, str(root)
                )
        self.assertIsNotNone(found)
        self.assertTrue(str(found).endswith(".cmd"))

    def test_posix_keeps_the_extensionless_shim(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "node_modules/.bin/eslint", "#!/bin/sh\n")
            found = linters_mod._resolve_binary(
                "eslint", ("node_modules/.bin/eslint",), False, str(root)
            )
        self.assertIsNotNone(found)
        self.assertTrue(str(found).endswith("eslint"))

    def test_a_system_python_on_windows_is_found_under_scripts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "Python312/python.exe", "")
            _write(root, "Python312/Scripts/ruff.exe", "")
            interpreter = root / "Python312" / "python.exe"
            with (
                patch.object(
                    linters_mod, "_executable_suffixes", return_value=self._AS_WINDOWS
                ),
                patch.object(linters_mod.sys, "executable", str(interpreter)),
            ):
                found = linters_mod._resolve_binary("ruff", (), True, str(root))
        self.assertIsNotNone(found)
        self.assertTrue(str(found).endswith("ruff.exe"))

    def test_a_missing_tool_still_resolves_to_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(
                    linters_mod, "_executable_suffixes", return_value=self._AS_WINDOWS
                ),
                patch.object(linters_mod.shutil, "which", return_value=None),
            ):
                found = linters_mod._resolve_binary(
                    "nonexistent-tool", ("node_modules/.bin/nope",), False, str(root)
                )
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
