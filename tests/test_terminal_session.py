"""Lifecycle of the integrated terminal's shell processes.

Unix sessions are covered by the live PTY path. The ConPTY half is exercised
against a fake winpty on every host, and against a real PowerShell when the
suite runs on Windows with pywinpty installed. The rest pins the platform-
independent edges: a session is closed rather than merely forgotten, the WSL
probe is not run on every keystroke, and the Windows-only helpers are inert
elsewhere.
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from navin.utils import wsl as wsl_module
from navin.utils.proc import kill_windows_process_tree
from navin.webui import fs_browse, terminal_ws
from navin.webui.terminal_ws import (
    TerminalManager,
    UnixTerminalSession,
    WindowsTerminalSession,
    _ensure_hidden_console,
)


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class ManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = TerminalManager()
        self.session = FakeSession()
        self.manager._by_connection["conn"] = {"t1": self.session}  # type: ignore[dict-item]
        self.manager._scrollback[("conn", "t1")] = bytearray(b"hello")

    def test_closing_ends_the_shell_and_forgets_it(self) -> None:
        self.manager.close("conn", "t1")
        self.assertTrue(self.session.closed)
        self.assertIsNone(self.manager.get("conn", "t1"))

    def test_discarding_forgets_without_ending(self) -> None:
        """The distinction that leaked a shell per terminal that ever exited."""
        self.manager.discard("conn", "t1")
        self.assertFalse(self.session.closed)
        self.assertIsNone(self.manager.get("conn", "t1"))

    def test_closing_an_unknown_terminal_is_not_an_error(self) -> None:
        self.manager.close("conn", "nope")
        self.manager.close("other", "t1")

    def test_a_dropped_connection_ends_every_shell_on_it(self) -> None:
        self.manager.cleanup_connection("conn")
        self.assertTrue(self.session.closed)

    def test_scrollback_goes_with_the_session(self) -> None:
        self.manager.close("conn", "t1")
        self.assertEqual(self.manager.scrollback("conn", "t1"), b"")


class PrepareLaunchTest(unittest.TestCase):
    """Which shell a project gets, and where it starts.

    The case that matters is a project inside a WSL distribution, opened from
    Windows: its path is a UNC path, and a Windows shell cannot start there at
    all - PowerShell drops the directory and lands in C:\\Windows, in a shell
    with none of the project's tools on it.
    """

    UBUNTU_PROJECT = r"\\wsl.localhost\Ubuntu\home\aymen\project"

    def setUp(self) -> None:
        self.windows = mock.patch.object(terminal_ws, "_IS_WINDOWS", True)
        self.windows.start()
        self.addCleanup(self.windows.stop)
        self.addCleanup(setattr, wsl_module, "_distros_cache", None)
        wsl_module._distros_cache = ["Ubuntu"]
        wsl_module._distros_cached_at = float("inf")
        self.exe = mock.patch.object(wsl_module, "wsl_executable", return_value="wsl.exe")
        self.exe.start()
        self.addCleanup(self.exe.stop)
        self.home = mock.patch.object(terminal_ws.Path, "home", return_value=Path("C:/Users/me"))
        self.home.start()
        self.addCleanup(self.home.stop)

    def test_a_project_in_a_distribution_gets_that_distributions_shell(self) -> None:
        name, path, args, cwd = terminal_ws.prepare_launch(None, self.UBUNTU_PROJECT)
        self.assertEqual(name, "WSL: Ubuntu")
        self.assertEqual(path, "wsl.exe")
        self.assertEqual(args, ["-d", "Ubuntu", "--cd", "/home/aymen/project"])
        self.assertNotIn("wsl.localhost", cwd)

    def test_the_distribution_name_is_matched_case_insensitively(self) -> None:
        _, _, args, _ = terminal_ws.prepare_launch(None, r"\\wsl$\ubuntu\srv\app")
        self.assertEqual(args, ["-d", "Ubuntu", "--cd", "/srv/app"])

    def test_choosing_that_distribution_by_hand_still_lands_in_the_project(self) -> None:
        with mock.patch.object(
            terminal_ws,
            "available_shells",
            return_value=[
                {"name": "WSL: Ubuntu", "path": "wsl.exe", "kind": "wsl", "args": ["-d", "Ubuntu"]}
            ],
        ):
            _, _, args, _ = terminal_ws.prepare_launch("WSL: Ubuntu", self.UBUNTU_PROJECT)
        self.assertEqual(args, ["-d", "Ubuntu", "--cd", "/home/aymen/project"])

    def test_choosing_a_windows_shell_gets_a_directory_windows_can_use(self) -> None:
        """It cannot reach the project, so it is given somewhere real."""
        with mock.patch.object(
            terminal_ws,
            "available_shells",
            return_value=[
                {"name": "PowerShell", "path": "powershell.exe", "kind": "powershell", "args": []}
            ],
        ):
            name, _, args, cwd = terminal_ws.prepare_launch("PowerShell", self.UBUNTU_PROJECT)
        self.assertEqual(name, "PowerShell")
        self.assertEqual(args, [])
        self.assertNotIn("wsl.localhost", cwd)

    def test_an_ordinary_windows_project_is_untouched(self) -> None:
        with mock.patch.object(
            terminal_ws,
            "available_shells",
            return_value=[
                {
                    "name": "PowerShell",
                    "path": "powershell.exe",
                    "kind": "powershell",
                    "args": [],
                    "default": True,
                }
            ],
        ):
            with mock.patch.object(terminal_ws.Path, "is_dir", return_value=True):
                name, _, args, cwd = terminal_ws.prepare_launch(None, r"C:\src\app")
        self.assertEqual(name, "PowerShell")
        self.assertEqual(args, [])
        self.assertEqual(cwd, r"C:\src\app")

    def test_a_directory_that_no_longer_exists_falls_back_to_home(self) -> None:
        with mock.patch.object(
            terminal_ws,
            "available_shells",
            return_value=[{"name": "sh", "path": "/bin/sh", "args": [], "default": True}],
        ):
            with mock.patch.object(terminal_ws.Path, "is_dir", return_value=False):
                _, _, _, cwd = terminal_ws.prepare_launch(None, "/gone")
        self.assertEqual(cwd, str(Path("C:/Users/me")))

    def test_without_wsl_installed_the_path_is_left_to_the_normal_shell(self) -> None:
        self.exe.stop()
        with mock.patch.object(wsl_module, "wsl_executable", return_value=None):
            with mock.patch.object(
                terminal_ws,
                "available_shells",
                return_value=[
                    {"name": "PowerShell", "path": "powershell.exe", "args": [], "default": True}
                ],
            ):
                name, _, _, _ = terminal_ws.prepare_launch(None, self.UBUNTU_PROJECT)
        self.exe.start()
        self.assertEqual(name, "PowerShell")


class WslRootsTest(unittest.TestCase):
    """Every installed distribution is offered as a place to open a project."""

    def setUp(self) -> None:
        self.addCleanup(setattr, wsl_module, "_distros_cache", None)
        wsl_module._distros_cache = ["Ubuntu", "Debian"]
        wsl_module._distros_cached_at = float("inf")

    def test_windows_offers_each_distribution(self) -> None:
        with mock.patch.object(fs_browse, "host_environment", return_value="windows"):
            with mock.patch.object(fs_browse.Path, "is_dir", return_value=True):
                roots = fs_browse.fs_roots_payload()["roots"]
        labels = [r["label"] for r in roots]
        self.assertIn("WSL: Ubuntu", labels)
        self.assertIn("WSL: Debian", labels)

    def test_a_distribution_root_points_at_a_path_windows_can_open(self) -> None:
        with mock.patch.object(fs_browse, "host_environment", return_value="windows"):
            with mock.patch.object(fs_browse.Path, "is_dir", return_value=True):
                roots = fs_browse.fs_roots_payload()["roots"]
        ubuntu = next(r for r in roots if r["label"] == "WSL: Ubuntu")
        self.assertEqual(ubuntu["path"], r"\\wsl.localhost\Ubuntu\home")
        self.assertEqual(ubuntu["kind"], "wsl")

    def test_a_distribution_that_is_not_running_is_still_offered(self) -> None:
        """Its home cannot be probed until it starts, and opening it starts it."""
        with mock.patch.object(fs_browse, "host_environment", return_value="windows"):
            with mock.patch.object(fs_browse.Path, "is_dir", side_effect=OSError):
                roots = fs_browse.fs_roots_payload()["roots"]
        ubuntu = next(r for r in roots if r["label"] == "WSL: Ubuntu")
        self.assertEqual(ubuntu["path"], r"\\wsl.localhost\Ubuntu")

    def test_other_hosts_are_not_offered_distributions(self) -> None:
        for env in ("linux", "macos", "wsl"):
            with self.subTest(env=env):
                with mock.patch.object(fs_browse, "host_environment", return_value=env):
                    with mock.patch.object(fs_browse.Path, "is_dir", return_value=True):
                        roots = fs_browse.fs_roots_payload()["roots"]
                self.assertFalse([r for r in roots if r["kind"] == "wsl"])


class WindowsHelpersAreInertElsewhereTest(unittest.TestCase):
    def test_the_tree_kill_does_nothing_off_windows(self) -> None:
        with mock.patch("navin.utils.proc.subprocess.run") as run:
            kill_windows_process_tree(4321)
        run.assert_not_called()

    def test_a_missing_pid_is_never_killed(self) -> None:
        with mock.patch("navin.utils.proc.is_windows", return_value=True):
            with mock.patch("navin.utils.proc.subprocess.run") as run:
                kill_windows_process_tree(0)
                kill_windows_process_tree(-1)
        run.assert_not_called()

    def test_the_tree_kill_prefers_the_native_extension(self) -> None:
        core = mock.Mock()
        with mock.patch("navin.utils.native.native", return_value=core):
            with mock.patch("navin.utils.proc.is_windows", return_value=True):
                with mock.patch("navin.utils.proc.subprocess.run") as run:
                    kill_windows_process_tree(4321)
        core.kill_tree.assert_called_once_with(4321, force=True)
        run.assert_not_called()

    def test_the_tree_kill_walks_children_on_windows(self) -> None:
        # Without the native extension the tree kill is taskkill /T.
        with mock.patch("navin.utils.native.native", return_value=None):
            with mock.patch("navin.utils.proc.is_windows", return_value=True):
                with mock.patch("navin.utils.proc.subprocess.run") as run:
                    kill_windows_process_tree(4321)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["taskkill", "/PID"])
        self.assertIn("/T", argv)

    def test_the_tree_kill_falls_back_when_native_fails(self) -> None:
        core = mock.Mock()
        core.kill_tree.side_effect = RuntimeError("no such pid")
        with mock.patch("navin.utils.native.native", return_value=core):
            with mock.patch("navin.utils.proc.is_windows", return_value=True):
                with mock.patch("navin.utils.proc.subprocess.run") as run:
                    kill_windows_process_tree(4321)
        self.assertEqual(run.call_args.args[0][:2], ["taskkill", "/PID"])

    def test_the_tree_kill_survives_taskkill_being_absent(self) -> None:
        with mock.patch("navin.utils.native.native", return_value=None):
            with mock.patch("navin.utils.proc.is_windows", return_value=True):
                with mock.patch("navin.utils.proc.subprocess.run", side_effect=OSError):
                    kill_windows_process_tree(4321)

    def test_the_hidden_console_is_not_allocated_off_windows(self) -> None:
        _ensure_hidden_console()


class FakePtyProcess:
    """In-memory ConPTY stand-in so WindowsTerminalSession runs off Windows."""

    last: FakePtyProcess | None = None

    def __init__(self) -> None:
        self.pid = 4242
        self.exitstatus: int | None = None
        self.written: list[str] = []
        self.argv: list[str] = []
        self.cwd = ""
        self.dimensions: tuple[int, int] | None = None
        self._chunks: list[str] = []
        self._alive = True
        self._cv = __import__("threading").Condition()

    @classmethod
    def spawn(cls, argv, cwd=None, env=None, dimensions=None):
        inst = cls()
        inst.argv = list(argv)
        inst.cwd = cwd or ""
        inst.dimensions = dimensions
        cls.last = inst
        return inst

    def read(self, _n: int) -> str:
        with self._cv:
            if not self._chunks and self._alive:
                self._cv.wait(timeout=0.05)
            if self._chunks:
                return self._chunks.pop(0)
            if not self._alive:
                raise EOFError
            return ""

    def push(self, text: str) -> None:
        with self._cv:
            self._chunks.append(text)
            self._cv.notify_all()

    def write(self, text: str) -> None:
        self.written.append(text)

    def isalive(self) -> bool:
        return self._alive

    def setwinsize(self, rows: int, cols: int) -> None:
        self.dimensions = (rows, cols)

    def terminate(self, force: bool = True) -> None:
        with self._cv:
            self._alive = False
            self.exitstatus = 0
            self._cv.notify_all()


class ConPtySessionTest(unittest.IsolatedAsyncioTestCase):
    """Exercise WindowsTerminalSession without a Windows host."""

    async def asyncSetUp(self) -> None:
        self._module = types.ModuleType("winpty")
        self._module.PtyProcess = FakePtyProcess
        self._previous = sys.modules.get("winpty")
        sys.modules["winpty"] = self._module
        FakePtyProcess.last = None

    async def asyncTearDown(self) -> None:
        if self._previous is None:
            sys.modules.pop("winpty", None)
        else:
            sys.modules["winpty"] = self._previous

    async def test_spawn_write_read_resize_and_close(self) -> None:
        outputs: list[bytes] = []
        exited = asyncio.Event()

        async def on_output(_tid: str, data: bytes) -> None:
            outputs.append(data)

        async def on_exit(_tid: str, _code: int | None) -> None:
            exited.set()

        session = WindowsTerminalSession(
            terminal_id="t1",
            shell_name="powershell",
            shell_path="powershell.exe",
            shell_args=["-NoLogo"],
            cwd="C:/tmp",
            cols=80,
            rows=24,
            on_output=on_output,
            on_exit=on_exit,
        )
        proc = FakePtyProcess.last
        self.assertIsNotNone(proc)
        assert proc is not None
        self.assertEqual(proc.argv[0], "powershell.exe")
        proc.push("ready")
        deadline = time.monotonic() + 2
        while not outputs and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        self.assertIn(b"ready", b"".join(outputs))

        session.write(b"\xc3")
        session.write(b"\xa9")
        self.assertEqual("".join(proc.written), "é")

        session.resize(100, 30)
        self.assertEqual(proc.dimensions, (30, 100))
        session.close()
        deadline = time.monotonic() + 2
        while proc.isalive() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        self.assertFalse(proc.isalive())
        # close() marks the session ended; on_exit is only for a shell that
        # dies on its own, so the reader must not hang after terminate().


@unittest.skipUnless(sys.platform == "win32", "ConPTY requires Windows")
class ConPtyLiveTest(unittest.IsolatedAsyncioTestCase):
    """Real pywinpty + PowerShell, run by the Windows CI / release gates."""

    async def test_powershell_echo_roundtrip(self) -> None:
        try:
            import winpty  # noqa: F401
        except ImportError:
            self.skipTest("pywinpty is not installed")

        import os
        import shutil

        shell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if not shell:
            self.skipTest("PowerShell is not on PATH")

        outputs: list[bytes] = []
        exited = asyncio.Event()

        async def on_output(_tid: str, data: bytes) -> None:
            outputs.append(data)

        async def on_exit(_tid: str, _code: int | None) -> None:
            exited.set()

        session = WindowsTerminalSession(
            terminal_id="live",
            shell_name="powershell",
            shell_path=shell,
            shell_args=["-NoLogo", "-NoProfile"],
            cwd=os.environ.get("TEMP") or os.getcwd(),
            cols=80,
            rows=24,
            on_output=on_output,
            on_exit=on_exit,
        )
        try:
            session.write(b"echo NAVIN_CONPTY_OK\r")
            deadline = time.monotonic() + 8
            blob = b""
            while time.monotonic() < deadline:
                blob = b"".join(outputs)
                if b"NAVIN_CONPTY_OK" in blob:
                    break
                await asyncio.sleep(0.1)
            self.assertIn(b"NAVIN_CONPTY_OK", blob)
        finally:
            session.close()
            try:
                await asyncio.wait_for(exited.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass


@unittest.skipIf(sys.platform == "win32", "Unix PTY")
class UnixCtrlCTest(unittest.IsolatedAsyncioTestCase):
    """Ctrl+C must SIGINT the foreground job, not sit in stdin as a character."""

    async def test_etx_stops_a_sleeping_child(self) -> None:
        import shutil

        sh = shutil.which("sh") or "/bin/sh"
        if not Path(sh).is_file():
            self.skipTest("no sh on this host")

        exited = asyncio.Event()

        async def on_output(_tid: str, _data: bytes) -> None:
            return

        async def on_exit(_tid: str, _code: int | None) -> None:
            exited.set()

        session = UnixTerminalSession(
            terminal_id="sigint",
            shell_name="sh",
            shell_path=sh,
            shell_args=["-c", "sleep 30"],
            cwd=str(Path.home()),
            cols=80,
            rows=24,
            on_output=on_output,
            on_exit=on_exit,
        )
        try:
            await asyncio.sleep(0.2)
            self.assertIsNone(session._process.poll(), "sleep should still be running")
            session.write(b"\x03")
            await asyncio.wait_for(exited.wait(), timeout=4.0)
        finally:
            session.close()

    def test_spawn_installs_a_controlling_tty_hook(self) -> None:
        source = Path(terminal_ws.__file__).read_text(encoding="utf-8")
        self.assertIn("preexec_fn=lambda: _attach_child_tty(slave_fd)", source)
        self.assertIn("os.login_tty", source)
        self.assertIn("TIOCSCTTY", source)


if __name__ == "__main__":
    unittest.main()
