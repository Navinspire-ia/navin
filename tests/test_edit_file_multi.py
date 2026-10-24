# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""edit_file: several replacements in one call, and a reply that shows the result.

The case behind this file: a build turn of 63 model calls made 12 separate
edit_file calls on one file and read it back 9 times. Each call is a model
round trip of several seconds, so the edits are batched and the reply carries
the changed lines instead of making the caller read the file again.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.file_state import FileStates
from navin.agent.tools.filesystem import EditFileTool

BODY = "".join(f"line {n}\n" for n in range(1, 31))


class EditManyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.states = FileStates()
        self.target = self.root / "a.txt"
        self.target.write_text(BODY, encoding="utf-8")
        self.states.record_read(self.target)

    def _run(self, **arguments):
        tool = EditFileTool(workspace=self.root, file_states=self.states)
        return asyncio.run(tool.execute(**arguments))

    def test_several_edits_land_in_one_call_and_the_reply_shows_them(self) -> None:
        result = self._run(path="a.txt", edits=[
            {"old_text": "line 3\n", "new_text": "THREE\n"},
            {"old_text": "line 12\n", "new_text": "TWELVE\n"},
            {"old_text": "line 25\n", "new_text": "TWENTY-FIVE\n"},
        ])
        self.assertFalse(getattr(result, "is_error", False), str(result))
        text = self.target.read_text(encoding="utf-8")
        for expected in ("THREE", "TWELVE", "TWENTY-FIVE"):
            self.assertIn(expected, text)
        self.assertIn("Successfully applied 3 edits", str(result))
        # The changed lines come back with their current numbers.
        self.assertIn("     3| THREE", str(result))
        self.assertIn("    12| TWELVE", str(result))

    def test_a_later_edit_sees_the_result_of_an_earlier_one(self) -> None:
        result = self._run(path="a.txt", edits=[
            {"old_text": "line 3\n", "new_text": "alpha\n"},
            {"old_text": "alpha\n", "new_text": "beta\n"},
        ])
        self.assertFalse(getattr(result, "is_error", False), str(result))
        self.assertIn("beta\n", self.target.read_text(encoding="utf-8"))
        self.assertNotIn("alpha", self.target.read_text(encoding="utf-8"))

    def test_one_failing_edit_writes_nothing(self) -> None:
        result = self._run(path="a.txt", edits=[
            {"old_text": "line 3\n", "new_text": "THREE\n"},
            {"old_text": "this text is not in the file", "new_text": "x"},
        ])
        self.assertTrue(getattr(result, "is_error", False))
        self.assertIn("edit 2 of 2 failed", str(result))
        self.assertIn("none of them was written", str(result))
        self.assertEqual(self.target.read_text(encoding="utf-8"), BODY)

    def test_edits_and_old_text_cannot_be_mixed(self) -> None:
        result = self._run(
            path="a.txt", old_text="line 3\n", new_text="x\n",
            edits=[{"old_text": "line 4\n", "new_text": "y\n"}],
        )
        self.assertTrue(getattr(result, "is_error", False))
        self.assertEqual(self.target.read_text(encoding="utf-8"), BODY)

    def test_malformed_edits_are_refused_before_anything_is_written(self) -> None:
        for edits in ([{"old_text": "line 3\n"}], [{"old_text": "", "new_text": "x"}], ["nope"]):
            result = self._run(path="a.txt", edits=edits)
            self.assertTrue(getattr(result, "is_error", False), edits)
            self.assertEqual(self.target.read_text(encoding="utf-8"), BODY)
        too_many = [{"old_text": f"line {n}\n", "new_text": "x\n"} for n in range(1, 22)]
        self.assertTrue(getattr(self._run(path="a.txt", edits=too_many), "is_error", False))
        self.assertEqual(self.target.read_text(encoding="utf-8"), BODY)

    def test_a_missing_file_is_reported(self) -> None:
        result = self._run(path="missing.txt", edits=[{"old_text": "a", "new_text": "b"}])
        self.assertTrue(getattr(result, "is_error", False))


class SingleEditReplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.states = FileStates()
        self.target = self.root / "a.txt"
        self.target.write_text(BODY, encoding="utf-8")
        self.states.record_read(self.target)

    def test_a_single_edit_still_works_and_shows_the_changed_lines(self) -> None:
        tool = EditFileTool(workspace=self.root, file_states=self.states)
        result = asyncio.run(tool.execute(path="a.txt", old_text="line 7\n", new_text="SEVEN\n"))
        self.assertIn("Successfully edited", str(result))
        self.assertIn("     7| SEVEN", str(result))
        # Two lines of context either side, and nothing like the whole file.
        self.assertNotIn("line 20", str(result))

    def test_the_old_calling_convention_without_edits_is_unchanged(self) -> None:
        tool = EditFileTool(workspace=self.root, file_states=self.states)
        missing = asyncio.run(tool.execute(path="a.txt"))
        self.assertTrue(getattr(missing, "is_error", False))
        self.assertIn("old_text", str(missing))


if __name__ == "__main__":
    unittest.main()
