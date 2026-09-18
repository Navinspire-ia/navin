# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""history.jsonl cursor allocation must be safe across processes.

The threading lock only serialized threads of one MemoryStore. The CLI and
the gateway run as two processes on the same project: both could read the
same tail and emit duplicate cursors (audit L2). An advisory flock on a
dedicated lock file now covers the whole allocation + append.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import navin
from navin.agent.memory import MemoryStore


class CrossProcessCursorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name).resolve()

    @classmethod
    def _python(cls) -> str:
        """A real interpreter with the repo's deps installed.

        sys.executable can be a frozen navin binary ('-c' hits the CLI
        parser) or a bare system python (no loguru). Probe the candidates
        once with the import that MemoryStore needs.
        """
        repo_root = Path(navin.__file__).resolve().parents[1]
        candidates = [
            Path(sys.prefix) / "bin" / "python3",
            repo_root / ".venv" / "bin" / "python",
            Path(sys.executable or ""),
            Path(shutil.which("python3") or ""),
        ]
        probe = "import loguru"
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                check = subprocess.run(
                    [str(candidate), "-c", probe],
                    capture_output=True,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if check.returncode == 0:
                return str(candidate)
        return shutil.which("python3") or "python3"

    def test_two_store_instances_never_emit_duplicate_cursors(self) -> None:
        # Two instances = two threading locks: the in-process case already
        # raced before the flock. Separate open() file descriptions make
        # flock serialize here too, so this is a real regression test.
        stores = [MemoryStore(self.workspace) for _ in range(2)]
        per_store = 15

        def append_burst(store: MemoryStore, tag: str) -> list[int]:
            return [
                store.append_history(f"{tag} {i}", session_key="s-lock")
                for i in range(per_store)
            ]

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(append_burst, store, tag)
                for store, tag in zip(stores, ("a", "b"), strict=True)
            ]
            cursors = [c for f in futures for c in f.result()]

        self.assertEqual(len(cursors), per_store * 2)
        self.assertEqual(len(set(cursors)), per_store * 2)
        # Monotonic and gapless: every allocation lands exactly once.
        self.assertEqual(sorted(cursors), list(range(1, per_store * 2 + 1)))

    def test_separate_processes_allocate_unique_cursors(self) -> None:
        repo_root = Path(navin.__file__).resolve().parents[1]
        script = (
            "import json\n"
            "from pathlib import Path\n"
            "from navin.agent.memory import MemoryStore\n"
            f"workspace = Path({str(self.workspace)!r})\n"
            "store = MemoryStore(workspace)\n"
            "out = [store.append_history(f'proc {i}', session_key='s')"
            " for i in range(8)]\n"
            "print(json.dumps(out))\n"
        )
        env = {
            **os.environ,
            "PYTHONPATH": str(repo_root),
        }
        results: list[int] = []
        for _ in range(3):
            proc = subprocess.run(
                [self._python(), "-c", script],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(repo_root),
                timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            results.extend(json.loads(proc.stdout))
        self.assertEqual(len(results), 24)
        self.assertEqual(sorted(results), list(range(1, 25)))

    def test_lock_file_lives_in_memory_dir(self) -> None:
        store = MemoryStore(self.workspace)
        store.append_history("once", session_key="s")
        self.assertTrue(
            (self.workspace / ".navin" / "memory" / ".history.lock").exists()
        )


if __name__ == "__main__":
    unittest.main()
