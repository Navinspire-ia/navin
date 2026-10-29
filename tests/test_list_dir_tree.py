# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

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


def test_recursive_listing_runs_off_the_event_loop(tmp_path):
    """A slow disk (\\\\wsl$, /mnt/c) must not freeze the loop every chat shares."""
    import asyncio
    import threading
    import time
    from unittest.mock import patch

    from navin.agent.tools.filesystem import ListDirTool

    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_text("x")
    tool = ListDirTool(workspace=tmp_path, allowed_dir=tmp_path)
    real_walk = __import__("os").walk
    walk_threads: list[str] = []

    def slow_walk(*args, **kwargs):
        walk_threads.append(threading.current_thread().name)
        time.sleep(0.4)
        return real_walk(*args, **kwargs)

    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        with patch("navin.agent.tools.filesystem.os.walk", slow_walk):
            out = await tool.execute(path=".", recursive=True)
        beat.cancel()
        return out, ticks

    out, ticks = asyncio.run(scenario())
    assert "a/f.txt" in str(out)
    assert walk_threads and walk_threads[0] != "MainThread"
    assert ticks >= 4  # the loop kept running while the disk was slow


def test_write_and_edit_do_disk_io_off_the_event_loop(tmp_path):
    """Writes/edits on \\\\wsl$ must not freeze the loop every chat shares."""
    import asyncio
    import threading
    from pathlib import Path
    from unittest.mock import patch

    from navin.agent.tools.filesystem import EditFileTool, WriteFileTool

    threads: list[str] = []
    real_read, real_write = Path.read_bytes, Path.write_bytes

    def spy_read(self):
        threads.append(threading.current_thread().name)
        return real_read(self)

    def spy_write(self, data):
        threads.append(threading.current_thread().name)
        return real_write(self, data)

    async def scenario():
        with patch.object(Path, "read_bytes", spy_read), patch.object(Path, "write_bytes", spy_write):
            await WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path).execute(path="a.txt", content="hello\n")
            await EditFileTool(workspace=tmp_path, allowed_dir=tmp_path).execute(
                path="a.txt", old_text="hello", new_text="bye"
            )

    asyncio.run(scenario())
    assert (tmp_path / "a.txt").read_text() == "bye\n"
    assert threads and "MainThread" not in threads


def test_stale_overwrite_check_and_instructions_run_off_the_event_loop(tmp_path):
    """The overwrite guard hashes the file on disk and read_file looks up nested AGENTS.md."""
    import asyncio
    import threading
    from unittest.mock import patch

    from navin.agent.tools import filesystem
    from navin.agent.tools.filesystem import ReadFileTool, WriteFileTool

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.txt").write_text("hello\n")
    threads: list[str] = []

    def spy_refusal(self, fp):
        threads.append(threading.current_thread().name)
        return None

    def spy_instructions(workspace, target, **kwargs):
        threads.append(threading.current_thread().name)
        return ""

    async def scenario():
        with (
            patch.object(WriteFileTool, "_stale_overwrite_refusal", spy_refusal),
            patch.object(filesystem, "scoped_project_instructions", spy_instructions),
        ):
            await ReadFileTool(workspace=tmp_path, allowed_dir=tmp_path).execute(path="pkg/a.txt")
            await WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path).execute(path="pkg/a.txt", content="bye\n")
            await WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path).execute(
                files=[{"path": "pkg/b.txt", "content": "x\n"}]
            )

    asyncio.run(scenario())
    assert (tmp_path / "pkg" / "a.txt").read_text() == "bye\n"
    assert len(threads) >= 3
    assert "MainThread" not in threads
