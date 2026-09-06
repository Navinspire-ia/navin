"""list_dir must stay fast and must not hide project folders."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.filesystem import ListDirTool


def _run(coro) -> str:
    return str(asyncio.run(coro))


class ListDirTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("x\n", encoding="utf-8")
        noisy = self.root / "node_modules" / "left-pad"
        noisy.mkdir(parents=True)
        (noisy / "index.js").write_text("module.exports=1\n", encoding="utf-8")
        # A tree large enough that a naive rglob would stall the turn.
        junk = self.root / "node_modules" / "junk"
        junk.mkdir()
        for i in range(400):
            (junk / f"f{i}.js").write_text("x\n", encoding="utf-8")

    def _tool(self) -> ListDirTool:
        return ListDirTool(workspace=self.root, allowed_dir=self.root)

    def test_top_level_shows_node_modules(self) -> None:
        out = _run(self._tool().execute(path="."))
        self.assertIn("node_modules", out)
        self.assertIn("src", out)
        self.assertNotIn("left-pad", out)

    def test_recursive_lists_src_but_does_not_walk_node_modules(self) -> None:
        out = _run(self._tool().execute(path=".", recursive=True, max_entries=200))
        self.assertIn("src/", out)
        self.assertIn("src/app.py", out)
        self.assertIn("node_modules/", out)
        self.assertNotIn("left-pad", out)
        self.assertNotIn("f0.js", out)

    def test_listing_inside_node_modules_still_works(self) -> None:
        out = _run(self._tool().execute(path="node_modules"))
        self.assertIn("left-pad", out)
        self.assertIn("junk", out)


if __name__ == "__main__":
    unittest.main()
