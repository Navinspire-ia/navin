# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""test_run: several targets in one call.

The case behind this file: checking three changed test files used to cost three
model round trips of several seconds each. They go in one call, each run on its
own and reported under its own heading.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.tools import quality as quality_tools
from navin.agent.tools.registry import ToolRegistry
from navin.quality import testing as testing_mod


def _outcome(passed: int = 1, failed: int = 0) -> testing_mod.TestOutcome:
    return testing_mod.TestOutcome(
        runner="pytest", ran=True, passed=passed, failed=failed, total=passed + failed,
        exit_code=1 if failed else 0,
    )


class RunManyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        # A target must exist before it is handed to a runner.
        (self.root / "tests").mkdir()
        for name in ["a.py", "b.py", *(f"t{n}.py" for n in range(7))]:
            (self.root / "tests" / name).write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        self.tool = quality_tools.TestRunTool(workspace=str(self.root))

    def _run(self, **arguments):
        return asyncio.run(self.tool.execute(**arguments))

    def test_each_target_is_run_and_reported_under_its_own_heading(self) -> None:
        results = {"tests/a.py": [_outcome(2)], "tests/b.py": [_outcome(1, failed=1)]}
        with mock.patch.object(testing_mod, "run_tests", side_effect=lambda root, **kw: results[kw["target"]]) as run:
            result = self._run(targets=["tests/a.py", "tests/b.py"])
        self.assertEqual([call.kwargs["target"] for call in run.call_args_list], ["tests/a.py", "tests/b.py"])
        text = str(result)
        self.assertIn("=== tests/a.py ===", text)
        self.assertIn("=== tests/b.py ===", text)
        self.assertFalse(result.is_error)

    def test_the_evidence_covers_every_target(self) -> None:
        results = {"tests/a.py": [_outcome(2)], "tests/b.py": [_outcome(1, failed=1)]}
        with mock.patch.object(testing_mod, "run_tests", side_effect=lambda root, **kw: results[kw["target"]]):
            result = self._run(targets=["tests/a.py", "tests/b.py"])
        # One failing target must not be hidden by a passing one.
        self.assertIs(result.verification.tests_ok, False)
        self.assertFalse(result.verification.ok)
        self.assertIn("3 passed, 1 failed", result.verification.summary)

    def test_all_targets_passing_is_a_pass(self) -> None:
        with mock.patch.object(testing_mod, "run_tests", return_value=[_outcome(2)]):
            result = self._run(targets=["tests/a.py", "tests/b.py"])
        self.assertIs(result.verification.tests_ok, True)

    def test_target_and_targets_cannot_be_mixed_and_the_count_is_capped(self) -> None:
        with mock.patch.object(testing_mod, "run_tests") as run:
            self.assertTrue(self._run(target="tests/a.py", targets=["tests/b.py"]).is_error)
            self.assertTrue(self._run(targets=[f"tests/t{n}.py" for n in range(7)]).is_error)
            self.assertTrue(self._run(targets=["tests/a.py", "  "]).is_error)
        run.assert_not_called()

    def test_a_single_target_still_works_the_old_way(self) -> None:
        with mock.patch.object(testing_mod, "run_tests", return_value=[_outcome()]) as run:
            result = self._run(target="tests/a.py")
        self.assertFalse(result.is_error)
        self.assertEqual(run.call_args.kwargs["target"], "tests/a.py")
        self.assertNotIn("===", str(result))

    def test_targets_pass_through_the_registry_schema(self) -> None:
        registry = ToolRegistry()
        registry.register(self.tool)
        _, params, error = registry.prepare_call("test_run", {"targets": ["tests/a.py", "tests/b.py"]})
        self.assertIsNone(error)
        self.assertEqual(params["action"], "run")
        self.assertEqual(params["targets"], ["tests/a.py", "tests/b.py"])
        _, _, error = registry.prepare_call("test_run", {"targets": []})
        self.assertTrue(error.is_error)


class MissingTargetTest(unittest.TestCase):
    """A guessed test path is refused before the runner starts.

    Seen in a session: ``tests/test_metagraph_tool.py`` next to 20 real files;
    pytest answered "file or directory not found" and ran none of them.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "tests").mkdir()
        for name in ("test_metagraph_graph.py", "test_metagraph_debounce.py", "test_board.py"):
            (self.root / "tests" / name).write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        self.tool = quality_tools.TestRunTool(workspace=str(self.root))

    def _run(self, **arguments):
        return asyncio.run(self.tool.execute(**arguments))

    def test_a_missing_target_is_refused_with_the_closest_real_files(self) -> None:
        with mock.patch.object(testing_mod, "run_tests") as run:
            result = self._run(target="tests/test_metagraph_tool.py")
        run.assert_not_called()
        self.assertTrue(result.is_error)
        text = str(result)
        self.assertIn("tests/test_metagraph_tool.py does not exist", text)
        self.assertIn("tests/test_metagraph_graph.py", text)
        self.assertIn("tests/test_metagraph_debounce.py", text)
        self.assertNotIn("test_board.py", text)
        self.assertIn('find_files glob="tests/test_metagraph*"', text)

    def test_one_missing_target_among_several_stops_the_whole_call(self) -> None:
        with mock.patch.object(testing_mod, "run_tests") as run:
            result = self._run(targets=["tests/test_board.py", "tests/test_metagraph_tool.py"])
        run.assert_not_called()
        self.assertTrue(result.is_error)
        self.assertIn("tests/test_metagraph_tool.py does not exist", str(result))

    def test_a_node_id_or_filter_without_a_path_is_left_to_the_runner(self) -> None:
        outcome = testing_mod.TestOutcome(runner="pytest", ran=True, passed=1, exit_code=0)
        with mock.patch.object(testing_mod, "run_tests", return_value=[outcome]) as run:
            self.assertFalse(self._run(target="tests/test_board.py::test_ok").is_error)
            self.assertFalse(self._run(target="TestBoard.test_ok").is_error)
            self.assertFalse(self._run(target="tests").is_error)
        self.assertEqual(run.call_count, 3)

    def test_a_missing_folder_names_the_real_test_folders(self) -> None:
        with mock.patch.object(testing_mod, "run_tests") as run:
            result = self._run(target="test/test_board.py")
        run.assert_not_called()
        self.assertIn("Its folder test/ does not exist either; test folders here: tests.", str(result))

    def test_verify_refuses_a_missing_test_target_before_linting(self) -> None:
        from navin.quality import verify as verify_mod

        tool = quality_tools.VerifyTool(workspace=str(self.root))
        with mock.patch.object(verify_mod, "verify_changes") as verify:
            result = asyncio.run(tool.execute(action="check", test_target="tests/test_metagraph_tool.py"))
        verify.assert_not_called()
        self.assertTrue(result.is_error)
        self.assertIn("tests/test_metagraph_graph.py", str(result))


if __name__ == "__main__":
    unittest.main()
