# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""read_file and grep: several files or patterns in one call.

The case behind this file: a 63-call build turn read files 19 times and ran 16
searches, one per call, each a model round trip of several seconds. Reading
and searching are independent, so they go in one call.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.file_state import FileStates
from navin.agent.tools.filesystem import ReadFileTool
from navin.agent.tools.search import GrepTool


class ReadManyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "a.py").write_text("".join(f"a{n} = {n}\n" for n in range(1, 21)), encoding="utf-8")
        (self.root / "b.py").write_text("beta = 1\ngamma = 2\n", encoding="utf-8")

    def _read(self, **arguments):
        tool = ReadFileTool(workspace=self.root, file_states=FileStates())
        return asyncio.run(tool.execute(**arguments))

    def test_two_files_come_back_under_their_own_headings(self) -> None:
        result = self._read(files=[{"path": "a.py", "limit": 3}, {"path": "b.py"}])
        text = str(result)
        self.assertFalse(getattr(result, "is_error", False), text)
        self.assertIn("=== a.py ===", text)
        self.assertIn("=== b.py ===", text)
        self.assertIn("a1 = 1", text)
        self.assertNotIn("a4 = 4", text)  # limit honored
        self.assertIn("gamma = 2", text)

    def test_offset_is_honored_per_file(self) -> None:
        text = str(self._read(files=[{"path": "a.py", "offset": 18}]))
        self.assertIn("a18 = 18", text)
        self.assertNotIn("a2 = 2", text)

    def test_one_missing_file_does_not_hide_the_others(self) -> None:
        result = self._read(files=[{"path": "nope.py"}, {"path": "b.py"}])
        self.assertFalse(getattr(result, "is_error", False))
        self.assertIn("beta = 1", str(result))
        self.assertIn("=== nope.py ===", str(result))

    def test_all_missing_is_an_error(self) -> None:
        result = self._read(files=[{"path": "nope.py"}, {"path": "nada.py"}])
        self.assertTrue(getattr(result, "is_error", False))

    def test_path_and_files_cannot_be_mixed_and_the_count_is_capped(self) -> None:
        self.assertTrue(getattr(self._read(path="a.py", files=[{"path": "b.py"}]), "is_error", False))
        many = [{"path": "b.py"} for _ in range(11)]
        self.assertTrue(getattr(self._read(files=many), "is_error", False))

    def test_a_single_path_still_works(self) -> None:
        self.assertIn("beta = 1", str(self._read(path="b.py")))
        self.assertTrue(getattr(self._read(), "is_error", False))


class GrepManyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "one.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        (self.root / "two.py").write_text("def beta():\n    return 2\n", encoding="utf-8")

    def _grep(self, **arguments):
        tool = GrepTool(workspace=self.root)
        return asyncio.run(tool.execute(**arguments))

    def test_several_patterns_each_get_a_section(self) -> None:
        result = self._grep(patterns=["alpha", "beta"], output_mode="content")
        text = str(result)
        self.assertFalse(getattr(result, "is_error", False), text)
        self.assertIn("=== pattern: alpha ===", text)
        self.assertIn("=== pattern: beta ===", text)
        self.assertIn("one.py", text)
        self.assertIn("two.py", text)

    def test_a_pattern_with_no_match_does_not_fail_the_batch(self) -> None:
        text = str(self._grep(patterns=["alpha", "zzz_nothing"]))
        self.assertIn("=== pattern: zzz_nothing ===", text)
        self.assertIn("one.py", text)

    def test_pattern_and_patterns_cannot_be_mixed_and_one_is_required(self) -> None:
        self.assertTrue(getattr(self._grep(pattern="alpha", patterns=["beta"]), "is_error", False))
        self.assertTrue(getattr(self._grep(), "is_error", False))
        self.assertTrue(getattr(self._grep(patterns=["x"] * 9), "is_error", False))

    def test_a_single_pattern_still_works(self) -> None:
        self.assertIn("one.py", str(self._grep(pattern="alpha")))


if __name__ == "__main__":
    unittest.main()
