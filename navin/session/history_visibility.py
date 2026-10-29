# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Visibility helpers for persisted session history messages."""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from navin.session.automation_turns import is_automation_history_message

HIDDEN_HISTORY_META = "_hidden_history"


def _has_hidden_history_marker(message: Mapping[str, Any] | None) -> bool:
    if not message:
        return False
    marker = message.get(HIDDEN_HISTORY_META)
    return marker is True or isinstance(marker, Mapping)


@lru_cache(maxsize=1)
def _runtime_prompt_heads() -> tuple[str, ...]:
    """Openings of the nudges the agent loop sends itself as "user" turns
    (scope reminder, keep going, verify, finalize...). They are saved with the
    turn but the user never typed them."""
    from navin.utils import runtime

    heads = [
        value[:60]
        for name, value in vars(runtime).items()
        if name.endswith("_PROMPT") and isinstance(value, str) and len(value) >= 40
    ]
    heads.append("Scope reminder: the request names these targets:")
    heads.append("The previous model response attempted to call tools, but every tool call")
    # Reminders built by functions, not module constants. Without them the
    # handover note carried "Exploration check: ..." as if the user wrote it.
    for name, value in vars(runtime).items():
        if not (name.startswith("build_") and name.endswith("_message") and callable(value)):
            continue
        try:
            built = value()
        except TypeError:
            try:
                built = value(1)
            except Exception:  # noqa: BLE001
                continue
        except Exception:  # noqa: BLE001
            continue
        text = built.get("content") if isinstance(built, dict) else None
        head = _fixed_head(text)
        if head:
            heads.append(head)
    try:
        from navin.agent.scope_anchor import exploration_budget_message

        heads.append(_fixed_head(exploration_budget_message(8)["content"]) or "Exploration check:")
    except Exception:  # noqa: BLE001
        heads.append("Exploration check:")
    try:
        from navin.session.turn_continuation import (
            _NO_PROGRESS_RESUME_PROMPT,
            _board_continuation_prompt,
            _turn_budget_continuation_prompt,
        )

        heads.append(_board_continuation_prompt()[:60])
        heads.append(_turn_budget_continuation_prompt()[:60])
        heads.append(_NO_PROGRESS_RESUME_PROMPT[:60])
    except Exception:  # noqa: BLE001
        pass
    heads.append("You were reminded ")
    return tuple(h for h in heads if h)


_NUMBERED_REMINDER_RE = re.compile(r"^Reminder \d+: the previous instruction was ignored")


def _fixed_head(text: Any) -> str:
    """The opening of a generated reminder, up to its first variable number."""
    if not isinstance(text, str):
        return ""
    match = re.search(r"\d", text)
    head = text[: match.start()] if match else text
    head = head[:60]
    return head if len(head) >= 20 else ""


def is_runtime_nudge_message(message: Mapping[str, Any] | None) -> bool:
    """True for an agent-loop nudge saved as a user message."""
    if not message or message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    text = content.lstrip()
    if _NUMBERED_REMINDER_RE.match(text):
        return True
    return any(text.startswith(head) for head in _runtime_prompt_heads())


def is_hidden_history_message(message: Mapping[str, Any] | None) -> bool:
    """True for persisted messages that should not be shown as chat turns."""
    return (
        _has_hidden_history_marker(message)
        or is_automation_history_message(message)
        or is_runtime_nudge_message(message)
    )
