# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Start child processes without flashing a console window on Windows.

The desktop build runs the gateway as a windowless process, so a child started
from it has no console to inherit and Windows gives it a brand new one. For a
command that finishes in milliseconds the window still appears, draws itself and
closes, which reads as a flicker on top of whatever the user is doing.

One command would go unnoticed. The editor is built on polling - git status
every twelve seconds, the git panel every fifteen, the review pane every five -
so the flicker never stops while the Code module is open, and several land at
once. ``CREATE_NO_WINDOW`` is what suppresses it; it exists only on Windows, so
everywhere else these helpers add nothing.

Use :func:`no_window_kwargs` for a process whose output navin reads itself. A
process the user is meant to see and interact with, such as a terminal session,
is deliberately not covered.
"""

from __future__ import annotations

import subprocess
import sys
from contextlib import suppress
from typing import Any

# Absent from the module on POSIX, so read it defensively rather than at the
# call sites.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_windows() -> bool:
    return sys.platform == "win32"


def no_window_kwargs() -> dict[str, Any]:
    """Keyword arguments that keep a child process from opening a console.

    Spread into ``subprocess.run``/``Popen``/``asyncio.create_subprocess_*``::

        subprocess.run(argv, capture_output=True, **no_window_kwargs())

    Returns an empty mapping off Windows, so the call site stays portable.
    """
    if not is_windows():
        return {}
    return {"creationflags": _CREATE_NO_WINDOW}


def kill_windows_process_tree(pid: int) -> None:
    """Kill a Windows process and everything it started.

    Terminating a process on Windows does not touch its children, and ConPTY
    does not pass the signal down either. Closing a terminal tab running a dev
    server would leave the server alive and holding its port, invisible until
    the next start failed.

    The native extension walks and kills the tree in-process (sysinfo);
    without it, ``taskkill /T`` is the tool that does the same - at the price
    of one more process spawn.

    Does nothing off Windows, where killing the process group already covers it.
    """
    if not is_windows() or pid <= 0:
        return
    from navin.utils.native import native

    core = native()
    if core is not None:
        try:
            core.kill_tree(pid, force=True)
            return
        except Exception:
            pass  # fall through to taskkill
    with suppress(OSError, subprocess.SubprocessError):
        subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            **no_window_kwargs(),
        )


def kill_posix_process_group(pid: int) -> None:
    """Kill the process group a POSIX child leads, and everything in it.

    ``process.kill()`` reaches only the shell; a dev server it backgrounded or
    a watcher it spawned stays alive holding its port. Children started with
    ``start_new_session=True`` lead their own group, so one ``killpg`` takes the
    whole tree - the POSIX mirror of ``taskkill /T`` above.

    Does nothing on Windows or for a group we cannot resolve.
    """
    if is_windows() or pid <= 0:
        return
    import os
    import signal

    with suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)


def _kill_own_group(pid: int) -> None:
    """SIGKILL the group a ``start_new_session`` child leads (pgid == pid).

    Unlike :func:`kill_posix_process_group` this does not ask ``getpgid``,
    which fails once the leader is gone - exactly when a wrapper (the
    ``pyright`` pip shim) exited and left its ``node`` worker running.
    Windows has no process groups to signal: its trees go through taskkill.
    """
    if is_windows():
        kill_windows_process_tree(pid)
        return
    import os
    import signal

    with suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pid, signal.SIGKILL)


def kill_process_tree(process: subprocess.Popen[Any]) -> None:
    """Kill a child started with :func:`detached_no_window_kwargs` and its descendants."""
    if process.pid <= 0:
        return
    if is_windows():
        if process.poll() is None:
            kill_windows_process_tree(process.pid)
    else:
        _kill_own_group(process.pid)
    with suppress(OSError):
        if process.poll() is None:
            process.kill()


def run_killing_tree(
    argv: list[str],
    *,
    timeout: float,
    capture_output: bool = False,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """``subprocess.run`` whose timeout kills the whole process tree.

    ``subprocess.run(timeout=...)`` kills only the direct child. A tool behind
    a launcher - the ``pyright`` pip wrapper starting ``node``, ``npx``
    starting ``vitest``, pytest-xdist workers - leaves the real worker alive:
    each timed-out ``pyright --project`` run kept a node process at 100% CPU
    and 2.7 GB of RAM forever, until the machine stalled and the web UI froze
    on a black screen. Here the child leads its own group and the timeout (or
    any exception, Ctrl-C included) kills the whole group.

    Raises :class:`subprocess.TimeoutExpired` like ``subprocess.run``.
    """
    kwargs.pop("creationflags", None)
    kwargs.pop("start_new_session", None)
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    process = subprocess.Popen(argv, **kwargs, **detached_no_window_kwargs())  # noqa: S603
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        kill_process_tree(process)
        with suppress(OSError, ValueError, subprocess.SubprocessError):
            process.communicate(timeout=5)
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def detached_no_window_kwargs() -> dict[str, Any]:
    """As :func:`no_window_kwargs`, for a child that outlives its parent.

    Adds a new process group so a Ctrl-C sent to navin is not forwarded to the
    child, and mirrors that with a new session on POSIX.
    """
    if not is_windows():
        return {"start_new_session": True}
    flags = _CREATE_NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": flags}
