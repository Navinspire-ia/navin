"""Interactive PTY terminals streamed over the WebUI WebSocket.

Each WebSocket connection can open a handful of shell sessions (bash, zsh,
fish, sh, PowerShell Core, ... — whatever is installed on the host). Output is
pushed to the client as base64 frames; input, resize, and close travel the
other way. Sessions die with their connection.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

MAX_TERMINALS_PER_CONNECTION = 8
_READ_CHUNK = 65536
_DEFAULT_COLS = 80
_DEFAULT_ROWS = 24

_IS_WINDOWS = sys.platform == "win32"


class TerminalError(Exception):
    pass


def _is_wsl() -> bool:
    """True when this Linux process runs inside WSL (Windows interop available)."""
    if _IS_WINDOWS:
        return False
    try:
        with open("/proc/version", encoding="utf-8", errors="ignore") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _wsl_distros() -> list[str]:
    """List installed WSL distributions (Windows host only)."""
    wsl = shutil.which("wsl") or shutil.which("wsl.exe")
    if not wsl:
        return []
    try:
        # UTF-16LE output on Windows; -q lists names only.
        out = subprocess.run(  # noqa: S603
            [wsl, "-l", "-q"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    raw = out.stdout
    for encoding in ("utf-16-le", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            text = ""
    names = [line.strip().strip("\x00") for line in text.splitlines()]
    return [n for n in names if n]


def available_shells() -> list[dict[str, Any]]:
    """Detect shells on the host, VSCode/Cursor style.

    - Windows host: PowerShell, pwsh, cmd, and one entry per installed WSL distro.
    - Linux/macOS host: the user's login shell plus bash/zsh/fish/sh/pwsh.
    - WSL guest: native Unix shells plus Windows interop shells (powershell.exe,
      cmd.exe) reachable over the /mnt/c PATH, so you can drive Windows too.
    """

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(
        name: str,
        path: str | None,
        *,
        default: bool = False,
        kind: str = "shell",
        args: list[str] | None = None,
    ) -> None:
        if not path or name in seen:
            return
        seen.add(name)
        rows.append(
            {"name": name, "path": path, "default": default, "kind": kind, "args": args or []}
        )

    if _IS_WINDOWS:
        add("PowerShell", shutil.which("powershell"), default=True, kind="powershell")
        add("pwsh", shutil.which("pwsh"), kind="powershell")
        add("cmd", shutil.which("cmd"), kind="cmd")
        wsl = shutil.which("wsl") or shutil.which("wsl.exe")
        distros = _wsl_distros()
        if wsl and distros:
            for distro in distros:
                add(f"WSL: {distro}", wsl, kind="wsl", args=["-d", distro])
        elif wsl:
            add("WSL", wsl, kind="wsl")
        return rows

    default_shell = (os.environ.get("SHELL") or "").strip()
    if default_shell and Path(default_shell).is_file():
        add(Path(default_shell).name, default_shell, default=True)
    for name in ("bash", "zsh", "fish", "sh", "pwsh"):
        add(name, shutil.which(name))
    if not rows:
        add("sh", "/bin/sh", default=True)

    # WSL guest: expose Windows interop shells so the user can run Windows
    # commands the same way VSCode/Cursor does from a WSL workspace.
    if _is_wsl():
        add("PowerShell (Windows)", shutil.which("powershell.exe"), kind="powershell")
        add("pwsh (Windows)", shutil.which("pwsh.exe"), kind="powershell")
        add("cmd (Windows)", shutil.which("cmd.exe"), kind="cmd")
    return rows


def _resolve_shell(requested: str | None) -> tuple[str, str, list[str]]:
    """Return (name, path, args) for the requested shell name, or the default."""

    shells = available_shells()
    if not shells:
        raise TerminalError("no shell available on this host")
    wanted = (requested or "").strip().lower()
    if wanted:
        for row in shells:
            if row["name"].lower() == wanted:
                return row["name"], row["path"], list(row.get("args") or [])
        raise TerminalError(f"shell not available: {requested}")
    for row in shells:
        if row.get("default"):
            return row["name"], row["path"], list(row.get("args") or [])
    first = shells[0]
    return first["name"], first["path"], list(first.get("args") or [])


class TerminalSession:
    """One PTY-backed shell process bound to a WebSocket connection."""

    def __init__(
        self,
        *,
        terminal_id: str,
        shell_name: str,
        shell_path: str,
        shell_args: list[str] | None = None,
        cwd: str,
        cols: int,
        rows: int,
        on_output: Callable[[str, bytes], Awaitable[None]],
        on_exit: Callable[[str, int | None], Awaitable[None]],
    ) -> None:
        if _IS_WINDOWS:
            raise TerminalError("interactive terminals require a Unix host")

        import pty

        self.terminal_id = terminal_id
        self.shell_name = shell_name
        self._on_output = on_output
        self._on_exit = on_exit
        self._loop = asyncio.get_running_loop()
        self._closed = False

        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        try:
            env = dict(os.environ)
            env.setdefault("TERM", "xterm-256color")
            env["COLORTERM"] = "truecolor"
            self._process = subprocess.Popen(  # noqa: S603
                [shell_path, *(shell_args or [])],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as e:
            os.close(master_fd)
            os.close(slave_fd)
            raise TerminalError(f"failed to start shell: {e}") from e
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass

        self.resize(cols, rows)
        os.set_blocking(master_fd, False)
        self._loop.add_reader(master_fd, self._on_readable)

    def _on_readable(self) -> None:
        try:
            data = os.read(self._master_fd, _READ_CHUNK)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            self._loop.create_task(self._on_output(self.terminal_id, data))
            return
        # EOF: the shell exited.
        exit_code = self._teardown()
        self._loop.create_task(self._on_exit(self.terminal_id, exit_code))

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        try:
            os.write(self._master_fd, data)
        except OSError:
            pass

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        import fcntl
        import struct
        import termios

        cols = max(2, min(int(cols or _DEFAULT_COLS), 500))
        rows = max(2, min(int(rows or _DEFAULT_ROWS), 300))
        try:
            fcntl.ioctl(
                self._master_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0),
            )
        except OSError:
            pass

    def _teardown(self) -> int | None:
        if self._closed:
            return self._process.poll()
        self._closed = True
        try:
            self._loop.remove_reader(self._master_fd)
        except (ValueError, OSError):
            pass
        try:
            os.close(self._master_fd)
        except OSError:
            pass
        if self._process.poll() is None:
            try:
                os.killpg(self._process.pid, signal.SIGHUP)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        return self._process.poll()

    def close(self) -> None:
        self._teardown()
        if self._process.poll() is None:
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass


class TerminalManager:
    """Terminal sessions grouped per WebSocket connection."""

    def __init__(self) -> None:
        self._by_connection: dict[Any, dict[str, TerminalSession]] = {}

    def open(
        self,
        connection: Any,
        *,
        terminal_id: str,
        shell: str | None,
        cwd: str,
        cols: int,
        rows: int,
        on_output: Callable[[str, bytes], Awaitable[None]],
        on_exit: Callable[[str, int | None], Awaitable[None]],
    ) -> TerminalSession:
        sessions = self._by_connection.setdefault(connection, {})
        if terminal_id in sessions:
            raise TerminalError("terminal already open")
        if len(sessions) >= MAX_TERMINALS_PER_CONNECTION:
            raise TerminalError("too many terminals open")
        shell_name, shell_path, shell_args = _resolve_shell(shell)
        # WSL interop shells and /mnt paths need a working directory the target
        # can reach; fall back to home when cwd is not valid for that shell.
        if not Path(cwd).is_dir():
            cwd = str(Path.home())
        session = TerminalSession(
            terminal_id=terminal_id,
            shell_name=shell_name,
            shell_path=shell_path,
            shell_args=shell_args,
            cwd=cwd,
            cols=cols,
            rows=rows,
            on_output=on_output,
            on_exit=on_exit,
        )
        sessions[terminal_id] = session
        return session

    def get(self, connection: Any, terminal_id: str) -> TerminalSession | None:
        return self._by_connection.get(connection, {}).get(terminal_id)

    def discard(self, connection: Any, terminal_id: str) -> None:
        sessions = self._by_connection.get(connection)
        if not sessions:
            return
        sessions.pop(terminal_id, None)
        if not sessions:
            self._by_connection.pop(connection, None)

    def close(self, connection: Any, terminal_id: str) -> None:
        session = self.get(connection, terminal_id)
        if session is not None:
            session.close()
        self.discard(connection, terminal_id)

    def cleanup_connection(self, connection: Any) -> None:
        sessions = self._by_connection.pop(connection, {})
        for session in sessions.values():
            session.close()


def encode_output(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode_input(data: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except ValueError as e:
        raise TerminalError("invalid terminal input encoding") from e
