# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""An isolated terminal is the user's shell inside the agent's OS sandbox.

Same helper and policy as exec (writes confined to the project plus toolchain
caches, reads and network open), inherited by everything typed into the
shell. When the helper cannot run, the terminal opens unconfined and says so
through ``session.sandboxed`` rather than failing to open at all.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.tools import sandbox
from navin.webui import terminal_ws
from navin.webui.terminal_ws import TerminalManager, sandboxed_launch


def _landlock_enforced() -> bool:
    binary = sandbox.native_sandbox_binary()
    if binary is None:
        return False
    with tempfile.TemporaryDirectory() as probe:
        result = subprocess.run(
            [binary, "--strict", "--workspace", probe, "--", "true"],
            capture_output=True,
            timeout=10,
        )
    return result.returncode == 0


class SandboxedLaunchShapeTest(unittest.TestCase):
    def test_the_shell_becomes_the_helpers_command(self) -> None:
        if sandbox.native_sandbox_binary() is None:
            self.skipTest("navin-sandbox binary not built")
        with tempfile.TemporaryDirectory() as ws:
            launch = sandboxed_launch("/bin/bash", ["-il"], workspace=ws, cwd=ws)
        self.assertIsNotNone(launch)
        assert launch is not None
        path, args = launch
        self.assertIn("navin-sandbox", path)
        self.assertEqual(args[0], "--workspace")
        separator = args.index("--")
        self.assertEqual(args[separator + 1 :], ["/bin/bash", "-il"])

    def test_no_helper_means_a_plain_shell(self) -> None:
        with (
            mock.patch.object(sandbox, "native_sandbox_binary", return_value=None),
            mock.patch.object(sandbox, "_compile_native_sandbox", return_value=None),
        ):
            self.assertIsNone(sandboxed_launch("/bin/sh", [], workspace="/tmp", cwd="/tmp"))

    def test_a_crashing_helper_lookup_does_not_block_the_terminal(self) -> None:
        with mock.patch.object(sandbox, "native_sandbox_argv", side_effect=RuntimeError("boom")):
            self.assertIsNone(sandboxed_launch("/bin/sh", [], workspace="/tmp", cwd="/tmp"))


@unittest.skipIf(sys.platform == "win32", "Unix PTY")
class IsolatedTerminalLiveTest(unittest.IsolatedAsyncioTestCase):
    """A real PTY shell inside the sandbox: project writes pass, others fail."""

    async def _open(self, manager: TerminalManager, *, sandbox_flag: bool, ws: str, command: str):
        outputs: list[bytes] = []
        exited = asyncio.Event()

        async def on_output(_tid: str, data: bytes) -> None:
            outputs.append(data)

        async def on_exit(_tid: str, _code: int | None) -> None:
            exited.set()

        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash on this host")
        with mock.patch.object(
            terminal_ws,
            "available_shells",
            return_value=[{"name": "bash", "path": bash, "args": ["-c", command], "default": True}],
        ):
            session = manager.open(
                "conn",
                terminal_id="iso",
                shell="bash",
                cwd=ws,
                cols=80,
                rows=24,
                on_output=on_output,
                on_exit=on_exit,
                sandbox=sandbox_flag,
                workspace=ws,
            )
        try:
            await asyncio.wait_for(exited.wait(), timeout=20.0)
        finally:
            manager.close("conn", "iso")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and b"DONE" not in b"".join(outputs):
            await asyncio.sleep(0.05)
        return session, b"".join(outputs).decode("utf-8", errors="replace")

    async def test_the_isolated_shell_is_confined_to_the_project(self) -> None:
        if not _landlock_enforced():
            self.skipTest("navin-sandbox binary missing or kernel does not enforce Landlock")
        with tempfile.TemporaryDirectory() as ws:
            session, text = await self._open(
                TerminalManager(),
                sandbox_flag=True,
                ws=ws,
                command=(
                    "echo x > /etc/navin-isolated-terminal-probe 2>&1 || echo OUTSIDE_DENIED; "
                    "touch inside-ok && echo INSIDE_OK; echo DONE"
                ),
            )
            self.assertTrue(session.sandboxed)
            self.assertIn("OUTSIDE_DENIED", text)
            self.assertIn("INSIDE_OK", text)
            self.assertTrue((Path(ws) / "inside-ok").exists())
        self.assertFalse(Path("/etc/navin-isolated-terminal-probe").exists())

    async def test_without_the_helper_the_shell_opens_unconfined_and_says_so(self) -> None:
        with (
            mock.patch.object(sandbox, "native_sandbox_binary", return_value=None),
            mock.patch.object(sandbox, "_compile_native_sandbox", return_value=None),
            tempfile.TemporaryDirectory() as ws,
        ):
            session, text = await self._open(
                TerminalManager(), sandbox_flag=True, ws=ws, command="echo PLAIN; echo DONE"
            )
            self.assertFalse(session.sandboxed)
            self.assertIn("PLAIN", text)

    async def test_a_normal_terminal_is_never_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            session, text = await self._open(
                TerminalManager(), sandbox_flag=False, ws=ws, command="echo PLAIN; echo DONE"
            )
            self.assertFalse(session.sandboxed)
            self.assertIn("PLAIN", text)


if __name__ == "__main__":
    unittest.main()
