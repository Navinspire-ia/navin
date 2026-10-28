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
                # Keys typed during the exit never reach the shell prompt.
                termios.tcflush(self._fd, termios.TCIFLUSH)


def release_input_thread(app) -> None:
    """Let the terminal input thread end now, in parallel with the engine's shutdown.

    Textual's driver stops that thread in ``disable_input`` by joining it,
    and the thread polls stdin in 100 ms selects, twice on the way out: up
    to 200 ms with the event loop blocked, measured at 32 percent of a quit.
    Setting its exit flag the moment the user quits lets it finish while the
    engine closes; the later ``disable_input`` sees the flag and skips the
    join. What that skip leaves out (the mouse modes, the terminal
    attributes, the pending input) :class:`TerminalGuard` restores anyway.
    """
    driver = getattr(app, "_driver", None)
    event = getattr(driver, "exit_event", None)
    if driver is None or event is None or getattr(driver, "is_headless", False):
        return
    with suppress(Exception):
        driver._disable_mouse_support()
    event.set()


def _cancel_leftovers(loop: asyncio.AbstractEventLoop, timeout: float) -> None:
    tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
    if not tasks:
        return
    for task in tasks:
        task.cancel()
        task.add_done_callback(consume_result)
    loop.run_until_complete(asyncio.wait(tasks, timeout=timeout))


def close_subprocess_transports(loop: asyncio.AbstractEventLoop) -> int:
    """Close child-process transports still open on *loop*, while it runs.

    A cleanup cut short (a slow MCP server, a command still running) left
    them to the garbage collector after loop.close(): their __del__ then
    printed "Exception ignored ... RuntimeError: Event loop is closed" under
    the exit summary. A child still running is killed with its process group
    (commands start their own session), not just its shell.

    The loop's child registry (``child_processes.install``) lists them in
    microseconds. Without one, the whole heap is walked: 200 to 400 ms on a
    long chat, and the attribute reads woke the OpenAI SDK's lazy modules.
    """
    from asyncio.base_subprocess import BaseSubprocessTransport

    from navin.utils import child_processes
    from navin.utils.proc import kill_posix_process_group, kill_windows_process_tree

    registry = child_processes.registry_of(loop)
    if registry is not None:
        leftovers = registry.transports()
    else:
        import gc

        leftovers = [
            obj for obj in gc.get_objects()
            if isinstance(obj, BaseSubprocessTransport)
            and getattr(obj, "_loop", None) is loop and not obj.is_closing()
        ]
    for transport in leftovers:
        with suppress(Exception):
            pid = transport.get_pid()
            if pid and transport.get_returncode() is None:
                if sys.platform == "win32":
                    kill_windows_process_tree(pid)
                elif os.getpgid(pid) != os.getpgid(0):
                    # Only a child that leads its own group: killpg on a
                    # child sharing ours (git, an MCP server) would kill navin.
                    kill_posix_process_group(pid)
        with suppress(Exception):
            # Kills a child still running (the process itself) and closes pipes.
            transport.close()
    if leftovers:
        # Pipe closes are scheduled with call_soon: let them run.
        with suppress(Exception):
            loop.run_until_complete(asyncio.sleep(0.05))
    return len(leftovers)


def run_app_fast_exit(app, *, leftover_timeout: float = 1.0):
    """Run a Textual app, then close the loop without waiting on stuck threads.

    ``asyncio.run`` joins the default executor for up to five minutes: one
    blocked worker thread (a hung git, clipboard or tool call) froze the
    terminal after the UI had already gone.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    from navin.utils import child_processes

    # Every child the CLI starts is known by name at exit: no heap walk then.
    child_processes.install(loop)
    try:
        return loop.run_until_complete(app.run_async())
    finally:
        try:
            _cancel_leftovers(loop, leftover_timeout)
            with suppress(Exception):
                loop.run_until_complete(asyncio.wait_for(loop.shutdown_asyncgens(), leftover_timeout))
            close_subprocess_transports(loop)
            executor = getattr(loop, "_default_executor", None)
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def wake_idle_executor_workers() -> int:
    """Tell every idle thread-pool worker to finish; returns how many were told.

    A ``ThreadPoolExecutor`` worker (the agent journal's, a tool's) is a
    non-daemon thread waiting on its queue: the interpreter joins it at exit
    with no limit, and :func:`exit_if_threads_linger` used to spend its whole
    second on it before giving up. This does now what ``concurrent.futures``
    does a moment later at interpreter exit: mark the pools as shutting down
    and post each queue its end marker. A worker finishes the items already
    queued (a journal line is never lost), then exits. Only for the very end
    of the process: no pool accepts work after it.
    """
    from concurrent.futures import thread as pool_thread

    lock = getattr(pool_thread, "_global_shutdown_lock", None)
    queues = []
    with lock if lock is not None else suppress():
        pool_thread._shutdown = True
        queues = list(getattr(pool_thread, "_threads_queues", {}).values())
    for queue in queues:
        with suppress(Exception):
            queue.put(None)
    return len(queues)


def exit_if_threads_linger(grace: float = 1.0) -> None:
    """Leave the process now that the exit summary is printed.

    Sessions are already flushed by the runtime. The idle pool workers are
    told to finish and joined for at most ``grace`` (milliseconds once told;
    a queued journal line is written first). The terminal input thread is
    not waited for: it only waits out its last 100 ms poll of stdin. Then the
    atexit hooks run (log sinks, journal writers) and the process ends with
    ``os._exit``: the interpreter's own teardown walks and frees the whole
    heap, seconds on a long chat, for nothing the user can see.
    """
    join_background_threads(grace)
    exit_now()


# Threads that need no join before the process ends: they only wait out a poll.
INPUT_THREADS = frozenset({"textual-input"})


def join_background_threads(grace: float = 1.0) -> list[str]:
    """Wake the pool workers, join the other non-daemon threads; names still alive."""
    wake_idle_executor_workers()
    deadline = time.monotonic() + grace
    for thread in threading.enumerate():
        if thread is threading.main_thread() or thread.daemon or thread.name in INPUT_THREADS:
            continue
        thread.join(max(0.0, deadline - time.monotonic()))
    return [
        t.name for t in threading.enumerate()
        if t.is_alive() and not t.daemon and t is not threading.main_thread() and t.name not in INPUT_THREADS
    ]


def exit_now(code: int = 0) -> None:
    """Run the atexit hooks, flush the streams and end the process at once."""
    import atexit

    with suppress(Exception):
        atexit._run_exitfuncs()
    with suppress(Exception):
        sys.stdout.flush()
        sys.stderr.flush()
    os._exit(code)
