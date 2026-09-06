"""User-tool PATH for the packaged desktop app (Linux, Windows, macOS)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.utils import path_env


class PathEnvTest(unittest.TestCase):
    def test_posix_candidates_cover_uvx_and_homebrew(self) -> None:
        home = Path("/Users/me")
        rows = [str(item) for item in path_env._posix_candidates(home)]
        self.assertIn(str(home / ".local" / "bin"), rows)
        self.assertIn("/opt/homebrew/bin", rows)
        self.assertIn("/usr/local/bin", rows)
        self.assertIn("/home/linuxbrew/.linuxbrew/bin", rows)

    def test_windows_candidates_cover_uvx_winget_and_scoop(self) -> None:
        home = Path(r"C:\Users\me")
        env = {
            "APPDATA": r"C:\Users\me\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
            "ProgramFiles": r"C:\Program Files",
        }
        with patch.dict(os.environ, env, clear=False):
            rows = path_env._windows_candidates(home)
        self.assertIn(home / ".local" / "bin", rows)
        self.assertIn(home / "scoop" / "shims", rows)
        self.assertIn(Path(env["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links", rows)
        self.assertIn(Path(env["LOCALAPPDATA"]) / "uv", rows)
        self.assertIn(Path(env["LOCALAPPDATA"]) / "Programs" / "uv", rows)
        self.assertIn(Path(env["APPDATA"]) / "npm", rows)

    def test_augment_appends_existing_user_bins_without_dropping_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            local = home / ".local" / "bin"
            local.mkdir(parents=True)
            with patch.object(path_env.Path, "home", return_value=home):
                with patch.object(path_env.os, "name", "posix"):
                    with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False):
                        path_env.augment_path_for_user_tools()
                        parts = os.environ["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], "/usr/bin")
        self.assertIn(str(local), parts)


if __name__ == "__main__":
    unittest.main()
