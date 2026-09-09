# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live ExecTool integration for command_output compaction.

These spawn real processes (git, pytest, printf). Skip when a binary or
environment piece is missing - never fail the suite for that.

Policy: skip > fail for tooling gaps. Fail only on compaction regressions.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.shell import ExecTool

WINDOWS = sys.platform == "win32"
_HAS_GIT = shutil.which("git") is not None
_HAS_SH = shutil.which("sh") is not None


def _run(tool: ExecTool, command: str, **kwargs: object) -> str:
    return str(asyncio.run(tool.execute(command=command, **kwargs)))  # type: ignore[arg-type]


class LiveExecCompactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.tool = ExecTool(working_dir=str(self.root))

    def test_plain_echo_still_works(self) -> None:
        out = _run(self.tool, "printf 'hello-navin\\n'")
        self.assertIn("hello-navin", out)
        self.assertIn("Exit code: 0", out)

    @unittest.skipIf(WINDOWS, "POSIX stderr redirect")
    def test_nonzero_exit_preserved(self) -> None:
        if not _HAS_SH:
            self.skipTest("sh not available")
        out = _run(self.tool, "sh -c 'echo boom >&2; exit 7'")
        self.assertIn("Exit code: 7", out)
        self.assertIn("boom", out)

    @unittest.skipUnless(_HAS_GIT, "git not on PATH")
    def test_git_status_via_exec_is_compact_and_useful(self) -> None:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Navin Test",
            "GIT_AUTHOR_EMAIL": "test@navin.local",
            "GIT_COMMITTER_NAME": "Navin Test",
            "GIT_COMMITTER_EMAIL": "test@navin.local",
        }
        try:
            subprocess.run(
                ["git", "init"],
                cwd=self.root,
                check=True,
                capture_output=True,
                env=env,
            )
            (self.root / "tracked.txt").write_text("v1\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "tracked.txt"],
                cwd=self.root,
                check=True,
                capture_output=True,
                env=env,
            )
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=self.root,
                check=True,
                capture_output=True,
                env=env,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            self.skipTest(f"git repo setup failed: {exc}")

        (self.root / "tracked.txt").write_text("v2\n", encoding="utf-8")
        (self.root / "new.txt").write_text("new\n", encoding="utf-8")

        raw = subprocess.run(
            ["git", "status"],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        raw_text = (raw.stdout or "") + (raw.stderr or "")
        if len(raw_text) < 40:
            self.skipTest("git status output unexpectedly tiny")

        out = _run(self.tool, "git status")
        self.assertIn("Exit code: 0", out)
        self.assertIn("tracked.txt", out)
        self.assertIn("new.txt", out)
        self.assertNotIn('use "git add', out)
        body = out.split("Exit code:")[0]
        self.assertLess(len(body), len(raw_text))
        self.assertGreater(len(raw_text) - len(body), 20)

    @unittest.skipUnless(_HAS_GIT, "git not on PATH")
    def test_git_log_via_exec(self) -> None:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Navin Test",
            "GIT_AUTHOR_EMAIL": "test@navin.local",
            "GIT_COMMITTER_NAME": "Navin Test",
            "GIT_COMMITTER_EMAIL": "test@navin.local",
        }
        try:
            subprocess.run(
                ["git", "init"], cwd=self.root, check=True, capture_output=True, env=env
            )
            (self.root / "f.txt").write_text("x\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "f.txt"], cwd=self.root, check=True, capture_output=True, env=env
            )
            subprocess.run(
                ["git", "commit", "-m", "first commit subject"],
                cwd=self.root,
                check=True,
                capture_output=True,
                env=env,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            self.skipTest(f"git repo setup failed: {exc}")

        out = _run(self.tool, "git log -n 1")
        self.assertIn("Exit code: 0", out)
        self.assertIn("first commit subject", out)

    def test_pytest_failures_via_exec(self) -> None:
        try:
            import pytest as _pytest  # noqa: F401
        except ImportError:
            self.skipTest("pytest not importable in this interpreter")

        test_file = self.root / "test_live_fail.py"
        test_file.write_text(
            "def test_ok():\n"
            "    assert True\n"
            "\n"
            "def test_boom():\n"
            "    assert 1 == 2\n",
            encoding="utf-8",
        )
        cmd = f"{sys.executable} -m pytest -q {test_file.name}"
        out = _run(self.tool, cmd)
        if "No module named pytest" in out or "Exit code: 2" in out and "pytest" in out.lower():
            self.skipTest("pytest module missing for exec subprocess")
        self.assertIn("Exit code: 1", out)
        self.assertIn("boom", out.lower())
        self.assertTrue(
            "FAILURES" in out or "FAILED" in out or "assert 1 == 2" in out,
            msg=out[:800],
        )

    @unittest.skipIf(WINDOWS or not _HAS_SH, "POSIX sh required")
    def test_repeated_lines_deduped_via_exec(self) -> None:
        out = _run(
            self.tool,
            "sh -c 'i=0; while [ $i -lt 8 ]; do echo SAME_LINE; i=$((i+1)); done; echo done'",
        )
        self.assertIn("Exit code: 0", out)
        self.assertIn("done", out)
        self.assertTrue(
            "identical lines omitted" in out or out.count("SAME_LINE") <= 2,
            msg=out[:800],
        )

    def test_ruff_via_exec_when_available(self) -> None:
        if shutil.which("ruff") is None:
            self.skipTest("ruff not on PATH")
        bad = self.root / "bad.py"
        bad.write_text("import os\n\nprint('x')\n", encoding="utf-8")
        out = _run(self.tool, f"ruff check {bad.name}")
        if "not found" in out.lower() and "Exit code:" in out and "F401" not in out:
            self.skipTest("ruff not runnable in exec environment")
        self.assertTrue(
            "F401" in out or "imported but unused" in out or "os" in out,
            msg=out[:600],
        )
        self.assertIn("Exit code:", out)

    def test_pip_list_via_exec(self) -> None:
        out = _run(self.tool, f"{sys.executable} -m pip list")
        if "No module named pip" in out:
            self.skipTest("pip not available for this interpreter")
        self.assertIn("Exit code: 0", out)
        self.assertTrue(
            "pytest" in out.lower() or "Package" in out or "pip" in out.lower(),
            msg=out[:500],
        )

    @unittest.skipIf(WINDOWS or not _HAS_SH, "POSIX sh required")
    def test_background_session_compacts_on_done(self) -> None:
        out_bg = _run(
            self.tool,
            (
                "sh -c 'i=0; while [ $i -lt 8 ]; do echo SAME_LINE; "
                "i=$((i+1)); done; echo done'"
            ),
            yield_time_ms=5000,
        )
        self.assertIn("Exit code: 0", out_bg)
        self.assertIn("done", out_bg)
        self.assertTrue(
            "identical lines omitted" in out_bg or out_bg.count("SAME_LINE") <= 2,
            msg=out_bg[:800],
        )


if __name__ == "__main__":
    unittest.main()
