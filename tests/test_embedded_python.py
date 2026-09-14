# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Scripts piped through the packaged Python shim behave like Python stdin."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from typer.testing import CliRunner

from navin.cli.commands import app
from navin.python_runtime import _shim_body


class StdinScriptTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX heredoc syntax")
    def test_shell_heredoc_through_the_packaged_shim(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            shim = Path(directory) / "python"
            shim.write_text(_shim_body([sys.executable, "-m", "navin", "python"]))
            shim.chmod(0o755)
            result = subprocess.run(
                ["/bin/sh", "-c", "python - <<'EOF'\nprint('heredoc', sum(range(5)))\nEOF"],
                cwd=root,
                env={**os.environ, "PATH": directory + os.pathsep + os.environ.get("PATH", ""), "PYTHONPATH": str(root)},
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("heredoc 10", result.stdout)

    def test_stdin_script_with_arguments_and_main_globals(self):
        original_argv, original_path = sys.argv[:], sys.path[:]
        result = CliRunner().invoke(app, ["python", "-", "argument"], input=(
            "import sys\n"
            "print(__name__, __file__, __package__, __spec__)\n"
            "print(repr(sys.argv), repr(sys.path[0]))\n"
            "print(sum(range(5)))\n"
        ))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("__main__ <stdin> None None", result.output)
        self.assertIn("['-', 'argument'] ''", result.output)
        self.assertIn("10", result.output)
        self.assertEqual(sys.argv, original_argv)
        self.assertEqual(sys.path, original_path)

    def test_stdin_script_preserves_encoding_cookie(self):
        result = CliRunner().invoke(app, ["python", "-"], input=(
            b"# coding: latin-1\nprint('caf\xe9')\n"
        ))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("café", result.output)

    def test_stdin_script_preserves_exit_status(self):
        result = CliRunner().invoke(app, ["python", "-"], input="raise SystemExit(7)\n")
        self.assertEqual(result.exit_code, 7)

    def test_empty_stdin_is_a_valid_script(self):
        result = CliRunner().invoke(app, ["python", "-"], input="")
        self.assertEqual(result.exit_code, 0, result.output)

    def test_code_and_missing_script_still_behave_as_before(self):
        runner = CliRunner()
        result = runner.invoke(app, ["python", "-c", "print(6 * 7)"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("42", result.output)
        result = runner.invoke(app, ["python", "does-not-exist.py"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("No such file", result.output)
