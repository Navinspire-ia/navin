"""Places where a POSIX assumption silently broke the Windows build.

Each case here stood for a feature that reported success while doing nothing:
document conversion that could not find a browser everyone had installed, and
a diagnostics panel that stayed empty on files full of errors.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from navin.documents import _chromium
from navin.lsp.client import diagnostics_key


class ChromiumOnWindowsTest(unittest.TestCase):
    """Chrome is never on PATH on Windows; it lives under Program Files."""

    def setUp(self) -> None:
        self.enterContext(mock.patch.object(_chromium.shutil, "which", return_value=None))
        self.enterContext(mock.patch.object(_chromium.os, "name", "nt"))

    def test_a_chrome_under_program_files_is_found(self) -> None:
        installed = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        with mock.patch.dict(
            _chromium.os.environ, {"ProgramFiles": r"C:\Program Files"}, clear=True
        ):
            self.assertIn(installed, _chromium._windows_install_candidates())

    def test_edge_and_brave_are_searched_too(self) -> None:
        with mock.patch.dict(
            _chromium.os.environ, {"LOCALAPPDATA": r"C:\Users\a\AppData\Local"}, clear=True
        ):
            found = _chromium._windows_install_candidates()
        self.assertTrue(any(path.endswith("msedge.exe") for path in found))
        self.assertTrue(any(path.endswith("brave.exe") for path in found))

    def test_a_trailing_separator_does_not_double_up(self) -> None:
        with mock.patch.dict(
            _chromium.os.environ, {"ProgramFiles": "C:\\Program Files\\"}, clear=True
        ):
            found = _chromium._windows_install_candidates()
        self.assertTrue(all("\\\\" not in path for path in found), found)

    def test_nothing_installed_still_names_the_escape_hatch(self) -> None:
        with mock.patch.dict(_chromium.os.environ, {}, clear=True):
            with mock.patch.object(Path, "exists", lambda self: False):
                with mock.patch.object(Path, "is_dir", lambda self: False):
                    with self.assertRaises(_chromium.ConversionError) as caught:
                        _chromium.find_chromium()
        self.assertIn("NAVIN_CHROMIUM", str(caught.exception))


class ChromiumEverywhereTest(unittest.TestCase):
    def test_an_explicit_path_wins_over_any_search(self) -> None:
        with mock.patch.object(Path, "exists", lambda self: True):
            self.assertEqual(_chromium.find_chromium("/opt/my/chrome"), "/opt/my/chrome")

    def test_a_binary_on_path_is_still_preferred_on_posix(self) -> None:
        with mock.patch.object(_chromium.os, "name", "posix"):
            with mock.patch.object(_chromium.shutil, "which", side_effect=lambda n: "/usr/bin/" + n if n == "chromium" else None):
                with mock.patch.object(Path, "exists", lambda self: True):
                    self.assertEqual(_chromium.find_chromium(), "/usr/bin/chromium")


class DiagnosticsKeyTest(unittest.TestCase):
    """The server republishes its own spelling of the URI we sent."""

    def test_the_same_file_keys_the_same_however_it_is_written(self) -> None:
        self.assertEqual(
            diagnostics_key("file:///home/a/f.py"),
            diagnostics_key("file:///home/a/f.py"),
        )

    def test_percent_encoding_is_undone(self) -> None:
        self.assertEqual(
            diagnostics_key("file:///home/mon%20projet/f.py"),
            diagnostics_key("file:///home/mon projet/f.py"),
        )

    def test_different_files_keep_different_keys(self) -> None:
        self.assertNotEqual(
            diagnostics_key("file:///home/a/f.py"),
            diagnostics_key("file:///home/a/g.py"),
        )

    def test_a_non_file_scheme_is_left_alone(self) -> None:
        self.assertEqual(diagnostics_key("untitled:Untitled-1"), "untitled:Untitled-1")


if __name__ == "__main__":
    unittest.main()
