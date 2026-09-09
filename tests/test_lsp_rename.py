# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A rename the agent has to retype by hand is a rename that loses a call site.

The language server already knows every edit; these tests cover turning that
answer into files on disk without corrupting anything, and refusing rather than
writing half of it.
"""

from __future__ import annotations

import asyncio
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.agent.tools.lsp import LspTool, _apply_edits, _utf16_index


def _run(coro):
    return asyncio.run(coro)


def _edit(line: int, col: int, end_col: int, text: str) -> dict:
    return {
        "line": line,
        "col": col,
        "end_line": line,
        "end_col": end_col,
        "new_text": text,
    }


class Utf16ColumnTest(unittest.TestCase):
    """LSP columns count UTF-16 units, which is not the character offset."""

    def test_ascii_columns_are_unchanged(self) -> None:
        self.assertEqual(_utf16_index("def alpha():", 4), 4)

    def test_a_column_past_an_astral_character_shifts_back(self) -> None:
        # The emoji is one character but two UTF-16 units, so unit 3 is index 2.
        self.assertEqual(_utf16_index("a\U0001f600bc", 3), 2)

    def test_an_accented_character_is_one_unit(self) -> None:
        self.assertEqual(_utf16_index("café x", 5), 5)

    def test_a_column_beyond_the_line_clamps(self) -> None:
        self.assertEqual(_utf16_index("ab", 99), 2)


class ApplyEditsTest(unittest.TestCase):
    def test_two_edits_on_one_line_both_land(self) -> None:
        """Applied front-to-back, the second edit would land at a stale offset."""
        line = "alpha(alpha())\n"
        out = _apply_edits(line, [_edit(1, 1, 6, "beta"), _edit(1, 7, 12, "beta")])
        self.assertEqual(out, "beta(beta())\n")

    def test_a_longer_name_does_not_corrupt_the_tail(self) -> None:
        out = _apply_edits("x = a + a\n", [_edit(1, 5, 6, "much_longer")])
        self.assertEqual(out, "x = much_longer + a\n")

    def test_an_edit_past_the_end_is_rejected(self) -> None:
        """A file that moved since the server read it must not be half-written."""
        with self.assertRaises(ValueError) as caught:
            _apply_edits("one line\n", [_edit(40, 1, 2, "x")])
        self.assertIn("may have changed", str(caught.exception))

    def test_a_rename_after_an_emoji_cuts_in_the_right_place(self) -> None:
        text = "# \U0001f600 note\nvalue = old\n"
        out = _apply_edits(text, [_edit(2, 9, 12, "new")])
        self.assertEqual(out, "# \U0001f600 note\nvalue = new\n")

    def test_crlf_content_keeps_its_carriage_returns(self) -> None:
        """Splitting on \\n leaves the \\r attached, so it must survive intact."""
        out = _apply_edits("a = old\r\nb = 2\r\n", [_edit(1, 5, 8, "new")])
        self.assertEqual(out, "a = new\r\nb = 2\r\n")


class _ToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.tool = LspTool(workspace=str(self.root), restrict_to_workspace=True)


class ApplyRenameTest(_ToolTest):
    def test_files_are_written_in_their_own_encoding(self) -> None:
        """A cp1252 file rewritten as UTF-8 would corrupt every accent in it."""
        target = self.root / "mod.py"
        target.write_bytes("# caf\xe9\nvalue = old\n".encode("cp1252"))
        written, problem = self.tool._apply_rename(
            {"mod.py": [_edit(2, 9, 12, "new")]}
        )
        self.assertIsNone(problem)
        self.assertEqual([p.name for p in written], ["mod.py"])
        self.assertEqual(
            target.read_bytes(), "# caf\xe9\nvalue = new\n".encode("cp1252")
        )

    def test_an_edit_outside_the_project_is_refused_entirely(self) -> None:
        """Half a rename leaves the project broken with no record of which half."""
        inside = self.root / "mine.py"
        inside.write_text("value = old\n", encoding="utf-8")
        with TemporaryDirectory() as outside:
            stub = Path(outside) / "vendored.py"
            stub.write_text("value = old\n", encoding="utf-8")
            written, problem = self.tool._apply_rename({
                "mine.py": [_edit(1, 9, 12, "new")],
                str(stub): [_edit(1, 9, 12, "new")],
            })
        self.assertEqual(written, [])
        self.assertIn("outside the project", problem or "")
        self.assertEqual(inside.read_text(encoding="utf-8"), "value = old\n")

    def test_a_binary_file_is_refused(self) -> None:
        (self.root / "blob.py").write_bytes(b"\x7fELF\x00\x01")
        written, problem = self.tool._apply_rename(
            {"blob.py": [_edit(1, 1, 2, "x")]}
        )
        self.assertEqual(written, [])
        self.assertIn("not text", problem or "")

    def test_a_stale_line_number_leaves_every_file_untouched(self) -> None:
        first = self.root / "a.py"
        second = self.root / "b.py"
        first.write_text("value = old\n", encoding="utf-8")
        second.write_text("value = old\n", encoding="utf-8")
        written, problem = self.tool._apply_rename({
            "a.py": [_edit(1, 9, 12, "new")],
            "b.py": [_edit(99, 1, 2, "new")],
        })
        self.assertEqual(written, [])
        self.assertIn("may have changed", problem or "")
        self.assertEqual(first.read_text(encoding="utf-8"), "value = old\n")


class ConcurrencyTest(_ToolTest):
    """Queries batch; a call that writes must not."""

    def test_a_query_is_batchable(self) -> None:
        self.assertTrue(
            self.tool.call_concurrency_safe({"action": "hover", "path": "a.py"})
        )

    def test_an_applying_rename_is_not(self) -> None:
        self.assertFalse(
            self.tool.call_concurrency_safe({"action": "rename", "apply": True})
        )

    def test_a_preview_rename_still_is(self) -> None:
        self.assertTrue(self.tool.call_concurrency_safe({"action": "rename"}))

    def test_a_non_dict_argument_does_not_crash_the_batcher(self) -> None:
        self.assertTrue(self.tool.call_concurrency_safe("garbage"))


class PartialAnswerTest(_ToolTest):
    """A server that misses a caller must not be allowed to write.

    This is not hypothetical: pyright answers a rename from the files it has
    finished loading, so with a cold workspace it reports the definition alone and
    says nothing about the module importing it. Applying that leaves a call to a
    name that no longer exists.
    """

    def setUp(self) -> None:
        super().setUp()
        (self.root / "lib.py").write_text(
            "def old_name(value):\n    return value\n", encoding="utf-8"
        )
        (self.root / "app.py").write_text(
            "from lib import old_name\n\nprint(old_name(1))\n", encoding="utf-8"
        )
        self._partial = {"lib.py": [self._edit(1, 5, 13)]}

    @staticmethod
    def _edit(line: int, col: int, end_col: int) -> dict:
        return {
            "line": line,
            "col": col,
            "end_line": line,
            "end_col": end_col,
            "new_text": "new_name",
        }

    def _stub(self, edits: dict) -> None:
        """Force every candidate server to return the same answer."""
        from navin.lsp.manager import LspManager

        for name, replacement in (
            ("rename", lambda *a, **k: edits),
            ("installed_servers_for", lambda *a, **k: ["pyright", "pylsp"]),
        ):
            patcher = mock.patch.object(LspManager, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_partial_rename_is_refused(self) -> None:
        self._stub(self._partial)
        out = _run(
            self.tool.execute(
                action="rename",
                path="lib.py",
                line=1,
                col=5,
                new_name="new_name",
                apply=True,
            )
        )
        self.assertIn("Error", out)
        self.assertIn("app.py", out)

    def test_nothing_is_written_when_it_is_refused(self) -> None:
        """A half-applied rename is worse than no rename at all."""
        self._stub(self._partial)
        before = (self.root / "lib.py").read_text(encoding="utf-8")
        _run(
            self.tool.execute(
                action="rename", path="lib.py", line=1, col=5,
                new_name="new_name", apply=True,
            )
        )
        self.assertEqual((self.root / "lib.py").read_text(encoding="utf-8"), before)
        self.assertIn("old_name", (self.root / "app.py").read_text(encoding="utf-8"))

    def test_a_preview_names_the_files_the_server_missed(self) -> None:
        self._stub(self._partial)
        out = _run(
            self.tool.execute(
                action="rename", path="lib.py", line=1, col=5, new_name="new_name"
            )
        )
        self.assertIn("app.py", out)
        self.assertIn("code_index", out)

    def test_a_complete_rename_is_not_second_guessed(self) -> None:
        """The guard must stay silent when the server did cover everything."""
        self._stub(
            {"lib.py": [self._edit(1, 5, 13)], "app.py": [self._edit(3, 7, 15)]}
        )
        out = _run(
            self.tool.execute(
                action="rename", path="lib.py", line=1, col=5,
                new_name="new_name", apply=True,
            )
        )
        self.assertNotIn("Error", out)
        self.assertIn("Applied", out)


class IdentifierAtTest(unittest.TestCase):
    """The guard needs the old name, and it only has a cursor position."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _name(self, body: str, line: int, col: int) -> str:
        from navin.agent.tools.lsp import _identifier_at

        target = self.root / "f.py"
        target.write_text(body, encoding="utf-8")
        return _identifier_at(target, line, col)

    def test_a_position_inside_a_name_finds_the_whole_name(self) -> None:
        self.assertEqual(self._name("def compute_total():\n    pass\n", 1, 8), "compute_total")

    def test_the_first_character_counts(self) -> None:
        self.assertEqual(self._name("def compute_total():\n    pass\n", 1, 5), "compute_total")

    def test_a_position_on_punctuation_yields_nothing(self) -> None:
        self.assertEqual(self._name("def f():\n", 1, 7), "")

    def test_a_line_past_the_end_yields_nothing(self) -> None:
        self.assertEqual(self._name("x = 1\n", 9, 1), "")

    def test_a_name_after_an_accent_is_not_shifted(self) -> None:
        """The column arrives in UTF-16 units, not Python indices."""
        self.assertEqual(self._name('s = "é"\nvaleur = 1\n', 2, 1), "valeur")


class EndToEndRenameTest(_ToolTest):
    """With a real server installed, the whole path must work, not just the parts."""

    def setUp(self) -> None:
        super().setUp()
        if not (shutil.which("pyright-langserver") or shutil.which("pylsp")):
            self.skipTest("no python language server installed")

    def test_a_rename_updates_the_definition_and_its_caller(self) -> None:
        """Whichever server is installed, the result must be a working project."""
        (self.root / "lib.py").write_text(
            "def old_name(value):\n    return value\n", encoding="utf-8"
        )
        (self.root / "app.py").write_text(
            "from lib import old_name\n\nprint(old_name(1))\n", encoding="utf-8"
        )
        out = _run(
            self.tool.execute(
                action="rename",
                path="lib.py",
                line=1,
                col=5,
                new_name="new_name",
                apply=True,
            )
        )
        caller = (self.root / "app.py").read_text(encoding="utf-8")
        definition = (self.root / "lib.py").read_text(encoding="utf-8")
        if "Applied" in out:
            self.assertIn("new_name", definition)
            self.assertIn("new_name", caller)
            self.assertNotIn("old_name", caller)
        else:
            # Refusing is the other acceptable outcome; a half rename is not.
            self.assertIn("Error", out)
            self.assertIn("old_name", definition)
            self.assertIn("old_name", caller)

    def test_a_preview_writes_nothing(self) -> None:
        source = "def old_name(value):\n    return value\n"
        (self.root / "lib.py").write_text(source, encoding="utf-8")
        out = _run(
            self.tool.execute(
                action="rename", path="lib.py", line=1, col=5, new_name="new_name"
            )
        )
        self.assertIn("apply=true", out)
        self.assertEqual((self.root / "lib.py").read_text(encoding="utf-8"), source)


if __name__ == "__main__":
    unittest.main()
