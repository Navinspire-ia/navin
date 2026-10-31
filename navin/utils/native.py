# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Access point for the optional native (Rust) extension ``navin_core``.

The extension accelerates hot paths - content search, batch file reads, tree
hashing, process management, git plumbing, PTY sessions, and Android mobile
preview (``MobilePreviewSession``) - but every caller keeps a pure-Python
fallback, so navin runs unchanged when it is absent (a source checkout without
``make native``, or a platform without a prebuilt wheel). Import errors are
swallowed here once, and callers ask :func:`native` for the module or ``None``.

Set ``NAVIN_DISABLE_NATIVE=1`` to force the pure-Python paths (used by the
parity tests and as an escape hatch if a native path ever misbehaves).
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_module: Any | None = None
_loaded = False

# Native calls that release the GIL (``py.allow_threads``) and run in daemon
# threads, e.g. the background full-text index warm. A daemon thread still
# inside one when the interpreter finalizes re-acquires the GIL on return,
# CPython ends it with ``pthread_exit``, and pyo3's panic guard catches that
# forced unwind: glibc aborts the whole process ("FATAL: exception not
# rethrown", a core dump when the CLI or the gateway quits). Background callers
# enter ``native_section``; at exit no new section starts and the ones in
# flight are given NATIVE_EXIT_WAIT_S to finish, each kept short by its caller.
NATIVE_EXIT_WAIT_S = 5.0
_sections = 0
_closing = False
_sections_cond = threading.Condition()


class NativeShutdownError(RuntimeError):
    """The interpreter is exiting: background native work must not start."""


@contextmanager
def native_section() -> Iterator[None]:
    """Mark a GIL-releasing native call made from a background thread.

    Raises :class:`NativeShutdownError` once the interpreter has started to exit,
    so a background loop stops between two native calls instead of being
    caught inside one by the finalization.
    """
    global _sections
    with _sections_cond:
        if _closing:
            raise NativeShutdownError("interpreter exiting")
        _sections += 1
    try:
        yield
    finally:
        with _sections_cond:
            _sections -= 1
            _sections_cond.notify_all()


def native_closing() -> bool:
    return _closing


def wait_for_native_sections(timeout: float = NATIVE_EXIT_WAIT_S) -> bool:
    """Refuse new sections and wait for the running ones; True when none is left."""
    global _closing
    deadline = time.monotonic() + timeout
    with _sections_cond:
        _closing = True
        while _sections:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _sections_cond.wait(left)
    return True


# atexit callbacks run after the non-daemon threads are joined and before the
# daemon threads are stopped: the only moment where waiting is still useful.
atexit.register(wait_for_native_sections)


def native() -> Any | None:
    """Return the ``navin_core`` module, or ``None`` if unavailable/disabled."""
    global _module, _loaded
    if _loaded:
        return _module
    _loaded = True
    if os.environ.get("NAVIN_DISABLE_NATIVE") == "1":
        _module = None
        return None
    try:
        import navin_core  # type: ignore
    except ImportError:
        _module = None
    else:
        _module = navin_core
    return _module


def has_native() -> bool:
    return native() is not None


def native_version() -> str | None:
    module = native()
    return getattr(module, "__version__", None) if module is not None else None
