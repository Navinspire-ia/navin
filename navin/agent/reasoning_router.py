# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Honor configured reasoning; choose a policy only for automatic effort.

Provider adapters translate the effort to their native reasoning controls.
Tool follow-ups must retain an explicit choice for the entire turn.
"""

from __future__ import annotations


def route_reasoning_effort(
    base: str | None,
    *,
    composer_mode: str | None = None,
    escalations: int = 0,
    tool_followup: bool = False,
    empty_retry: bool = False,
) -> str | None:
    """Effective reasoning effort for the next model request."""
    if isinstance(base, str) and base.strip():
        return base.strip().lower()
    if empty_retry:
        return "none"

    mode = (composer_mode or "").strip().lower()
    if mode == "plan":
        if tool_followup and escalations <= 0:
            return "none"
        return "high"

    # Agent and every other mode: execute. Do not sit in CoT.
    return "none"
