# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bounded CLI shutdown: cleanup waits, loop close and terminal restore."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from contextlib import suppress

from loguru import logger


def consume_result(task: asyncio.Task) -> None:
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.debug("CLI shutdown task {}: {}", task.get_name(), error)


async def drain(tasks: list[asyncio.Task], *, timeout: float) -> bool:
    if not tasks:
        return True
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in done:
        consume_result(task)
    for task in pending:
        task.cancel()
        task.add_done_callback(consume_result)
    if pending:
        # Let cancellation close subprocess transports, without letting a
        # remote MCP server or an integration hold the terminal indefinitely.
        await asyncio.wait(pending, timeout=0.1)
    return not pending


# Leave the alternate screen, show the cursor, stop mouse tracking and
# bracketed paste. Each is a no-op when already off.
_TERMINAL_RESET = "\x1b[?1049l\x1b[?25h\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l"


class TerminalGuard:
    """Put the terminal back exactly as the shell left it.

    Textual restores it on a clean exit. When shutdown is cut short (a
    stuck cleanup, an exception during unmount) the shell used to stay
    without echo: typed commands became invisible.
    """

    def __init__(self) -> None:
        self._attrs = None
        self._fd = None
        try:
            import termios

            fd = sys.stdin.fileno()
            if os.isatty(fd):
                self._attrs = termios.tcgetattr(fd)
                self._fd = fd
        except Exception:  # noqa: BLE001 - Windows console, redirected stdin
            self._attrs = None

    def restore(self) -> None:
        with suppress(Exception):
            if sys.stdout.isatty():
                sys.stdout.write(_TERMINAL_RESET)
                sys.stdout.flush()
        if self._attrs is not None and self._fd is not None:
            with suppress(Exception):
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._attrs)


def _cancel_leftovers(loop: asyncio.AbstractEventLoop, timeout: float) -> None:
    tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
    if not tasks:
        return
    for task in tasks:
        task.cancel()
        task.add_done_callback(consume_result)
    loop.run_until_complete(asyncio.wait(tasks, timeout=timeout))


def run_app_fast_exit(app, *, leftover_timeout: float = 1.0):
    """Run a Textual app, then close the loop without waiting on stuck threads.

    ``asyncio.run`` joins the default executor for up to five minutes: one
    blocked worker thread (a hung git, clipboard or tool call) froze the
    terminal after the UI had already gone.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(app.run_async())
    finally:
        try:
            _cancel_leftovers(loop, leftover_timeout)
            with suppress(Exception):
                loop.run_until_complete(asyncio.wait_for(loop.shutdown_asyncgens(), leftover_timeout))
            executor = getattr(loop, "_default_executor", None)
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def exit_if_threads_linger(grace: float = 1.0) -> None:
    """Exit now when non-daemon threads would otherwise hold the process.

    Sessions are already flushed by the runtime. Python joins executor and
    non-daemon threads at exit with no limit; the shell must come back.
    """
    deadline = time.monotonic() + grace
    for thread in threading.enumerate():
        if thread is threading.main_thread() or thread.daemon:
            continue
        thread.join(max(0.0, deadline - time.monotonic()))
    if any(t.is_alive() and not t.daemon and t is not threading.main_thread() for t in threading.enumerate()):
        with suppress(Exception):
            sys.stdout.flush()
            sys.stderr.flush()
        os._exit(0)
