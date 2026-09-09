# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Route reasoning effort per request instead of forwarding config verbatim.

Speed is the product. GLM / Grok / Z.AI thinking is on/off, not a ladder:
``low`` / ``medium`` / ``high`` all turn a full chain of thought on, which
is how a /forge turn spent an hour before the first useful edit.

Signals:

* Agent, Ask, Debug, unset: thinking off (``none``), including the first
  call. A config of ``high`` does not win here.
* Plan: design work still thinks (floor ``high``), then off after tools.
* Tool follow-ups: always off, unless an explicit ``xhigh`` / ``max`` is
  still on the first call of that request (follow-ups still drop it).
* Empty-content retries: off, so a blank CoT dump cannot loop.
* ``adaptive`` / ``xhigh`` / ``max``: explicit "think hard" from the UI;
  kept on the first call only.
"""

from __future__ import annotations

# The routable band. Values outside it are explicit user choices.
_LADDER: tuple[str, ...] = ("low", "medium", "high")
_PLAN_FLOOR_INDEX = 2  # high
_EXPLICIT_THINK = frozenset({"adaptive", "xhigh", "max"})


def route_reasoning_effort(
    base: str | None,
    *,
    composer_mode: str | None = None,
    escalations: int = 0,
    tool_followup: bool = False,
    empty_retry: bool = False,
) -> str | None:
    """Effective reasoning effort for the next model request."""
    if empty_retry and base not in ("xhigh", "max"):
        return "none"
    if base == "none":
        return "none"
    if base in _EXPLICIT_THINK:
        if tool_followup:
            return "none"
        return base

    mode = (composer_mode or "").strip().lower()
    if mode == "plan":
        if tool_followup and escalations <= 0:
            return "none"
        index = _LADDER.index(base) if base in _LADDER else None
        index = max(index if index is not None else 0, _PLAN_FLOOR_INDEX)
        if escalations > 0:
            index = min(index + escalations, len(_LADDER) - 1)
        return _LADDER[index]

    # Agent and every other mode: execute. Do not sit in CoT.
    return "none"
