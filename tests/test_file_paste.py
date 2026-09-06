"""Tests for copy, duplicate and move-on-paste in the WebUI explorer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.security.workspace_access import default_workspace_scope
from navin.webui.file_preview import WebUIFilePreviewError, file_paste_payload


class ExplorerPasteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        (self.root / "note.txt").write_text("hello")
        (self.root / "dest").mkdir()
        (self.root / "pkg" / "inner").mkdir(parents=True)
        (self.root / "pkg" / "inner" / "deep.txt").write_text("deep")

    def paste(self, path: str, parent: str | None, *, move: bool = False):
        return file_paste_payload(
            path,
            parent,
            move=move,
            scope=default_workspace_scope(self.root, True),
        )

    def names(self, folder: str = "") -> set[str]:
        base = self.root / folder if folder else self.root
        return {p.name for p in base.iterdir()}

    # -- copy --------------------------------------------------------------

    def test_copies_a_file_into_another_folder(self):
        payload = self.paste("note.txt", "dest")
        self.assertTrue(payload["pasted"])
        self.assertEqual(payload["kind"], "file")
        self.assertIsNone(payload["previous_path"])
        self.assertEqual((self.root / "dest" / "note.txt").read_text(), "hello")
        self.assertTrue((self.root / "note.txt").exists())

    def test_copies_a_folder_with_everything_inside_it(self):
        self.paste("pkg", "dest")
        self.assertEqual(
            (self.root / "dest" / "pkg" / "inner" / "deep.txt").read_text(), "deep",
        )

    def test_an_empty_parent_means_the_project_root(self):
        self.paste("dest", None)
        self.assertIn("dest copy", self.names())

    # -- suffixing ---------------------------------------------------------

    def test_pasting_beside_the_original_suffixes_rather_than_failing(self):
        payload = self.paste("note.txt", None)
        self.assertEqual(Path(payload["path"]).name, "note copy.txt")
        self.assertEqual((self.root / "note copy.txt").read_text(), "hello")

    def test_the_suffix_counts_up_on_repeat_pastes(self):
        self.paste("note.txt", None)
        self.paste("note.txt", None)
        third = self.paste("note.txt", None)
        self.assertEqual(Path(third["path"]).name, "note copy 3.txt")

    def test_a_double_extension_survives_the_suffix(self):
        (self.root / "bundle.tar.gz").write_text("z")
        payload = self.paste("bundle.tar.gz", None)
        self.assertEqual(Path(payload["path"]).name, "bundle copy.tar.gz")

    def test_a_dotfile_keeps_its_leading_dot(self):
        (self.root / ".gitignore").write_text("x")
        payload = self.paste(".gitignore", None)
        self.assertEqual(Path(payload["path"]).name, ".gitignore copy")

    def test_a_folder_has_no_extension_to_preserve(self):
        payload = self.paste("pkg", None)
        self.assertEqual(Path(payload["path"]).name, "pkg copy")

    def test_an_existing_copy_is_never_overwritten(self):
        (self.root / "note copy.txt").write_text("mine")
        self.paste("note.txt", None)
        self.assertEqual((self.root / "note copy.txt").read_text(), "mine")

    # -- move --------------------------------------------------------------

    def test_moving_leaves_nothing_behind(self):
        payload = self.paste("note.txt", "dest", move=True)
        self.assertTrue(payload["pasted"])
        self.assertEqual(payload["previous_path"], str(self.root / "note.txt"))
        self.assertFalse((self.root / "note.txt").exists())
        self.assertEqual((self.root / "dest" / "note.txt").read_text(), "hello")

    def test_moving_into_the_folder_it_is_already_in_is_a_no_op(self):
        # Otherwise a cut-then-paste in place would rename the file to
        # "note copy.txt", which is not what the user asked for.
        payload = self.paste("note.txt", None, move=True)
        self.assertFalse(payload["pasted"])
        self.assertEqual(self.names() & {"note copy.txt"}, set())
        self.assertTrue((self.root / "note.txt").exists())

    def test_moving_onto_a_taken_name_suffixes_instead_of_clobbering(self):
        (self.root / "dest" / "note.txt").write_text("theirs")
        payload = self.paste("note.txt", "dest", move=True)
        self.assertEqual(Path(payload["path"]).name, "note copy.txt")
        self.assertEqual((self.root / "dest" / "note.txt").read_text(), "theirs")

    # -- refusals ----------------------------------------------------------

    def test_a_folder_cannot_be_pasted_into_itself(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.paste("pkg", "pkg")
        self.assertEqual(ctx.exception.status, 400)

    def test_a_folder_cannot_be_pasted_into_its_own_descendant(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.paste("pkg", "pkg/inner")
        self.assertEqual(ctx.exception.status, 400)

    def test_the_project_folder_itself_cannot_be_pasted(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.paste(str(self.root), "dest")
        self.assertEqual(ctx.exception.status, 403)

    def test_a_file_is_not_a_destination(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.paste("note.txt", "dest/../note.txt")
        self.assertEqual(ctx.exception.status, 400)

    def test_a_source_outside_the_project_is_refused(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.paste("../escape.txt", "dest")
        self.assertIn(ctx.exception.status, {403, 404})

    def test_a_destination_outside_the_project_is_refused(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.paste("note.txt", "..")
        self.assertIn(ctx.exception.status, {403, 404})

    def test_a_missing_source_is_reported(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.paste("nope.txt", "dest")
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
