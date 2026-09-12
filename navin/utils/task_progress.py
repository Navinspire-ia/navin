# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Task progress blobs for rich clients (bars, ETA, step labels).

Tools and orchestrators call :func:`emit_task_progress` while a turn is active.
The progress hook binds an emitter that forwards ``agent_ui.task_progress`` on
the WebSocket (and optionally enriches the current tool's ``output`` event).
"""

from __future__ import annotations

import math
import re
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from loguru import logger

TaskProgressEmitter = Callable[[dict[str, Any]], Awaitable[None]]

_EMITTER: ContextVar[TaskProgressEmitter | None] = ContextVar(
    "navin_task_progress_emitter",
    default=None,
)

_PERCENT_RE = re.compile(
    r"(?P<pct>\d{1,3}(?:\.\d+)?)\s*%|"
    r"\[\s*(?P<bar>\d{1,3})\s*/\s*100\s*\]|"
    r"progress[:\s]+(?P<label_pct>\d{1,3}(?:\.\d+)?)",
    re.IGNORECASE,
)
_ETA_RE = re.compile(
    r"(?:eta|remaining|left)[:\s]*"
    r"(?:(?P<h>\d+)\s*h(?:ours?)?\s*)?"
    r"(?:(?P<m>\d+)\s*m(?:in(?:utes?)?)?\s*)?"
    r"(?:(?P<s>\d+)\s*s(?:ec(?:onds?)?)?)?",
    re.IGNORECASE,
)
_DOWNLOAD_RE = re.compile(
    r"(download|installing|extracting|building|compiling|sdkmanager)",
    re.IGNORECASE,
)


def progress_bar(percent: float | int | None, *, width: int = 10) -> str:
    """A compact terminal bar; absent measurements stay indeterminate."""
    if percent is None or not math.isfinite(percent):
        return ""
    value = max(0, min(100, round(percent)))
    filled = value * width // 100
    return f"[{'━' * filled}{'·' * (width - filled)}] {value}%"


def bind_task_progress_emitter(emitter: TaskProgressEmitter | None) -> None:
    """Install the per-turn emitter (progress hook, before tool execution)."""
    _EMITTER.set(emitter)


def task_progress_enabled() -> bool:
    return _EMITTER.get() is not None


def build_task_progress_data(
    *,
    label: str,
    percent: float | int | None = None,
    indeterminate: bool | None = None,
    eta_s: float | int | None = None,
    step: str | None = None,
    step_index: int | None = None,
    steps_total: int | None = None,
    call_id: str | None = None,
) -> dict[str, Any]:
    """Normalize a task_progress payload for the wire."""
    data: dict[str, Any] = {"label": (label or "").strip() or "Working…"}
    if call_id:
        data["call_id"] = call_id
    if step:
        data["step"] = step
    if step_index is not None:
        data["step_index"] = int(step_index)
    if steps_total is not None:
        data["steps_total"] = int(steps_total)

    pct: float | None = None
    if percent is not None:
        try:
            pct = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            pct = None
    if pct is not None:
        data["percent"] = pct
        data["indeterminate"] = False
    else:
        data["indeterminate"] = True if indeterminate is None else bool(indeterminate)

    if eta_s is not None:
        try:
            data["eta_s"] = max(0.0, float(eta_s))
        except (TypeError, ValueError):
            pass
    return data


def build_agent_ui_blob(data: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "task_progress", "data": data}


def parse_progress_from_output(text: str) -> dict[str, Any]:
    """Best-effort parse of percent / ETA / label from tool stdout tails."""
    if not text or not text.strip():
        return {"indeterminate": True, "label": "Running…"}

    # Prefer the last matching percent in the tail (most recent line wins).
    tail = text[-4000:]
    percent: float | None = None
    for match in _PERCENT_RE.finditer(tail):
        raw = match.group("pct") or match.group("bar") or match.group("label_pct")
        if raw is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if 0.0 <= value <= 100.0:
            percent = value

    eta_s: float | None = None
    for match in _ETA_RE.finditer(tail):
        hours = int(match.group("h") or 0)
        minutes = int(match.group("m") or 0)
        seconds = int(match.group("s") or 0)
        total = hours * 3600 + minutes * 60 + seconds
        if total > 0:
            eta_s = float(total)

    label = "Running…"
    last_lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    if last_lines:
        candidate = last_lines[-1]
        if len(candidate) > 120:
            candidate = candidate[:117] + "…"
        if _DOWNLOAD_RE.search(candidate) or percent is not None:
            label = candidate
        elif _DOWNLOAD_RE.search(tail):
            # Generic phase label when % is present but last line is noise.
            if re.search(r"download", tail, re.I):
                label = "Downloading…"
            elif re.search(r"install", tail, re.I):
                label = "Installing…"
            elif re.search(r"build|compil", tail, re.I):
                label = "Building…"

    result: dict[str, Any] = {"label": label}
    if percent is not None:
        result["percent"] = percent
        result["indeterminate"] = False
    else:
        result["indeterminate"] = True
    if eta_s is not None:
        result["eta_s"] = eta_s
    return result


async def emit_task_progress(
    *,
    label: str,
    percent: float | int | None = None,
    indeterminate: bool | None = None,
    eta_s: float | int | None = None,
    step: str | None = None,
    step_index: int | None = None,
    steps_total: int | None = None,
    call_id: str | None = None,
) -> None:
    """Push a task_progress update for the current turn, if an emitter is bound."""
    emitter = _EMITTER.get()
    if emitter is None:
        return
    data = build_task_progress_data(
        label=label,
        percent=percent,
        indeterminate=indeterminate,
        eta_s=eta_s,
        step=step,
        step_index=step_index,
        steps_total=steps_total,
        call_id=call_id,
    )
    try:
        await emitter(data)
    except Exception:
        logger.debug("task progress emission failed", exc_info=True)
