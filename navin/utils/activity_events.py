# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Versioned activity events (schema v1), derived from the existing tool events.

Additive layer only: the legacy ``tool_events`` payload is kept unchanged and
these events ride next to it, so older clients keep working and the UI can fall
back to the legacy rendering. Nothing here touches reasoning, context, memory
or model selection.
"""

from __future__ import annotations

import time
from typing import Any

ACTIVITY_EVENTS_VERSION = 1

EVENT_TYPES = (
    "task.started",
    "tool.started",
    "tool.output",
    "tool.finished",
    "file.changed",
    "test.completed",
    "agent.started",
    "approval.required",
    "task.completed",
)

_PHASE_TO_TYPE = {"start": "tool.started", "output": "tool.output", "end": "tool.finished", "error": "tool.finished"}
_TEST_TOOLS = {"test_run", "verify"}


def make_event(
    event_type: str,
    *,
    event_id: str,
    agent: str = "main",
    parent: str | None = None,
    status: str = "ok",
    data: dict[str, Any] | None = None,
    ts: float | None = None,
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown activity event type: {event_type}")
    return {
        "v": ACTIVITY_EVENTS_VERSION,
        "type": event_type,
        "id": event_id,
        "ts": round(ts if ts is not None else time.time(), 3),
        "agent": agent,
        "parent": parent,
        "status": status,
        "data": data or {},
    }


def activity_events_from_tool_events(
    tool_events: list[dict[str, Any]] | None,
    *,
    agent: str = "main",
    parent: str | None = None,
) -> list[dict[str, Any]]:
    """Map legacy tool events to v1 activity events. Unknown or malformed entries are skipped."""
    out: list[dict[str, Any]] = []
    for raw in tool_events or []:
        if not isinstance(raw, dict):
            continue
        phase = raw.get("phase")
        event_type = _PHASE_TO_TYPE.get(phase) if isinstance(phase, str) else None
        if event_type is None:
            continue
        call_id = str(raw.get("call_id") or "")
        name = str(raw.get("name") or "")
        status = {"start": "running", "output": "running", "error": "error"}.get(phase, "ok")
        data: dict[str, Any] = {"tool": name, "call_id": call_id}
        if phase == "error" and raw.get("error"):
            data["error"] = str(raw.get("error"))[:2000]
        base_id = call_id or f"{name}:{len(out)}"
        out.append(make_event(event_type, event_id=f"{base_id}:{phase}", agent=agent, parent=parent, status=status, data=data))
        if phase in ("end", "error"):
            files = [f for f in raw.get("files") or [] if isinstance(f, (str, dict))]
            for idx, item in enumerate(files):
                path = item if isinstance(item, str) else str(item.get("path") or "")
                if path:
                    out.append(
                        make_event("file.changed", event_id=f"{base_id}:file:{idx}", agent=agent, parent=base_id, data={"path": path})
                    )
            if name in _TEST_TOOLS:
                out.append(
                    make_event("test.completed", event_id=f"{base_id}:test", agent=agent, parent=base_id, status=status, data={"tool": name})
                )
    return out
