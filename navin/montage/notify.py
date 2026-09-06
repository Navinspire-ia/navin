"""Montage change fan-out to connected WebUI clients.

The HTTP API (human edits), the agent ``montage`` tool and background render
jobs all call :func:`publish_montage_update` after a change. The websocket
channel broadcasts the event to every open connection; clients showing the
same project refetch the timeline list, the job, or the gallery.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from navin.bus.outbound_events import MontageUpdatedEvent, outbound_message_for_event


def publish_montage_update(
    bus: Any,
    project_path: str | Path | None,
    *,
    kind: str,
    name: str | None = None,
    job: Mapping[str, Any] | None = None,
) -> None:
    """Best-effort broadcast; clients refetch on their own as a fallback."""
    if bus is None:
        return
    event = MontageUpdatedEvent(
        project_path=str(project_path) if project_path else None,
        kind=kind,
        name=name,
        job=dict(job) if job is not None else None,
    )
    message = outbound_message_for_event(channel="websocket", chat_id="*", event=event)

    def _put() -> None:
        try:
            bus.outbound.put_nowait(message)
        except Exception:  # noqa: BLE001 - notification must never fail the caller
            pass

    # Render progress can be reported from a worker thread; the outbound queue
    # belongs to the gateway loop, so hop onto it when we are not on it.
    loop = getattr(bus, "loop", None)
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if loop is not None and running is not loop and getattr(loop, "is_running", lambda: False)():
        loop.call_soon_threadsafe(_put)
        return
    _put()


def job_notifier(bus: Any, project_path: str | Path | None) -> Callable[[dict[str, Any]], None]:
    """A ``notify`` hook for :func:`navin.montage.jobs.run_job` / ``start_job``.

    Broadcasts the compact job summary on every manifest write, and an
    ``assets`` update once a render lands so galleries refresh.
    """
    from navin.montage.jobs import job_summary

    def notify(manifest: dict[str, Any]) -> None:
        summary = job_summary(manifest)
        publish_montage_update(
            bus, project_path, kind="job", name=str(summary.get("id") or ""), job=summary
        )
        if summary.get("status") == "completed":
            publish_montage_update(bus, project_path, kind="assets")

    return notify


__all__ = ["job_notifier", "publish_montage_update"]
