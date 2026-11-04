# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Metagraph change fan-out to connected WebUI clients.

After annotate, index refresh, or a debounced file-write batch, call
:func:`publish_metagraph_update`. The websocket channel broadcasts
``metagraph_updated``; clients apply the structured diff or full-fetch.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from loguru import logger

from navin.bus.outbound_events import MetagraphUpdatedEvent, outbound_message_for_event

_DEBOUNCE_S = 0.3
# One worker thread serves every root: a Timer per write started (and
# cancelled) a thread on each edit, which waited on a busy interpreter.
_due: dict[str, float] = {}
_due_cond = threading.Condition()
_worker: threading.Thread | None = None


def publish_metagraph_update(
    bus: Any,
    project_path: str,
    *,
    generation: int = 0,
    diff: dict[str, Any] | None = None,
    view: str = "files",
) -> None:
    """Queue a metagraph change. Best-effort: never raises for the caller."""
    if bus is None:
        try:
            from navin.bus import notify as notify_mod

            bus = getattr(notify_mod, "_default_bus", None)
        except Exception:
            bus = None
    if bus is None:
        return
    try:
        bus.outbound.put_nowait(
            outbound_message_for_event(
                channel="websocket",
                chat_id="*",
                event=MetagraphUpdatedEvent(
                    project_path=project_path,
                    generation=generation,
                    diff=diff,
                    view=view,
                ),
            )
        )
    except Exception:
        pass


def _rebuild(key: str) -> None:
    """Rebuild and broadcast the graph of *key*, for an open Graph view only.

    The Graph tab builds the graph when it opens. Rebuilding it (a tree walk
    plus a layout) after every write of every agent, with nobody looking,
    kept a core busy for whole turns on Windows (2026-11-04 probe).
    """
    from pathlib import Path

    from navin.webui.metagraph import graph_watched, rebuild_and_notify

    root = Path(key)
    if not graph_watched(root):
        return
    try:
        rebuild_and_notify(root)
    except Exception:  # worker boundary: one failed rebuild must not stop the next ones
        logger.opt(exception=True).warning("Graph rebuild of {} failed", key)


def _run_worker() -> None:
    while True:
        with _due_cond:
            while not _due:
                _due_cond.wait()
            now = time.monotonic()
            ready = [key for key, at in _due.items() if at <= now]
            if not ready:
                _due_cond.wait(min(_due.values()) - now)
                continue
            for key in ready:
                _due.pop(key, None)
        for key in ready:
            _rebuild(key)


def schedule_metagraph_refresh(root: str | Any) -> None:
    """Debounce a rebuild+notify after file edits (default bus)."""
    global _worker
    with _due_cond:
        # Each new write pushes the rebuild back: one per burst of edits.
        _due[str(root)] = time.monotonic() + _DEBOUNCE_S
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run_worker, name="navin-metagraph-refresh", daemon=True)
            _worker.start()
        _due_cond.notify()
