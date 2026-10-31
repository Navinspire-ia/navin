# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Is Navin busy? The automatic install waits for a quiet machine.

An update restarts Navin. Doing it in the middle of an agent mission would
cut the work the user is waiting for, so the automatic install only runs
when every probe says nothing runs (no chat turn, no subagent, no command)
and nobody used Navin for a while. The agent loop registers its probes when
it starts; the chat turns are read directly.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

_LOCK = threading.Lock()
_PROBES: dict[str, Callable[[], int]] = {}
_LAST_ACTIVITY = time.monotonic()


def register_busy_probe(name: str, probe: Callable[[], int]) -> Callable[[], None]:
    """Add a probe returning how many things run; returns its unregister call."""
    with _LOCK:
        _PROBES[name] = probe

    def unregister() -> None:
        with _LOCK:
            if _PROBES.get(name) is probe:
                _PROBES.pop(name, None)

    return unregister


def mark_activity() -> None:
    """Someone used Navin: push the idle window back."""
    global _LAST_ACTIVITY
    with _LOCK:
        _LAST_ACTIVITY = time.monotonic()


def idle_for_s() -> float:
    with _LOCK:
        return time.monotonic() - _LAST_ACTIVITY


def busy_reasons() -> list[str]:
    """What still runs, as short labels. Empty means safe to restart.

    A probe that fails counts as busy: an unknown state must never be read as
    "nothing runs" when the next step is a restart.
    """
    from navin.session.webui_turns import running_websocket_turns

    reasons: list[str] = []
    turns = len(running_websocket_turns())
    if turns:
        reasons.append(f"chat:{turns}")
    with _LOCK:
        probes = list(_PROBES.items())
    for name, probe in probes:
        try:
            count = int(probe())
        except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
            # A probe that cannot answer (a set resized mid-count, a manager
            # torn down) blocks the restart: unknown is not "nothing runs".
            reasons.append(f"{name}:unknown ({type(exc).__name__})")
            continue
        if count > 0:
            reasons.append(f"{name}:{count}")
    if reasons:
        mark_activity()
    return reasons
