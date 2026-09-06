"""Tests for the starting directory of a terminal opened from the explorer."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from navin.channels.websocket import _terminal_cwd
from navin.security.workspace_access import build_workspace_scope, default_workspace_scope


class TerminalCwdTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "note.txt").write_text("x")
        self.scope = default_workspace_scope(self.root, True)

    def cwd(self, requested):
        return _terminal_cwd(requested, self.scope)

    def test_a_folder_inside_the_project_is_honoured(self):
        self.assertEqual(self.cwd(str(self.root / "pkg")), str(self.root / "pkg"))

    def test_a_file_falls_back_to_the_folder_holding_it(self):
        # "Open in terminal" on a file is a reasonable thing to click, and its
        # folder is the only useful answer.
        self.assertEqual(
            self.cwd(str(self.root / "pkg" / "note.txt")), str(self.root / "pkg"),
        )

    def test_nothing_requested_means_the_project(self):
        for value in (None, "", "   ", 42, {"path": "x"}):
            self.assertEqual(self.cwd(value), str(self.root))

    def test_a_path_outside_the_project_falls_back_to_the_project(self):
        # Silently ignored rather than refused: the shell has to start
        # somewhere, and a wrong directory is worse than the project root.
        self.assertEqual(self.cwd(str(Path(self.root).parent)), str(self.root))
        self.assertEqual(self.cwd("/etc"), str(self.root))

    def test_a_missing_path_falls_back_to_the_project(self):
        self.assertEqual(self.cwd(str(self.root / "nope")), str(self.root))

    @unittest.skipIf(os.name == "nt", "POSIX symlink behaviour")
    def test_a_symlink_pointing_out_of_the_project_is_refused(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: outside.rmdir())
        link = self.root / "escape"
        link.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.cwd(str(link)), str(self.root))


class FullAccessTerminalCwdTest(unittest.TestCase):
    """A full-access scope honours explicit folders anywhere on disk.

    The editor may legitimately show a project that is not the scope the
    gateway resolved (no active chat yet, scope not persisted). Refusing the
    requested folder used to drop the shell into the default workspace - the
    "terminal opens in the wrong project" bug.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        self.root = base / "default-workspace"
        self.root.mkdir()
        self.elsewhere = base / "real-project"
        self.elsewhere.mkdir()
        (self.elsewhere / "file.txt").write_text("x")
        self.scope = build_workspace_scope(self.root, "full")

    def cwd(self, requested):
        return _terminal_cwd(requested, self.scope)

    def test_an_existing_folder_outside_the_scope_is_honoured(self):
        self.assertEqual(self.cwd(str(self.elsewhere)), str(self.elsewhere))

    def test_a_file_outside_the_scope_falls_back_to_its_folder(self):
        self.assertEqual(
            self.cwd(str(self.elsewhere / "file.txt")), str(self.elsewhere),
        )

    def test_a_missing_path_still_falls_back_to_the_project(self):
        self.assertEqual(self.cwd(str(self.elsewhere / "nope")), str(self.root))

    def test_nothing_requested_still_means_the_project(self):
        self.assertEqual(self.cwd(None), str(self.root))


if __name__ == "__main__":
    unittest.main()
