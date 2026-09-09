# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for deleting and renaming entries from the WebUI explorer."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from navin.security.workspace_access import default_workspace_scope
from navin.webui.file_preview import (
    WebUIFilePreviewError,
    file_delete_payload,
    file_rename_payload,
)


class ExplorerManageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def scope(self, *, restrict: bool = True):
        return default_workspace_scope(self.root, restrict)

    def delete(self, path: str, *, restrict: bool = True):
        return file_delete_payload(path, scope=self.scope(restrict=restrict))

    def rename(self, path: str, name: str, *, restrict: bool = True):
        return file_rename_payload(path, name, scope=self.scope(restrict=restrict))

    # -- delete ------------------------------------------------------------

    def test_deletes_a_file(self):
        (self.root / "gone.txt").write_text("x")
        payload = self.delete("gone.txt")
        self.assertFalse((self.root / "gone.txt").exists())
        self.assertEqual(payload["kind"], "file")
        self.assertTrue(payload["deleted"])

    def test_deletes_a_directory_and_its_contents(self):
        (self.root / "pkg" / "inner").mkdir(parents=True)
        (self.root / "pkg" / "inner" / "a.txt").write_text("x")
        payload = self.delete("pkg")
        self.assertFalse((self.root / "pkg").exists())
        self.assertEqual(payload["kind"], "directory")

    def test_refuses_to_delete_the_project_root(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.delete(str(self.root))
        self.assertEqual(ctx.exception.status, 403)
        self.assertTrue(self.root.is_dir())

    def test_refuses_to_delete_outside_the_project(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "keep.txt"
            target.write_text("precious")
            with self.assertRaises(WebUIFilePreviewError) as ctx:
                self.delete(str(target))
            self.assertEqual(ctx.exception.status, 403)
            self.assertTrue(target.exists())

    def test_reports_a_missing_path_as_not_found(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.delete("nope.txt")
        self.assertEqual(ctx.exception.status, 404)

    @unittest.skipIf(os.name == "nt", "symlink creation needs privileges on Windows")
    def test_deleting_a_symlink_does_not_touch_its_target(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "real.txt"
            target.write_text("precious")
            (self.root / "link").symlink_to(target)
            # The link resolves outside the project, so it is refused rather
            # than followed - the danger being an unlink that lands elsewhere.
            with self.assertRaises(WebUIFilePreviewError):
                self.delete("link")
            self.assertTrue(target.exists())

    # -- rename ------------------------------------------------------------

    def test_renames_a_file_in_place(self):
        (self.root / "old.txt").write_text("body")
        payload = self.rename("old.txt", "new.txt")
        self.assertFalse((self.root / "old.txt").exists())
        self.assertEqual((self.root / "new.txt").read_text(), "body")
        self.assertEqual(payload["display_path"], "new.txt")

    def test_renames_a_directory(self):
        (self.root / "old").mkdir()
        (self.root / "old" / "a.txt").write_text("x")
        self.rename("old", "new")
        self.assertTrue((self.root / "new" / "a.txt").is_file())

    def test_a_name_with_a_separator_moves_the_entry(self):
        (self.root / "a.txt").write_text("body")
        self.rename("a.txt", "lib/deep/a.txt")
        self.assertEqual((self.root / "lib/deep/a.txt").read_text(), "body")

    def test_a_bare_name_stays_in_the_same_folder(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "a.txt").write_text("body")
        self.rename("src/a.txt", "b.txt")
        self.assertTrue((self.root / "src" / "b.txt").is_file())
        self.assertFalse((self.root / "b.txt").exists())

    def test_renaming_to_the_same_name_is_a_no_op(self):
        (self.root / "a.txt").write_text("body")
        payload = self.rename("a.txt", "a.txt")
        self.assertFalse(payload["renamed"])
        self.assertEqual((self.root / "a.txt").read_text(), "body")

    def test_refuses_to_overwrite_an_existing_entry(self):
        (self.root / "a.txt").write_text("a")
        (self.root / "b.txt").write_text("b")
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.rename("a.txt", "b.txt")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual((self.root / "b.txt").read_text(), "b")

    def test_refuses_to_move_a_folder_inside_itself(self):
        (self.root / "pkg").mkdir()
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.rename("pkg", "pkg/nested")
        self.assertEqual(ctx.exception.status, 400)
        self.assertTrue((self.root / "pkg").is_dir())

    def test_refuses_to_rename_out_of_the_project(self):
        (self.root / "a.txt").write_text("body")
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.rename("a.txt", "../escaped.txt")
        self.assertIn(ctx.exception.status, {400, 403})
        self.assertFalse((self.root.parent / "escaped.txt").exists())

    def test_refuses_an_empty_name(self):
        (self.root / "a.txt").write_text("body")
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.rename("a.txt", "   ")
        self.assertEqual(ctx.exception.status, 400)

    def test_refuses_to_rename_the_project_root(self):
        with self.assertRaises(WebUIFilePreviewError) as ctx:
            self.rename(str(self.root), "other")
        self.assertEqual(ctx.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
