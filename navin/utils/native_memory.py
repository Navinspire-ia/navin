# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Give the memory Navin's threads free back to the machine (glibc only).

glibc's malloc gives a thread that allocates an arena of its own, up to
eight per core, and memory freed inside an arena stays with the process
until the top of that arena is free. A CLI keeps up to 232 threads for
blocking work (session saves, file reads, lint and test runs): on 2026-11-05
four fresh CLIs each held 31 to 39 arenas and 2.4 to 2.6 GB of resident
memory after 30 minutes, and five older ones filled most of a 32 GB WSL VM.
Python threads allocate under the GIL, so many arenas buy Navin's own code
almost no parallelism.

:func:`tune_malloc` caps the arena count at process start, before the
threads exist; :func:`trim` hands the free pages of every arena back to the
kernel (``malloc_trim``), and :func:`start_trimmer` does that from a
background thread each time the resident size has grown by
``TRIM_GROWTH_BYTES``. A ``MALLOC_ARENA_MAX`` set by the user wins;
``NAVIN_MALLOC_TUNING=0`` turns both off. Other C libraries (macOS, Windows,
musl) are left alone.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from functools import lru_cache

from loguru import logger

M_ARENA_MAX = -8  # mallopt parameter number, from glibc's malloc.h
DEFAULT_ARENA_MAX = 2
TRIM_INTERVAL_S = 30.0
TRIM_GROWTH_BYTES = 64 * 1024 * 1024
_OFF = frozenset({"0", "false", "no", "off"})
_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

_trimmer_lock = threading.Lock()
_trimmer: threading.Thread | None = None


def enabled() -> bool:
    return os.environ.get("NAVIN_MALLOC_TUNING", "").strip().lower() not in _OFF


@lru_cache(maxsize=1)
def _glibc() -> ctypes.CDLL | None:
    """This process's glibc, None under any other C library."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        version = os.confstr("CS_GNU_LIBC_VERSION") or ""
    except (OSError, ValueError) as exc:
        logger.debug("C library version unknown: {}", exc)
        return None
    if not version.startswith("glibc"):
        return None
    try:
        libc = ctypes.CDLL("libc.so.6")
    except OSError as exc:
        logger.debug("glibc could not be loaded: {}", exc)
        return None
    libc.mallopt.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.mallopt.restype = ctypes.c_int
    libc.malloc_trim.argtypes = [ctypes.c_size_t]
    libc.malloc_trim.restype = ctypes.c_int
    return libc


def tune_malloc(arena_max: int = DEFAULT_ARENA_MAX) -> str:
    """Cap glibc's arena count for the threads started from now on.

    Returns what was done, for the start-up log.
    """
    if not enabled():
        return "tuning turned off (NAVIN_MALLOC_TUNING=0)"
    if os.environ.get("MALLOC_ARENA_MAX"):
        return f"MALLOC_ARENA_MAX={os.environ['MALLOC_ARENA_MAX']} kept"
    libc = _glibc()
    if libc is None:
        return "not glibc, left as is"
    if libc.mallopt(M_ARENA_MAX, max(1, int(arena_max))) != 1:
        return "glibc refused the arena cap"
    return f"glibc arenas capped at {max(1, int(arena_max))}"


def resident_bytes() -> int:
    """Resident size of this process, 0 when unknown."""
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            fields = handle.read().split()
    except OSError as exc:
        logger.debug("resident size unknown: {}", exc)
        return 0
    return int(fields[1]) * _PAGE if len(fields) > 1 and fields[1].isdigit() else 0


def trim() -> bool:
    """Return the free pages of every glibc arena to the kernel.

    True when memory was released. ctypes releases the GIL for the call, so
    other threads keep running while glibc walks its arenas.
    """
    libc = _glibc()
    if libc is None or not enabled():
        return False
    return bool(libc.malloc_trim(0))


def _trim_loop(interval: float, growth: int, stop: threading.Event) -> None:
    baseline = resident_bytes()
    while not stop.wait(interval):
        current = resident_bytes()
        if current - baseline < growth:
            continue
        trim()
        after = resident_bytes()
        if current - after >= growth // 4:
            logger.debug("malloc_trim gave back {} MB", (current - after) // (1024 * 1024))
        baseline = after


def start_trimmer(
    interval: float = TRIM_INTERVAL_S, growth: int = TRIM_GROWTH_BYTES,
) -> threading.Event | None:
    """Trim in a daemon thread after each growth of ``growth`` bytes.

    One per process; returns the event that stops it, None when there is
    nothing to do here (not glibc, or turned off).
    """
    global _trimmer
    if _glibc() is None or not enabled():
        return None
    with _trimmer_lock:
        if _trimmer is not None and _trimmer.is_alive():
            return None
        stop = threading.Event()
        _trimmer = threading.Thread(
            target=_trim_loop, args=(interval, growth, stop), name="navin-malloc-trim", daemon=True,
        )
        _trimmer.start()
        return stop
