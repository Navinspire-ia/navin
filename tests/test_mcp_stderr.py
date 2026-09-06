"""An MCP server's diagnostics must arrive under that server's name.

The MCP SDK hands the child navin's own stderr descriptor, so a stale AWS token
inside one connector printed a bare "Token has expired" line into the gateway
terminal, with nothing to say which of the configured servers produced it. These
cover the pipe that puts a name in front of it, and the teardown, because a
reader thread that never sees EOF would hang every shutdown.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from loguru import logger

from navin.agent.tools.mcp import _STDERR_LINE_LIMIT, _server_stderr


class Captured:
    """Collects formatted log messages for the duration of a block."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._sink_id: int | None = None

    def __enter__(self) -> "Captured":
        self._sink_id = logger.add(self._write, format="{message}", level="DEBUG")
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._sink_id is not None:
            logger.remove(self._sink_id)

    def _write(self, message: object) -> None:
        self.lines.append(str(message).rstrip("\n"))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def run_child(name: str, script: str) -> str:
    """Run a child whose stderr goes through the named server's pipe."""
    with Captured() as captured, _server_stderr(name) as errlog:
        subprocess.run([sys.executable, "-c", script], stderr=errlog, check=False)
    return captured.text


class AttributionTest(unittest.TestCase):
    def test_a_child_s_stderr_is_logged_under_the_server_name(self) -> None:
        output = run_child(
            "aws-api",
            "import sys; sys.stderr.write('Token has expired and refresh failed\\n')",
        )
        self.assertIn("MCP server 'aws-api': Token has expired and refresh failed", output)

    def test_each_line_is_reported_separately(self) -> None:
        output = run_child(
            "kubernetes",
            "import sys; sys.stderr.write('first\\nsecond\\n')",
        )
        self.assertIn("MCP server 'kubernetes': first", output)
        self.assertIn("MCP server 'kubernetes': second", output)

    def test_blank_lines_are_dropped(self) -> None:
        # Banners are mostly padding; logging it verbatim would bury the one
        # line that matters.
        output = run_child("noisy", "import sys; sys.stderr.write('\\n\\n  \\n')")
        self.assertEqual(output, "")

    def test_a_runaway_line_is_truncated(self) -> None:
        output = run_child(
            "flood",
            f"import sys; sys.stderr.write('x' * {_STDERR_LINE_LIMIT * 3} + '\\n')",
        )
        self.assertIn("MCP server 'flood': " + "x" * _STDERR_LINE_LIMIT, output)
        self.assertNotIn("x" * (_STDERR_LINE_LIMIT + 1), output)

    def test_stdout_is_left_alone(self) -> None:
        # stdout carries JSON-RPC. Touching it would break the protocol.
        with Captured() as captured, _server_stderr("quiet") as errlog:
            result = subprocess.run(
                [sys.executable, "-c", "print('{\"jsonrpc\": \"2.0\"}')"],
                stderr=errlog,
                capture_output=False,
                stdout=subprocess.PIPE,
                text=True,
                check=False,
            )
        self.assertIn('"jsonrpc"', result.stdout)
        self.assertEqual(captured.text, "")


class TeardownTest(unittest.TestCase):
    def test_the_context_exits_once_the_child_is_gone(self) -> None:
        # The reader only sees EOF after the parent drops its own copy of the
        # write end, so this would hang if the context manager forgot to close.
        with _server_stderr("short-lived") as errlog:
            subprocess.run([sys.executable, "-c", "pass"], stderr=errlog, check=False)

    def test_the_sink_survives_a_server_that_writes_nothing(self) -> None:
        with _server_stderr("silent") as errlog:
            self.assertFalse(errlog.closed)
        self.assertTrue(errlog.closed)


if __name__ == "__main__":
    unittest.main()
