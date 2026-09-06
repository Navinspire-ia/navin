"""Tests for creating files and folders from the WebUI explorer."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from navin.security.workspace_access import default_workspace_scope
from navin.webui.file_preview import WebUIFilePreviewError, file_create_payload


class FileCreateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def scope(self, *, restrict: bool = True):
        return default_workspace_scope(self.root, restrict)

    def create(self, path: str, kind: str = "file", *, restrict: bool = True):
        return file_create_payload(path, kind=kind, scope=self.scope(restrict=restrict))

    # -- files -------------------------------------------------------------

    def test_creates_an_empty_file_at_the_project_root(self):
        payload = self.create("notes.md")
        target = self.root / "notes.md"
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(), "")
        self.assertEqual(payload["display_path"], "notes.md")
        self.assertEqual(payload["kind"], "file")

    def test_creates_missing_parents_for_a_nested_file(self):
        # One entry of a path is what the user means; making them create each
        # folder by hand first would be the wrong kind of faithful.
        self.create("src/components/Button.tsx")
        self.assertTrue((self.root / "src/components/Button.tsx").is_file())

    def test_accepts_an_absolute_path_inside_the_project(self):
        self.create(str(self.root / "deep" / "file.txt"))
        self.assertTrue((self.root / "deep/file.txt").is_file())

    def test_reports_the_path_relative_to_the_project(self):
        payload = self.create("src/app.py")
        self.assertEqual(payload["display_path"], "src/app.py")

    # -- directories -------------------------------------------------------

    def test_creates_a_directory(self):
        payload = self.create("assets", kind="directory")
        self.assertTrue((self.root / "assets").is_dir())
        self.assertEqual(payload["kind"], "directory")

    def test_creates_a_nested_directory_in_one_step(self):
        self.create("a/b/c", kind="directory")
        self.assertTrue((self.root / "a/b/c").is_dir())

    # -- refusals ----------------------------------------------------------

    def test_refuses_to_replace_an_existing_file(self):
        (self.root / "keep.txt").write_text("precious")
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.create("keep.txt")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual((self.root / "keep.txt").read_text(), "precious")

    def test_refuses_to_replace_an_existing_directory(self):
        (self.root / "src").mkdir()
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.create("src", kind="directory")
        self.assertEqual(ctx.exception.status, 409)

    def test_refuses_an_empty_path(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.create("   ")
        self.assertEqual(ctx.exception.status, 400)

    def test_refuses_an_unknown_kind(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.create("x", kind="symlink")
        self.assertEqual(ctx.exception.status, 400)

    def test_refuses_to_escape_the_project_with_dot_dot(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.create("../escaped.txt")
        self.assertIn(ctx.exception.status, {400, 403})
        self.assertFalse((self.root.parent / "escaped.txt").exists())

    def test_refuses_to_escape_through_a_deeper_dot_dot(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.create("src/../../escaped.txt")
        self.assertIn(ctx.exception.status, {400, 403})
        self.assertFalse((self.root.parent / "escaped.txt").exists())

    def test_refuses_an_absolute_path_outside_the_project(self):
        with tempfile.TemporaryDirectory() as outside:
            with self.assertRaises(WebUIFilePreviewError) as ctx:
                self.create(str(Path(outside) / "escaped.txt"))
            self.assertEqual(ctx.exception.status, 403)
            self.assertFalse((Path(outside) / "escaped.txt").exists())

    @unittest.skipIf(os.name == "nt", "symlink creation needs privileges on Windows")
    def test_refuses_a_parent_symlinked_out_of_the_project(self):
        # The check has to resolve the nearest existing ancestor, not just
        # compare strings: a symlink inside the project is a legitimate-looking
        # path whose real location is anywhere at all.
        with tempfile.TemporaryDirectory() as outside:
            (self.root / "link").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(WebUIFilePreviewError) as ctx:
                self.create("link/escaped.txt")
            self.assertEqual(ctx.exception.status, 403)
            self.assertFalse((Path(outside) / "escaped.txt").exists())

    def test_allows_an_outside_path_when_the_workspace_is_not_restricted(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "allowed.txt"
            self.create(str(target), restrict=False)
            self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
