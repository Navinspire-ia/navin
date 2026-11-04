# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep the CLI open through an error of its interface, and say why it stopped.

Textual ends the whole app on any exception that a worker, a timer or a message
handler lets through. In navin-cli that ends every turn, subagent and command
the CLI runs as well. On 2026-11-03 CLIs closed "by themselves" and took their
work with them, and nothing said why: the terminal UI hides navin's logs.

Once the chat is up:

- an error of the interface is written with its stack to the black box
  (``~/.navin/logs/cli-freeze.log``, next to the freezes, with the pid and the
  project) and shown in the chat, and the CLI goes on;
- every way the CLI stops (quit, signal, fatal error, panic) leaves its reason
  there too.

A CLI that fails before its chat is up stays fatal: there is nothing to keep,
and Textual prints the error where the user started it.
"""

from __future__ import annotations

import io
import os
import signal
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from textual import events, messages
from textual._context import NoActiveAppError
from textual.message_pump import MessagePump
from textual.worker import WorkerFailed

# Textual's lifecycle messages keep their meaning: a widget that cannot compose
# or mount is not mounted, and a message loop asked to close closes.
_LIFECYCLE = (events.Compose, events.Mount, messages.CloseMessages, messages.Prune, messages.ExitApp)


def log_path() -> Path:
    from navin.config.paths import get_logs_dir

    return get_logs_dir() / "cli-freeze.log"


def record(kind: str, text: str, *, project: str = "", error: BaseException | None = None) -> str | None:
    """Append one entry to the black box: None once written, else why it was not.

    One ``write`` per entry, in append mode: several CLIs share the file and
    their entries never interleave.
    """
    where = f"pid {os.getpid()}" + (f" project {project}" if project else "")
    lines = [f"{time.strftime('%Y-%m-%d %H:%M:%S')} | [{where}] {kind}: {text}"]
    if error is not None:
        stack = "".join(traceback.format_exception(error)).rstrip()
        lines.extend(f"  {line}" for line in stack.splitlines())
    data = ("\n".join(lines) + "\n").encode("utf-8", "replace")
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def unwrap(error: BaseException) -> BaseException:
    """The error a failed worker raised, not Textual's wrapper around it."""
    while isinstance(error, WorkerFailed) and isinstance(error.error, BaseException):
        error = error.error
    return error


def plain_text(renderables: tuple[Any, ...], width: int = 120) -> str:
    """What a panic shows (rich tracebacks, text), as plain text for the log."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=width, color_system=None, force_terminal=False)
    for renderable in renderables:
        console.print(renderable)
    return buffer.getvalue().rstrip()


class ErrorTally:
    """The errors of the interface, counted by where they were raised.

    The first one from a place is written in full and shown. A timer failing
    at every tick then adds one line at its 10th, 100th, 1000th... failure
    instead of filling the disk and the screen.
    """

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = {}

    def count(self, error: BaseException) -> int:
        # walk_tb: the last frame without reading source lines (a failing
        # timer comes back eight times a second).
        place = ""
        for frame, lineno in traceback.walk_tb(error.__traceback__):
            place = f"{frame.f_code.co_filename}:{lineno}"
        key = (type(error).__name__, place)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]


def worth_a_line(count: int) -> bool:
    """1, 10, 100, 1000...: the occurrences of one error that get a line."""
    while count >= 10 and count % 10 == 0:
        count //= 10
    return count == 1


def _reporting_app(pump: MessagePump, message: Any) -> Any:
    """The navin app that takes this handler error, or None to let it raise."""
    if isinstance(message, _LIFECYCLE):
        return None
    try:
        app = pump.app
    except NoActiveAppError:
        return None
    if not getattr(app, "_navin_ui_ready", False) or getattr(app, "_exit", False):
        return None
    return app


def install_handler_guard() -> None:
    """Keep a message loop (the app's, a screen's, a widget's) alive past a handler error.

    Textual leaves the loop on the first error: a widget stops answering, and
    the app's own loop ending is the app ending. In navin-cli, once the chat
    is up, the error goes to ``_report_ui_error`` and the next message is
    processed. Any other app, and navin-cli while it starts, behave as before.
    One coroutine layer per message: this runs for every key, frame and event.
    """
    if getattr(MessagePump, "_navin_handler_guard", False):
        return
    original = MessagePump._dispatch_message

    async def _dispatch_message(self: MessagePump, message: Any) -> None:
        try:
            await original(self, message)
        except Exception as error:  # every handler's boundary: reported, or raised as before
            app = _reporting_app(self, message)
            if app is None:
                raise
            app._report_ui_error(error, where=self)

    MessagePump._dispatch_message = _dispatch_message
    MessagePump._navin_handler_guard = True


# How long the clean quit a stop signal asks for may take (sessions flushed,
# engine closed: about 15 s at worst) before the process is ended anyway.
STOP_SIGNAL_GRACE_S = 20.0


def force_exit(signum: int, restore: Callable[[], str | None] | None = None) -> None:
    """End the process now, with the shell's exit code for a death by *signum*.

    *restore* gives the terminal its modes back first (echo, line editing),
    without writing or waiting; what it reports going wrong is recorded.
    """
    if restore is not None:
        problem = restore()
        if problem:
            record("signal", f"terminal modes not restored before the forced exit: {problem}")
    os._exit(128 + signum)


class StopWatchdog:
    """Ends the process when the clean quit a stop signal asked for does not come.

    Started before any signal: the signal handler only sets an event. Starting
    a thread from a handler could wait forever on a lock that the code the
    signal interrupted holds.
    """

    def __init__(
        self, grace: float, project: Callable[[], str], restore: Callable[[], str | None] | None = None,
    ) -> None:
        self.grace = grace
        self._project = project
        self._restore = restore
        self._armed = threading.Event()
        self._signal: tuple[str, int] = ("", 0)
        self.thread = threading.Thread(target=self._watch, name="navin-stop-watchdog", daemon=True)
        self.thread.start()

    def arm(self, name: str, signum: int) -> None:
        self._signal = (name, signum)
        self._armed.set()

    def _watch(self) -> None:
        self._armed.wait()
        name, signum = self._signal
        time.sleep(self.grace)
        record(
            "signal", f"{name}: navin-cli did not close within {self.grace:g} s, forced exit",
            project=self._project(),
        )
        force_exit(signum, restore=self._restore)


def ignore_ctrl_z() -> bool:
    """Ctrl+Z never suspends the CLI: True once SIGTSTP is ignored.

    ``run_tui`` ignores SIGTSTP, but Textual's Linux driver then installs a
    handler of its own that stops the process (SIGSTOP). Ctrl+Z only reaches
    the CLI as a signal when the terminal is in cooked mode (the shutdown
    window, a child that reset the terminal), and then it froze the CLI with
    all its work while the shell showed it as gone ("Stopped"). A CLI found
    stopped since 2026-11-01, with that handler installed, is how this was
    noticed on 2026-11-03; the kernel does not keep which signal stopped it.
    Called once the driver is built. Signal handlers belong to the main
    thread: elsewhere this changes nothing and says so.
    """
    tstp = getattr(signal, "SIGTSTP", None)
    if tstp is None or threading.current_thread() is not threading.main_thread():
        return False
    signal.signal(tstp, signal.SIG_IGN)
    return True
