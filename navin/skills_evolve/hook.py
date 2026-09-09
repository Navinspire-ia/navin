# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-turn hook: "the same failure N times" queues a draft job (S2.1).

``create_skills_evolve_hook`` is a turn hook factory registered next to the
episodic journal hook. It answers ``None`` for every turn where the corridor
must stay silent: no project, ephemeral turn, heartbeat, or the flag off.
That path is one ``os.stat``.

When it does return a hook, ``after_run`` looks at the turn's tool events,
counts one strike per distinct failure signature (tool name plus the first
words of the error) and, at ``failure_threshold`` strikes within a day,
calls ``enqueue_draft_job``: a file append and a ``queue.put``. Writing,
examining and promoting happen on the job runner, after the turn.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from navin.agent.hook import AgentHook, AgentRunHookContext, AgentTurnHookContext
from navin.skills_evolve.author import brief_from_failure
from navin.skills_evolve.jobs import enqueue_draft_job
from navin.skills_evolve.settings import evolve_enabled, read_settings

_HEARTBEAT_KEY = "heartbeat"
_WINDOW_S = 24 * 3600
_COOLDOWN_S = 6 * 3600
_MAX_SIGNATURES = 256
_DETAIL_WORDS = 6

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_./-]*")

# (workspace, signature) -> strike timestamps; process-wide, bounded.
_STRIKES: dict[tuple[str, str], list[float]] = {}
_ENQUEUED: dict[tuple[str, str], float] = {}
_LOCK = threading.Lock()


def _is_heartbeat(context: AgentTurnHookContext) -> bool:
    if (context.session_key or "").strip().lower() == _HEARTBEAT_KEY:
        return True
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    return bool(metadata.get(_HEARTBEAT_KEY))


def failure_signature(name: str, detail: str) -> str:
    """Tool name plus the first meaningful words of the error, lowercased."""
    words = [w.lower() for w in _WORD_RE.findall(detail or "")]
    words = [w for w in words if not w.replace(".", "").isdigit()][:_DETAIL_WORDS]
    return f"{name}:{' '.join(words)}" if words else f"{name}:error"


def failure_signatures(tool_events: list[dict[str, str]] | None) -> dict[str, tuple[str, str]]:
    """Distinct failures of one turn: signature -> (tool, detail)."""
    found: dict[str, tuple[str, str]] = {}
    for event in tool_events or ():
        if not isinstance(event, dict) or event.get("status") != "error":
            continue
        name = str(event.get("name") or "").strip()
        if not name:
            continue
        detail = str(event.get("detail") or "")
        found.setdefault(failure_signature(name, detail), (name, detail))
    return found


def record_strikes(
    workspace: Path,
    signatures: dict[str, tuple[str, str]],
    *,
    threshold: int,
    now: float | None = None,
) -> list[tuple[str, str, int]]:
    """Add one strike per signature; return those that just reached the bar."""
    stamp = now if now is not None else time.time()
    key_ws = str(workspace)
    triggered: list[tuple[str, str, int]] = []
    with _LOCK:
        for signature, (tool, detail) in signatures.items():
            key = (key_ws, signature)
            strikes = [t for t in _STRIKES.get(key, []) if stamp - t < _WINDOW_S]
            strikes.append(stamp)
            _STRIKES[key] = strikes
            last = _ENQUEUED.get(key)
            if len(strikes) >= threshold and (last is None or stamp - last > _COOLDOWN_S):
                _ENQUEUED[key] = stamp
                triggered.append((tool, detail, len(strikes)))
        if len(_STRIKES) > _MAX_SIGNATURES:
            for key in sorted(_STRIKES, key=lambda k: _STRIKES[k][-1])[: len(_STRIKES) - _MAX_SIGNATURES]:
                _STRIKES.pop(key, None)
    return triggered


def reset_strikes() -> None:
    with _LOCK:
        _STRIKES.clear()
        _ENQUEUED.clear()


class SkillsEvolveHook(AgentHook):
    """Count failures when the run ends; queue a draft job at the threshold."""

    __slots__ = ("_workspace", "_threshold")

    def __init__(self, *, workspace: Path, threshold: int) -> None:
        super().__init__()
        self._workspace = workspace
        self._threshold = max(1, threshold)

    def _observe(self, context: AgentRunHookContext) -> None:
        signatures = failure_signatures(context.tool_events)
        if not signatures:
            return
        for tool, detail, count in record_strikes(self._workspace, signatures, threshold=self._threshold):
            enqueue_draft_job(self._workspace, brief_from_failure(tool, detail, count), source="repeated_failure")

    async def after_run(self, context: AgentRunHookContext) -> None:
        self._observe(context)

    async def on_error(self, context: AgentRunHookContext) -> None:
        if context.exception is not None:
            self._observe(context)


def create_skills_evolve_hook(context: AgentTurnHookContext) -> AgentHook | None:
    """Turn hook factory: the failure counter, or ``None`` when it must stay off."""
    workspace = context.workspace
    if workspace is None or context.ephemeral or _is_heartbeat(context):
        return None
    if not evolve_enabled(workspace, "draft"):
        return None
    settings = read_settings(workspace)
    return SkillsEvolveHook(workspace=Path(workspace), threshold=settings.failure_threshold)


def hook_state() -> dict[str, Any]:
    with _LOCK:
        return {"signatures": len(_STRIKES), "enqueued": len(_ENQUEUED)}
