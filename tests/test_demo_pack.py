"""Bundled demo workspace for first-run setup."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.demo.pack import demo_root, ensure_demo_workspace, list_demo_files


class DemoPackTest(unittest.TestCase):
    def test_packaged_demo_contains_risklens_and_vision_mini(self):
        root = demo_root()
        self.assertTrue(root.is_dir(), f"missing demo root {root}")
        files = set(list_demo_files())
        self.assertIn("risklens-brief.md", files)
        self.assertIn("vision360-mini/index.html", files)
        self.assertIn("vision360-mini/about.html", files)

    def test_ensure_demo_workspace_copies_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "demo"
            out = ensure_demo_workspace(dest)
            self.assertEqual(out, dest)
            self.assertTrue((dest / "risklens-brief.md").is_file())
            self.assertTrue((dest / "vision360-mini" / "index.html").is_file())
            # Idempotent second call.
            ensure_demo_workspace(dest)
            self.assertTrue((dest / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
