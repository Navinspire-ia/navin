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

import asyncio
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout
from loguru import logger

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
        from navin.skills_evolve.experience import safe_detail
        detail = safe_detail(str(event.get("detail") or ""))
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
    from navin.skills_evolve.drafts import _write_atomic
    from navin.skills_evolve.paths import drafts_dir
    folder = drafts_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "experience.json"
    with _LOCK, FileLock(str(path) + ".lock", timeout=2):
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            saved = {}
        for signature, value in (saved.items() if isinstance(saved, dict) else []):
            if isinstance(value, dict):
                values = value.get("strikes", [])
                _STRIKES[(key_ws, signature)] = [t for t in values if isinstance(t, (int, float)) and 0 <= stamp - t < _WINDOW_S] if isinstance(values, list) else []
                if isinstance(value.get("proposed_at"), (int, float)):
                    _ENQUEUED[(key_ws, signature)] = value["proposed_at"]
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
            for key in sorted(_STRIKES, key=lambda k: (_STRIKES[k] or [0])[-1])[: len(_STRIKES) - _MAX_SIGNATURES]:
                _STRIKES.pop(key, None)
                _ENQUEUED.pop(key, None)
        _write_atomic(path, json.dumps({key[1]: {"strikes": value, "proposed_at": _ENQUEUED.get(key)}
                                       for key, value in _STRIKES.items() if key[0] == key_ws}))
    return triggered


def reset_strikes() -> None:
    with _LOCK:
        _STRIKES.clear()
        _ENQUEUED.clear()


class SkillsEvolveHook(AgentHook):
    """Count failures when the run ends; queue a draft job at the threshold."""

    __slots__ = ("_workspace", "_threshold", "_module")

    def __init__(self, *, workspace: Path, threshold: int, module: str | None = None) -> None:
        super().__init__()
        self._workspace = workspace
        self._threshold = max(1, threshold)
        self._module = module

    def _observe(self, context: AgentRunHookContext) -> None:
        if context.stop_reason in {"cancelled", "stop_requested"} or context.had_injections:
            return
        signatures = failure_signatures(context.tool_events)
        if not signatures:
            self._observe_efficiency(context)
            return
        events = [row for row in context.tool_events or [] if isinstance(row, dict)]
        for signature, (tool, detail) in signatures.items():
            last_error = max(i for i, row in enumerate(events) if row.get("name") == tool and row.get("status") == "error")
            recovered = context.stop_reason == "completed" and any(
                row.get("name") == tool and row.get("status") == "ok" for row in events[last_error + 1:])
            scoped = f"{self._module or 'shared'}:{signature}"
            triggered = record_strikes(self._workspace, {scoped: (tool, detail)}, threshold=1 if recovered else self._threshold)
            if not triggered:
                continue
            _, _, count = triggered[0]
            brief = brief_from_failure(tool, detail, count)
            brief.module = self._module
            if self._module:
                from navin.skills_evolve.author import slugify
                brief.name = slugify(f"{self._module}-{brief.name}", limit=48)
            if recovered:
                brief.kind = "observed_recovery"
                brief.hints = ["A later tool sequence completed after this failure: " + " -> ".join(
                    str(row.get("name")) for row in events[last_error + 1:] if row.get("status") == "ok")]
            enqueue_draft_job(self._workspace, brief, source=brief.kind)
            from navin.skills_evolve.experience import remember
            remember(self._workspace, brief.kind, module=self._module, name=brief.name, detail=detail)

    def _observe_efficiency(self, context):
        if context.stop_reason != "completed":
            return
        from collections import Counter

        from navin.skills_evolve.author import DraftBrief, family_for_tool, slugify
        events = [row for row in context.tool_events or [] if isinstance(row, dict) and row.get("status") == "ok"]
        counts = Counter(str(row.get("name") or "") for row in events)
        if len(events) < 12 or not counts:
            return
        tool, count = counts.most_common(1)[0]
        if count < 8:
            return
        detail = f"A completed workflow used {len(events)} tool calls, including {count} calls to {tool}."
        signature = f"{self._module or 'shared'}:efficiency:{tool}"
        if not record_strikes(self._workspace, {signature: (tool, detail)}, threshold=1):
            return
        brief = DraftBrief(slugify(f"{self._module or 'shared'}-optimize-{tool}", limit=48),
                           "Reduce redundant tool calls while preserving verified results.", kind="observed_efficiency",
                           family=family_for_tool(tool), tool=tool, detail=detail, module=self._module)
        enqueue_draft_job(self._workspace, brief, source=brief.kind)

    async def after_run(self, context: AgentRunHookContext) -> None:
        try:
            await asyncio.to_thread(self._observe, context)
        except (OSError, ValueError, TypeError, Timeout) as exc:
            logger.debug("Skill experience skipped: {}", type(exc).__name__)

    async def on_error(self, context: AgentRunHookContext) -> None:
        if context.exception is not None:
            await self.after_run(context)


def create_skills_evolve_hook(context: AgentTurnHookContext) -> AgentHook | None:
    """Turn hook factory: the failure counter, or ``None`` when it must stay off."""
    workspace = context.workspace
    if workspace is None or context.ephemeral or _is_heartbeat(context):
        return None
    if not evolve_enabled(workspace, "draft"):
        return None
    settings = read_settings(workspace)
    from navin.skills_evolve.jobs import resume_jobs
    resume_jobs(Path(workspace))
    from navin.command.modules import normalize_product_module
    return SkillsEvolveHook(workspace=Path(workspace), threshold=settings.failure_threshold,
                           module=normalize_product_module(context.metadata.get("product_module")))


def hook_state() -> dict[str, Any]:
    with _LOCK:
        return {"signatures": len(_STRIKES), "enqueued": len(_ENQUEUED)}
