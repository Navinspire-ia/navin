# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Honor configured reasoning; choose a policy only for automatic effort.

Provider adapters translate the effort to their native reasoning controls.
Tool follow-ups must retain an explicit choice for the entire turn.
"""

from __future__ import annotations

# Rank of each effort; "adaptive" and "max" sit at the top, an unknown name is
# never lowered to (a typo in the setting must not change behaviour).
_RANK = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "adaptive": 4, "xhigh": 5, "max": 6}


def _lighter(configured: str, followup: str | None) -> str:
    """The follow-up effort when it is lower than the configured one."""
    wanted = (followup or "").strip().lower()
    if wanted in _RANK and _RANK.get(configured, 99) > _RANK[wanted]:
        return wanted
    return configured


def route_reasoning_effort(
    base: str | None,
    *,
    composer_mode: str | None = None,
    escalations: int = 0,
    conversational: bool = False,
    followup_effort: str | None = None,
    tool_followup: bool = False,
    empty_retry: bool = False,
) -> str | None:
    """Effective reasoning effort for the next model request."""
    if conversational:
        # A greeting has nothing to reason about: "hello" at effort high spent
        # 17 s thinking before answering in one line.
        return "none"
    if isinstance(base, str) and base.strip():
        configured = base.strip().lower()
        # Steps that only read a tool result need less thought than the first
        # step or the retry after a red verify, which keep the configured effort.
        if empty_retry:
            # The configured effort just produced a blank reply; repeating it
            # at "max" spent another hour thinking and came back blank again,
            # and "medium" still burned the output budget on reasoning. Low is
            # the level that reliably answers, so the retry uses it. A blank
            # reply at low or below would come back byte-identical, so that
            # retry drops thinking altogether.
            if _RANK.get(configured, 99) > _RANK["low"]:
                return "low"
            return "none"
        if followup_effort and tool_followup and escalations <= 0:
            return _lighter(configured, followup_effort)
        return configured
    if empty_retry:
        return "none"

    mode = (composer_mode or "").strip().lower()
    if mode == "plan":
        if tool_followup and escalations <= 0:
            return "none"
        return "high"

    # Agent and every other mode: execute. Do not sit in CoT.
    return "none"


# Output budgets under which heavy thinking leaves no room for the answer.
_TIGHT_OUTPUT_TOKENS = 32_000
_VERY_TIGHT_OUTPUT_TOKENS = 12_000


def fit_effort_to_output(effort: str | None, max_tokens: object) -> str | None:
    """Lower *effort* when the output budget cannot hold that much thinking.

    Thinking is billed against ``max_tokens``. With the prompt near the window
    the budget shrinks to a few thousand tokens, and Claude at "max" spent all
    17k of them reasoning: finish_reason "length", no text, then a blank-reply
    retry that did the same.
    """
    if not isinstance(effort, str) or not isinstance(max_tokens, int) or max_tokens <= 0:
        return effort
    rank = _RANK.get(effort.strip().lower())
    if rank is None:
        return effort
    if max_tokens < _VERY_TIGHT_OUTPUT_TOKENS and rank > _RANK["low"]:
        return "low"
    if max_tokens < _TIGHT_OUTPUT_TOKENS and rank > _RANK["medium"]:
        return "medium"
    return effort
