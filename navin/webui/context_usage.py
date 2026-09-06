"""Live context usage for one chat session, for the Dev status bar.

The gauge and the legend come from the same Navin prompt profile: the
sections persisted from the peak request of the last turn. When that
profile is missing, a live ``profile_request`` reconstructs the current
prompt. A provider peak without a profile is shown as a total only - it
is never padded into a fake "Other" row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent.prompt_profile import display_buckets
from navin.session.context_usage_meta import last_context_usage_row
from navin.utils.helpers import estimate_message_tokens

_MESSAGE_TOKEN_CAP = 200


def _message_rows(session_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    messages = session_data.get("messages") if isinstance(session_data, dict) else None
    if not isinstance(messages, list):
        return []
    return [m for m in messages if isinstance(m, dict)]


def _bucket_total(buckets: list[dict[str, Any]]) -> int:
    return sum(int(bucket.get("tokens") or 0) for bucket in buckets)


def _live_profile_sections(
    project_path: str | Path | None,
    counted: list[dict[str, Any]],
    *,
    skill_names: list[str] | None,
) -> dict[str, int] | None:
    """Rebuild the current prompt and attribute it. No invented floors."""
    messages: list[dict[str, Any]] = []
    if project_path:
        try:
            from navin.agent.context import ContextBuilder

            root = Path(project_path)
            system = ContextBuilder(root).build_system_prompt(
                skill_names=skill_names,
                workspace=root,
                include_memory_recent_history=False,
            )
            messages.append({"role": "system", "content": system})
        except Exception as exc:
            logger.debug("context builder for usage meter failed: {}", exc)
    messages.extend(counted)
    if not messages:
        return None
    try:
        from navin.agent.prompt_profile import profile_request

        return dict(profile_request(messages, None).sections)
    except Exception as exc:
        logger.debug("live prompt profile failed: {}", exc)
        return None


def _paths_from_message_metadata(meta: dict[str, Any]) -> list[tuple[str, str]]:
    """Return (path, reason) pairs from one turn's metadata."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(path: str, reason: str) -> None:
        rel = (path or "").strip().replace("\\", "/")
        if not rel or rel in seen:
            return
        seen.add(rel)
        found.append((rel, reason))

    raw_open = meta.get("open_files")
    if isinstance(raw_open, (list, tuple)):
        for item in raw_open:
            if isinstance(item, str):
                add(item, "open_file")
            elif isinstance(item, dict) and item.get("path"):
                add(str(item["path"]), "open_file")

    mentions = meta.get("file_mentions")
    if isinstance(mentions, (list, tuple)):
        for item in mentions:
            if isinstance(item, dict) and item.get("path"):
                add(str(item["path"]), "file_mention")
            elif isinstance(item, str):
                add(item, "file_mention")
    return found


def _latest_included_from_session(
    session_data: dict[str, Any] | None,
) -> list[tuple[str, str]]:
    rows = _message_rows(session_data)
    for row in reversed(rows):
        if str(row.get("role") or "") != "user":
            continue
        meta = row.get("metadata")
        if not isinstance(meta, dict):
            continue
        pairs = _paths_from_message_metadata(meta)
        if pairs:
            return pairs
    top_meta = session_data.get("metadata") if isinstance(session_data, dict) else None
    if isinstance(top_meta, dict):
        return _paths_from_message_metadata(top_meta)
    return []


def _rules_included(project_path: str | Path | None) -> list[tuple[str, str]]:
    if not project_path:
        return []
    try:
        from navin.agent.project_rules import list_navin_rules

        rows = list_navin_rules(Path(project_path))
        return [(str(row.get("path") or ""), "project_rules") for row in rows if row.get("path")]
    except Exception:
        return []


def _configured_context_window() -> int:
    from navin.config.loader import load_config

    try:
        return int(load_config().agents.defaults.context_window_tokens or 0)
    except Exception:
        return 0


def _peak_from_row(last: dict[str, Any] | None) -> int | None:
    if not isinstance(last, dict):
        return None
    try:
        peak = int(last.get("peak_prompt_tokens") or last.get("prompt_tokens") or 0)
    except (TypeError, ValueError):
        return None
    return peak if peak > 0 else None


def context_usage_payload(
    session_data: dict[str, Any] | None,
    *,
    project_path: str | Path | None = None,
) -> dict[str, Any]:
    """Attributed prompt fill against the configured window."""
    rows = _message_rows(session_data)
    counted = rows[-_MESSAGE_TOKEN_CAP:] if len(rows) > _MESSAGE_TOKEN_CAP else rows
    message_tokens = 0
    for row in counted:
        try:
            message_tokens += max(0, estimate_message_tokens(row))
        except Exception:
            continue

    top_meta = session_data.get("metadata") if isinstance(session_data, dict) else None
    last = last_context_usage_row(top_meta if isinstance(top_meta, dict) else None)
    peak = _peak_from_row(last)

    window = _configured_context_window()
    if last and last.get("context_window"):
        try:
            window = int(last["context_window"]) or window
        except (TypeError, ValueError):
            pass

    from navin.session.context_usage_meta import last_preload_skills

    skills = last_preload_skills(top_meta if isinstance(top_meta, dict) else None)
    persisted = last.get("sections") if isinstance(last, dict) else None
    buckets = display_buckets(persisted if isinstance(persisted, dict) else None)
    billed_session = None
    if last is not None:
        try:
            billed_session = int(last.get("billed_tokens_session") or 0) or None
        except (TypeError, ValueError):
            billed_session = None

    if buckets:
        tokens = _bucket_total(buckets)
        source = str(last.get("source") or "last_turn") if last else "last_turn"
    elif peak:
        tokens = peak
        source = str(last.get("source") or "last_turn") if last else "last_turn"
    else:
        live = _live_profile_sections(project_path, counted, skill_names=skills)
        buckets = display_buckets(live)
        if buckets:
            tokens = _bucket_total(buckets)
            source = "estimate"
        else:
            tokens = message_tokens
            source = "messages"

    percent: float | None = None
    if window > 0:
        percent = round(min(100.0, 100.0 * tokens / window), 1)

    included_map: dict[str, str] = {}
    for path, reason in _latest_included_from_session(session_data):
        included_map.setdefault(path, reason)
    for path, reason in _rules_included(project_path):
        included_map.setdefault(path, reason)

    included = [
        {"path": path, "reason": reason}
        for path, reason in sorted(included_map.items(), key=lambda item: item[0])
    ]

    return {
        "tokens": tokens,
        "message_tokens": message_tokens,
        "context_window": window or None,
        "percent": percent,
        "messages": len(rows),
        "buckets": buckets,
        "included": included,
        "source": source,
        "peak_prompt_tokens": peak,
        "billed_tokens_session": billed_session,
    }
