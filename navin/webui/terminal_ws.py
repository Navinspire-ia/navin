"""Interactive PTY terminals streamed over the WebUI WebSocket.

Each WebSocket connection can open a handful of shell sessions (bash, zsh,
fish, sh, PowerShell, cmd, WSL, ... - whatever is installed on the host).
Output is pushed to the client as base64 frames; input, resize, and close
travel the other way. Sessions die with their connection.

Unix hosts use the stdlib ``pty`` module; Windows hosts use ConPTY through
``pywinpty`` (the same underlying mechanism as VSCode's integrated terminal).
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from navin.process_runtime import child_environment
from navin.utils import wsl
from navin.utils.proc import kill_windows_process_tree

MAX_TERMINALS_PER_CONNECTION = 8
# Bytes of output kept per session so a re-attached client (terminal panel
# closed and reopened) can replay what the shell printed while detached.
MAX_SCROLLBACK_BYTES = 200_000
_READ_CHUNK = 65536
_DEFAULT_COLS = 80
_DEFAULT_ROWS = 24

_IS_WINDOWS = sys.platform == "win32"

_SW_HIDE = 0


class TerminalError(Exception):
    pass


def _ensure_hidden_console() -> None:
    """Give this process one hidden console, once, on Windows.

    The desktop build is windowed, so it has no console. ConPTY needs one, and
    when it finds none it allocates a console per session and hides it a moment
    later - the user sees a window flash each time a terminal opens, and once
    more each time one is re-opened. Allocating a single hidden console up front
    means ConPTY finds it already there and never allocates another.

    Best-effort by design: if any of it fails the terminal still works, it just
    flashes, and that is not worth refusing to open a shell over.
    """
    if not _IS_WINDOWS:
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if kernel32.GetConsoleWindow() != 0:
            return
        if not kernel32.AllocConsole():
            return
        window = kernel32.GetConsoleWindow()
        if window:
            ctypes.windll.user32.ShowWindow(window, _SW_HIDE)  # type: ignore[attr-defined]
    except Exception:
        pass




# shutil.which walks PATH; in a WSL guest that PATH carries every Windows
# directory over the /mnt/c 9P mount, where one stat can take a second when
# Windows is busy. The shell list froze the gateway loop for 13 s. Shells do not
# appear mid-session: keep the answer and recompute off the loop.
_SHELLS_CACHE_TTL_S = 120.0
_shells_cache: tuple[float, list[dict[str, Any]]] | None = None
_shells_lock = threading.Lock()


def available_shells(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Detect shells on the host, VSCode/Cursor style (cached, see above).

    - Windows host: PowerShell, pwsh, cmd, and one entry per installed WSL distro.
    - Linux/macOS host: the user's login shell plus bash/zsh/fish/sh/pwsh.
    - WSL guest: native Unix shells plus Windows interop shells (powershell.exe,
      cmd.exe) reachable over the /mnt/c PATH, so you can drive Windows too.
    """

    global _shells_cache
    with _shells_lock:
        cached = _shells_cache
        if (
            not refresh
            and cached is not None
            and time.monotonic() - cached[0] < _SHELLS_CACHE_TTL_S
        ):
            return [dict(row) for row in cached[1]]
        rows = _detect_shells()
        _shells_cache = (time.monotonic(), rows)
        return [dict(row) for row in rows]


def _detect_shells() -> list[dict[str, Any]]:
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
        executable = wsl.wsl_executable()
        distros = wsl.distributions()
        if executable and distros:
            for distro in distros:
                add(f"WSL: {distro}", executable, kind="wsl", args=["-d", distro])
        elif executable:
            add("WSL", executable, kind="wsl")
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
    if wsl.is_wsl_guest():
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


def prepare_launch(requested: str | None, cwd: str) -> tuple[str, str, list[str], str]:
    """Pick the shell for this project, and the directory it starts in.

    A project inside a WSL distribution is reached from Windows through a
    ``\\\\wsl.localhost\\...`` path, and no Windows shell can start there:
    PowerShell refuses a UNC working directory and lands the user in
    ``C:\\Windows`` instead, in a shell with none of the project's tools. The
    shell that can start there is the distribution's own, so that is what such a
    project gets by default, with the path translated back to the name the
    distribution knows it by.

    Choosing another shell from the menu still works; it just cannot honour the
    directory, so it is given the user's home rather than a path Windows will
    silently replace.
    """
    location = wsl.parse_unc(cwd) if _IS_WINDOWS else None

    if location is not None and not (requested or "").strip():
        executable = wsl.wsl_executable()
        if executable:
            distro = wsl.resolve_distro(location.distro) or location.distro
            return (
                f"WSL: {distro}",
                executable,
                ["-d", distro, "--cd", location.posix],
                str(Path.home()),
            )

    shell_name, shell_path, shell_args = _resolve_shell(requested)

    if location is not None:
        distro = wsl.resolve_distro(location.distro) or location.distro
        # An explicitly chosen WSL shell can still land in the project, as long
        # as it is the distribution the project lives in.
        if shell_args[:2] == ["-d", distro] and "--cd" not in shell_args:
            shell_args = [*shell_args, "--cd", location.posix]
        return shell_name, shell_path, shell_args, str(Path.home())

    if not Path(cwd).is_dir():
        cwd = str(Path.home())
    return shell_name, shell_path, shell_args, cwd


def _clamp_size(cols: int, rows: int) -> tuple[int, int]:
    cols = max(2, min(int(cols or _DEFAULT_COLS), 500))
    rows = max(2, min(int(rows or _DEFAULT_ROWS), 300))
    return cols, rows


def _attach_child_tty(slave_fd: int) -> None:
    """Make this PTY the child's controlling terminal.

    ``start_new_session`` only creates a session. Without a controlling tty the
    kernel will not turn Ctrl+C / Ctrl+Z into SIGINT / SIGTSTP, so a ``sleep``
    or a node server keeps running while the bytes sit in stdin as literals.
    ``os.login_tty`` (setsid + TIOCSCTTY + dup onto 0/1/2) is the usual fix;
    the ioctl path covers hosts where login_tty is missing or already a leader.
    """
    try:
        os.login_tty(slave_fd)
        return
    except (AttributeError, OSError):
        pass
    try:
        os.setsid()
    except OSError:
        pass
    try:
        import fcntl
        import termios

        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
    except (OSError, AttributeError):
        pass
    for fd in (0, 1, 2):
        if fd == slave_fd:
            continue
        try:
            os.dup2(slave_fd, fd)
        except OSError:
            pass


class UnixTerminalSession:
    """One PTY-backed shell process bound to a WebSocket connection."""

    # True when the shell was started inside the OS sandbox (isolated terminal).
    sandboxed: bool = False

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
            # Sanitised, not raw: a packaged build points the loader at its own
            # unpacked copies of libssl and libstdc++, and the user's shell must
            # not inherit that - every git or node they run in this tab would
            # load the bundle's libraries instead of the system ones.
            env = child_environment()
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
                preexec_fn=lambda: _attach_child_tty(slave_fd),
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

        cols, rows = _clamp_size(cols, rows)
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


class WindowsTerminalSession:
    """One ConPTY-backed shell process (Windows host), via pywinpty.

    pywinpty exposes a blocking, text-mode pipe, so a daemon thread pumps
    output back onto the event loop with run_coroutine_threadsafe.
    """

    # Windows has no OS sandbox yet; an isolated terminal request opens a
    # normal shell and the client is told so.
    sandboxed: bool = False

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
        try:
            from winpty import PtyProcess
        except ImportError as e:
            raise TerminalError(
                "interactive terminals require the pywinpty package on Windows"
            ) from e

        import codecs
        import threading

        self.terminal_id = terminal_id
        self.shell_name = shell_name
        self._on_output = on_output
        self._on_exit = on_exit
        self._loop = asyncio.get_running_loop()
        self._closed = False
        # WebSocket input arrives as raw bytes; ConPTY wants text. Keystrokes
        # can split multi-byte UTF-8 sequences across frames, hence incremental.
        self._utf8_input = codecs.getincrementaldecoder("utf-8")(errors="replace")

        _ensure_hidden_console()
        cols, rows = _clamp_size(cols, rows)
        env = {str(k): str(v) for k, v in child_environment().items()}
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")
        try:
            self._process = PtyProcess.spawn(
                [shell_path, *(shell_args or [])],
                cwd=cwd,
                env=env,
                dimensions=(rows, cols),
            )
        except Exception as e:
            logger.error(
                "terminal spawn failed: shell={} args={} cwd={} env_keys={} error={}",
                shell_path,
                shell_args,
                cwd,
                len(env),
                e,
            )
            raise TerminalError(f"failed to start shell: {e}") from e
        logger.debug(
            "terminal {} started: shell={} args={} cwd={}",
            terminal_id,
            shell_path,
            shell_args,
            cwd,
        )

        self._reader = threading.Thread(
            target=self._read_loop,
            name=f"navin-terminal-{terminal_id}",
            daemon=True,
        )
        self._reader.start()

    def _dispatch(self, coro: Awaitable[None]) -> None:
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]
        except RuntimeError:
            # Event loop already gone (server shutdown).
            pass

    def _read_loop(self) -> None:
        proc = self._process
        produced = 0
        started = time.monotonic()
        while not self._closed:
            try:
                chunk = proc.read(_READ_CHUNK)
            except (EOFError, ConnectionError, OSError):
                break
            except Exception:
                # pywinpty raises its own WinptyError, which is not an OSError.
                # Letting it escape kills this thread with only a traceback on a
                # stderr nobody reads in a windowed build, and the tab just says
                # the process exited with no reason given anywhere.
                logger.exception("terminal {} read failed", self.terminal_id)
                break
            if not chunk:
                if not proc.isalive():
                    break
                continue
            produced += len(chunk)
            self._dispatch(
                self._on_output(self.terminal_id, chunk.encode("utf-8", errors="replace"))
            )
        # A shell that dies before printing anything is a broken terminal, not a
        # session the user ended, and the two are indistinguishable in the UI.
        if not self._closed and produced == 0:
            logger.warning(
                "terminal {} ({}) produced no output before exiting after {:.1f}s; "
                "exitstatus={}",
                self.terminal_id,
                self.shell_name,
                time.monotonic() - started,
                getattr(proc, "exitstatus", None),
            )
        exit_code: int | None = None
        alive = True
        try:
            alive = proc.isalive()
            if not alive:
                exit_code = proc.exitstatus
        except Exception:
            alive = False
        # The loop also ends on a read error, and then the shell is still
        # running with nobody reading it. Leaving it there is what accumulated
        # orphaned shells - one per reconnect - each holding a console.
        if alive:
            self._kill_tree()
        if not self._closed:
            self._closed = True
            self._dispatch(self._on_exit(self.terminal_id, exit_code))

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        text = self._utf8_input.decode(data)
        if not text:
            return
        try:
            self._process.write(text)
        except (EOFError, ConnectionError, OSError):
            pass

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        cols, rows = _clamp_size(cols, rows)
        try:
            self._process.setwinsize(rows, cols)
        except Exception:
            pass

    def _kill_tree(self) -> None:
        """End the shell and whatever it started."""
        try:
            pid = int(getattr(self._process, "pid", 0) or 0)
        except (TypeError, ValueError):
            pid = 0
        try:
            self._process.terminate(force=True)
        except Exception:
            pass
        # terminate() ends the shell alone. Anything it launched - a dev server,
        # a watcher - outlives it and keeps its ports.
        kill_windows_process_tree(pid)

    def close(self) -> None:
        self._closed = True
        self._kill_tree()


TerminalSession = UnixTerminalSession | WindowsTerminalSession


def _create_session(**kwargs: Any) -> TerminalSession:
    cls = WindowsTerminalSession if _IS_WINDOWS else UnixTerminalSession
    return cls(**kwargs)


def sandboxed_launch(
    shell_path: str,
    shell_args: list[str],
    *,
    workspace: str,
    cwd: str,
) -> tuple[str, list[str]] | None:
    """Prefix an interactive shell with the OS sandbox the exec tool uses.

    Landlock (Linux/WSL) and Seatbelt (macOS) rules are inherited by every
    child, so a shell started this way confines everything the user types in
    it: writes stay inside the project plus the toolchain caches, reads and
    the network stay open. Same helper, same policy as agent commands, which
    is the point - an "isolated terminal" is not a different sandbox to learn.
    ``None`` when the helper is missing or cannot run on this host; the caller
    opens a normal shell and tells the client so the tab does not lie.
    """
    from navin.agent.tools.sandbox import native_sandbox_argv

    try:
        prefix = native_sandbox_argv(workspace, cwd)
    except Exception as exc:  # noqa: BLE001 - a broken helper must not block the terminal
        logger.warning("isolated terminal: sandbox helper unavailable ({}); opening unconfined", exc)
        return None
    if not prefix:
        return None
    return prefix[0], [*prefix[1:], "--", shell_path, *shell_args]


class TerminalManager:
    """Terminal sessions grouped per WebSocket connection."""

    def __init__(self) -> None:
        self._by_connection: dict[Any, dict[str, TerminalSession]] = {}
        self._scrollback: dict[tuple[Any, str], bytearray] = {}

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
        sandbox: bool = False,
        workspace: str | None = None,
    ) -> TerminalSession:
        sessions = self._by_connection.setdefault(connection, {})
        if terminal_id in sessions:
            raise TerminalError("terminal already open")
        if len(sessions) >= MAX_TERMINALS_PER_CONNECTION:
            raise TerminalError("too many terminals open")
        shell_name, shell_path, shell_args, cwd = prepare_launch(shell, cwd)

        sandboxed = False
        if sandbox and not _IS_WINDOWS:
            launch = sandboxed_launch(
                shell_path, shell_args, workspace=workspace or cwd, cwd=cwd
            )
            if launch is not None:
                shell_path, shell_args = launch
                sandboxed = True

        buffer = bytearray()
        self._scrollback[(connection, terminal_id)] = buffer

        async def buffered_output(tid: str, data: bytes) -> None:
            buffer.extend(data)
            if len(buffer) > MAX_SCROLLBACK_BYTES:
                del buffer[: len(buffer) - MAX_SCROLLBACK_BYTES]
            await on_output(tid, data)

        session = _create_session(
            terminal_id=terminal_id,
            shell_name=shell_name,
            shell_path=shell_path,
            shell_args=shell_args,
            cwd=cwd,
            cols=cols,
            rows=rows,
            on_output=buffered_output,
            on_exit=on_exit,
        )
        session.sandboxed = sandboxed
        sessions[terminal_id] = session
        return session

    def get(self, connection: Any, terminal_id: str) -> TerminalSession | None:
        return self._by_connection.get(connection, {}).get(terminal_id)

    def scrollback(self, connection: Any, terminal_id: str) -> bytes:
        return bytes(self._scrollback.get((connection, terminal_id), b""))

    def discard(self, connection: Any, terminal_id: str) -> None:
        sessions = self._by_connection.get(connection)
        self._scrollback.pop((connection, terminal_id), None)
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
        for terminal_id, session in sessions.items():
            self._scrollback.pop((connection, terminal_id), None)
            session.close()


def encode_output(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode_input(data: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except ValueError as e:
        raise TerminalError("invalid terminal input encoding") from e
