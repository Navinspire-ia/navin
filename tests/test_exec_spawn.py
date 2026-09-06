"""The shell the model asks for is the shell the command actually reaches.

The Windows spawn path used to know exactly two shells: cmd got cmd semantics
and everything else got the PowerShell prelude with ``-NoProfile
-NonInteractive -Command``. bash rejected ``-NoProfile`` on sight, wsl.exe
printed its usage text and exited - the allowlist accepted shells the runtime
could not deliver. These tests pin the routing by resolved program name, and
the POSIX side's process-group spawn that lets a timeout kill a whole tree.
"""

from __future__ import annotations

import unittest
import unittest.mock
from typing import Any

import navin.agent.tools.shell as shell_mod
from navin.agent.tools.shell import ExecTool
from navin.utils import wsl


class _Recorder:
    """Stands in for asyncio.create_subprocess_exec/shell and keeps the call."""

    def __init__(self) -> None:
        self.argv: tuple[Any, ...] | None = None
        self.command: str | None = None
        self.kwargs: dict[str, Any] = {}

    async def exec(self, *argv: Any, **kwargs: Any) -> Any:
        self.argv = argv
        self.kwargs = kwargs
        return unittest.mock.MagicMock()

    async def shell(self, command: str, **kwargs: Any) -> Any:
        self.command = command
        self.kwargs = kwargs
        return unittest.mock.MagicMock()


class WindowsRoutingTest(unittest.IsolatedAsyncioTestCase):
    """Each shell name reaches its own calling convention, not PowerShell's."""

    async def _spawn_windows(self, shell_program: str, **kwargs: Any) -> _Recorder:
        recorder = _Recorder()
        with (
            unittest.mock.patch.object(shell_mod, "_IS_WINDOWS", True),
            unittest.mock.patch.object(
                shell_mod.asyncio, "create_subprocess_exec", recorder.exec
            ),
            unittest.mock.patch.object(
                shell_mod.asyncio, "create_subprocess_shell", recorder.shell
            ),
        ):
            await ExecTool._spawn(
                "echo hello", kwargs.pop("cwd", "C:/proj"), {}, shell_program,
                kwargs.pop("login", False),
            )
        return recorder

    async def test_powershell_gets_the_command_prelude(self) -> None:
        recorder = await self._spawn_windows("C:/Windows/pwsh.exe")
        assert recorder.argv is not None
        self.assertIn("-NoProfile", recorder.argv)
        self.assertIn("-NonInteractive", recorder.argv)
        self.assertIn("-Command", recorder.argv)

    async def test_cmd_goes_through_the_shell_with_utf8(self) -> None:
        recorder = await self._spawn_windows("C:/Windows/System32/cmd.exe")
        assert recorder.command is not None
        # OEM codepage output (accents in dir, ipconfig) must come back as
        # UTF-8, not replacement characters.
        self.assertTrue(recorder.command.startswith("chcp 65001>nul & "))
        self.assertIn("echo hello", recorder.command)

    async def test_bash_is_not_handed_powershell_flags(self) -> None:
        recorder = await self._spawn_windows("C:/Git/bin/bash.exe")
        assert recorder.argv is not None
        self.assertNotIn("-NoProfile", recorder.argv)
        self.assertEqual(recorder.argv[-2:], ("-c", "echo hello"))

    async def test_nu_is_not_handed_powershell_flags(self) -> None:
        recorder = await self._spawn_windows("C:/tools/nu.exe")
        assert recorder.argv is not None
        self.assertNotIn("-Command", recorder.argv)
        self.assertEqual(recorder.argv[-2:], ("-c", "echo hello"))

    async def test_wsl_crosses_into_the_distribution(self) -> None:
        recorder = await self._spawn_windows("C:/Windows/System32/wsl.exe", cwd="C:\\proj\\api")
        assert recorder.argv is not None
        self.assertIn("--", recorder.argv)
        self.assertEqual(recorder.argv[-3:], ("bash", "-lc", "echo hello"))
        self.assertNotIn("-NoProfile", recorder.argv)
        # The working directory follows the command to the Linux side.
        self.assertIn("--cd", recorder.argv)
        self.assertIn("/mnt/c/proj/api", recorder.argv)


class PosixSpawnTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_shell_leads_its_own_process_group(self) -> None:
        # Without start_new_session a timeout kills the shell alone, and the
        # dev server it backgrounded keeps the port.
        recorder = _Recorder()
        with (
            unittest.mock.patch.object(shell_mod, "_IS_WINDOWS", False),
            unittest.mock.patch.object(
                shell_mod.asyncio, "create_subprocess_exec", recorder.exec
            ),
        ):
            await ExecTool._spawn("sleep 1", "/tmp", {}, "/bin/bash", False)
        self.assertTrue(recorder.kwargs.get("start_new_session"))

    async def test_login_adds_dash_l_for_bash(self) -> None:
        recorder = _Recorder()
        with (
            unittest.mock.patch.object(shell_mod, "_IS_WINDOWS", False),
            unittest.mock.patch.object(
                shell_mod.asyncio, "create_subprocess_exec", recorder.exec
            ),
        ):
            await ExecTool._spawn("env", "/tmp", {}, "/bin/bash", True)
        assert recorder.argv is not None
        self.assertEqual(recorder.argv[:2], ("/bin/bash", "-l"))


class DriveToMountTest(unittest.TestCase):
    """The path translation wsl.exe --cd relies on."""

    def test_a_drive_path_maps_to_mnt(self) -> None:
        self.assertEqual(wsl.drive_to_mount("C:\\proj\\api"), "/mnt/c/proj/api")

    def test_forward_slashes_work_too(self) -> None:
        self.assertEqual(wsl.drive_to_mount("D:/data"), "/mnt/d/data")

    def test_the_drive_root_alone(self) -> None:
        self.assertEqual(wsl.drive_to_mount("C:\\"), "/mnt/c")

    def test_a_unc_path_is_not_a_drive(self) -> None:
        self.assertIsNone(wsl.drive_to_mount("\\\\wsl.localhost\\Ubuntu\\home"))

    def test_a_relative_path_is_not_a_drive(self) -> None:
        self.assertIsNone(wsl.drive_to_mount("proj/api"))


if __name__ == "__main__":
    unittest.main()
