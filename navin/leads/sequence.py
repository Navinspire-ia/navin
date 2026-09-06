"""3-step cold sequence. Heartbeat reports due steps and never sends."""

from __future__ import annotations

import time
from typing import Any

STEPS = (
    {"n": 1, "day": 0, "channel": "email", "label": "j0"},
    {"n": 2, "day": 3, "channel": "email", "label": "j3"},
    {"n": 3, "day": 7, "channel": "email", "label": "j7"},
)


def _now() -> float:
    return time.time()


def start_sequence(row: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    stamp = now if now is not None else _now()
    steps = []
    for item in STEPS:
        steps.append(
            {
                **item,
                "status": "due" if item["day"] == 0 else "pending",
                "due_at": stamp + item["day"] * 86400,
                "sent_at": 0,
            }
        )
    return {
        "name": "cold-3",
        "started_at": stamp,
        "step": 1,
        "steps": steps,
    }


def due_steps(row: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
    """Only the current unsent step can be due. j3 never fires before j0 is sent.

    A stopped sequence (opt-out, lost) never has a due step.
    """
    seq = row.get("sequence") if isinstance(row.get("sequence"), dict) else {}
    if seq.get("stopped"):
        return []
    steps = seq.get("steps") if isinstance(seq.get("steps"), list) else []
    stamp = now if now is not None else _now()
    ordered = [step for step in steps if isinstance(step, dict)]
    ordered.sort(key=lambda item: int(item.get("n") or 0))
    for step in ordered:
        if str(step.get("status") or "") == "sent":
            continue
        try:
            due_at = float(step.get("due_at") or 0)
        except (TypeError, ValueError):
            return []
        if due_at and due_at <= stamp:
            return [dict(step)]
        return []
    return []


def mark_step_sent(row: dict[str, Any], *, channel: str = "email", now: float | None = None) -> dict[str, Any]:
    raw = row.get("sequence") if isinstance(row.get("sequence"), dict) else None
    steps_in = raw.get("steps") if raw and isinstance(raw.get("steps"), list) else []
    if not raw or not steps_in:
        return dict(row)
    seq = dict(raw)
    steps = [dict(item) for item in steps_in if isinstance(item, dict)]
    stamp = now if now is not None else _now()
    for step in steps:
        if str(step.get("status") or "") == "sent":
            continue
        try:
            due_at = float(step.get("due_at") or 0)
        except (TypeError, ValueError):
            due_at = 0
        if due_at and due_at > stamp:
            continue
        step["status"] = "sent"
        step["sent_at"] = stamp
        step["sent_channel"] = channel
        seq["step"] = int(step.get("n") or 0)
        break
    seq["steps"] = steps
    next_row = dict(row)
    next_row["sequence"] = seq
    return next_row
