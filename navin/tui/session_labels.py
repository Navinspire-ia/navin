# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Human session names for the TUI picker (Windows, Linux, macOS)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from loguru import logger

from navin.tui.modes import display_user_text
from navin.tui.runtime import split_session_id

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Assistant leftovers must not become the chat name.
_JUNK_TITLE_RE = re.compile(
    r"(?is)("
    r"you ended the turn|without calling any tools|sans outil|"
    r"runtime context|system reminder|skills for this mission"
    r")"
)


def format_session_when(raw: str) -> str:
    """``2026-09-09T15:00:00`` -> ``9 Sep 15:00``. Same on every OS."""
    text = (raw or "").replace("T", " ").strip()
    if len(text) < 10 or text[4] != "-" or text[7] != "-":
        return text[:16] if text else ""
    try:
        day = int(text[8:10])
        month = int(text[5:7])
    except ValueError:
        return text[:16]
    if month < 1 or month > 12:
        return text[:16]
    clock = text[11:16] if len(text) >= 16 and text[11:16] != "00:00" else ""
    label = f"{day} {_MONTHS[month - 1]}"
    return f"{label} {clock}".strip()


def compact_session_age(raw: str, now: datetime | None = None) -> str:
    """``updated_at`` -> ``now``, ``5m``, ``2h``, ``3d``, ``2w``, ``4mo``, ``1y``.

    Same steps as the desktop sidebar (``compactRelativeTime``): hours under a
    day, then days. Empty when the date cannot be read.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        # An unreadable date shows no age rather than a wrong one.
        logger.debug("session date {!r} unreadable: {}", text, exc)
        return ""
    if now is None:
        now = datetime.now(when.tzinfo) if when.tzinfo else datetime.now()
    elif (now.tzinfo is None) != (when.tzinfo is None):
        when = when.replace(tzinfo=now.tzinfo)
    seconds = max(0, round((now - when).total_seconds()))
    if seconds < 60:
        return "now"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = round(minutes / 60)
    if hours < 24:
        return f"{hours}h"
    days = round(hours / 24)
    if days < 14:
        return f"{days}d"
    weeks = round(days / 7)
    if weeks < 8:
        return f"{weeks}w"
    months = round(days / 30)
    if months < 24:
        return f"{months}mo"
    return f"{round(days / 365)}y"


def session_origin(key: str) -> str:
    """Quiet origin, never the raw ``cli:direct`` id."""
    channel, rest = split_session_id(key)
    if channel == "cli":
        return "This window" if rest in {"direct", ""} else "CLI"
    if channel == "websocket":
        return "App"
    if channel == "sdk":
        return "SDK"
    if channel:
        return channel.upper()
    return "Chat"


def _usable_title(text: str) -> bool:
    compact = " ".join((text or "").split())
    if len(compact) < 3:
        return False
    if _JUNK_TITLE_RE.search(compact):
        return False
    if compact.startswith(("cli:", "sdk:", "websocket:")):
        return False
    return True


def _clean_preview(text: str) -> str:
    """First user words, without runtime context or /forge prefixes."""
    from navin.cognition.episodes import strip_runtime_context
    from navin.session.webui_turns import title_source_from_user_text

    cleaned = display_user_text(strip_runtime_context(text or ""))
    focused = title_source_from_user_text(cleaned)
    return focused or cleaned


def session_display_title(row: dict[str, Any]) -> str:
    """Name a chat from its title, first user line, or Untitled chat."""
    from navin.session.webui_turns import limit_title_words

    title = str(row.get("title") or "").strip()
    if _usable_title(title):
        return limit_title_words(title)
    preview = _clean_preview(str(row.get("preview") or ""))
    if _usable_title(preview):
        return limit_title_words(preview)
    words = preview.split()
    if words and not _JUNK_TITLE_RE.search(preview):
        return limit_title_words(preview)
    return "Untitled chat"
