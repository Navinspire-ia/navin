"""Metagraph change fan-out to connected WebUI clients.

After annotate, index refresh, or a debounced file-write batch, call
:func:`publish_metagraph_update`. The websocket channel broadcasts
``metagraph_updated``; clients apply the structured diff or full-fetch.
"""

from __future__ import annotations

import threading
from typing import Any

from navin.bus.outbound_events import MetagraphUpdatedEvent, outbound_message_for_event

_DEBOUNCE_S = 0.3
_timers: dict[str, threading.Timer] = {}
_timers_lock = threading.Lock()


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


def schedule_metagraph_refresh(root: str | Any) -> None:
    """Debounce a rebuild+notify after file edits (default bus)."""
    key = str(root)
    with _timers_lock:
        previous = _timers.pop(key, None)
        if previous is not None:
            previous.cancel()

        def _fire() -> None:
            with _timers_lock:
                _timers.pop(key, None)
            try:
                from pathlib import Path

                from navin.webui.metagraph import rebuild_and_notify

                rebuild_and_notify(Path(key))
            except Exception:
                pass

        timer = threading.Timer(_DEBOUNCE_S, _fire)
        timer.daemon = True
        _timers[key] = timer
        timer.start()
