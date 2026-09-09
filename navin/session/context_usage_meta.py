# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Session metadata keys for honest context-window metering.

The Dev footer used to count only persisted chat messages, which ignores the
system prompt, preloaded skills, and tool schemas that dominate real prompts.
Turns persist the last measured peak here so the UI and consolidator agree.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

LAST_CONTEXT_USAGE_KEY = "_last_context_usage"
LAST_PRELOAD_SKILLS_KEY = "_last_preload_skills"


def _positive_int(raw: Any) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _positive_int_map(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, int] = {}
    for key, value in raw.items():
        tokens = _positive_int(value)
        name = str(key).strip()
        if tokens and name:
            cleaned[name] = tokens
    return cleaned


def persist_last_context_usage(
    metadata: dict[str, Any],
    usage: dict[str, Any] | None,
    *,
    context_window_tokens: int,
    model: str | None = None,
    sections: dict[str, Any] | None = None,
    tool_count: int | None = None,
) -> dict[str, Any] | None:
    """Merge turn usage into ``metadata``; return the stored row or None."""
    if not isinstance(metadata, dict) or not usage:
        return None
    peak = int(usage.get("peak_prompt_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    # prompt_tokens on the runner is the SUM across iterations (billing).
    # Window fill must use the peak single-request prompt size.
    fill = peak if peak > 0 else prompt
    if fill <= 0 and completion <= 0:
        return None
    billed_turn = max(0, prompt) + max(0, completion)
    if fill <= 0:
        fill = billed_turn
    prev = metadata.get(LAST_CONTEXT_USAGE_KEY)
    prev_billed = 0
    if isinstance(prev, dict):
        try:
            prev_billed = int(prev.get("billed_tokens_session") or 0)
        except (TypeError, ValueError):
            prev_billed = 0
    estimated = bool(usage.get("estimated_tokens"))
    row = {
        "prompt_tokens": fill,
        "peak_prompt_tokens": fill,
        "billed_tokens_turn": billed_turn,
        "billed_tokens_session": prev_billed + billed_turn,
        "context_window": int(context_window_tokens or 0) or None,
        "source": "estimate" if estimated else "provider",
        "model": (model or "").strip() or None,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    cleaned = _positive_int_map(sections)
    if cleaned:
        row["sections"] = cleaned
    elif isinstance(prev, dict):
        kept = _positive_int_map(prev.get("sections"))
        if kept:
            row["sections"] = kept
    stored_tools = _positive_int(tool_count)
    if stored_tools is None and isinstance(prev, dict):
        stored_tools = _positive_int(prev.get("tool_count"))
    if stored_tools:
        row["tool_count"] = stored_tools
    metadata[LAST_CONTEXT_USAGE_KEY] = row
    return row


def persist_last_preload_skills(
    metadata: dict[str, Any],
    skill_names: list[str] | None,
) -> None:
    """Remember Active Skills names so token probes match real turns."""
    if not isinstance(metadata, dict):
        return
    names = [str(n).strip() for n in (skill_names or []) if str(n).strip()]
    if names:
        metadata[LAST_PRELOAD_SKILLS_KEY] = names
    else:
        metadata.pop(LAST_PRELOAD_SKILLS_KEY, None)


def last_preload_skills(metadata: dict[str, Any] | None) -> list[str] | None:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get(LAST_PRELOAD_SKILLS_KEY)
    if not isinstance(raw, list):
        return None
    names = [str(n).strip() for n in raw if str(n).strip()]
    return names or None


def last_context_usage_row(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    row = metadata.get(LAST_CONTEXT_USAGE_KEY)
    return dict(row) if isinstance(row, dict) else None
