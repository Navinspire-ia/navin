# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Route reasoning effort by what the turn is, not a static setting.

The provider layer already normalizes, remaps or strips ``reasoning_effort``
per model (Mistral's high/none vocabulary, DashScope aliases, implicit
reasoners), so the semantic OpenAI vocabulary is safe to adjust here and
degrade gracefully everywhere.

Speed first:

- An explicit per-message override wins (UI "think hard").
- Plan turns still think at least at ``high``: writes are blocked, so the
  plan *is* the work.
- Agent / Ask / Debug: thinking off. GLM and Grok treat low/medium/high as
  the same "on" switch; a config of ``high`` must not burn a minute before
  the first tool. ``xhigh`` / ``max`` / ``adaptive`` stay explicit.
"""

from __future__ import annotations

_ORDER = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5}
_ALLOWED_OVERRIDES = frozenset(_ORDER)
_EXPLICIT_THINK = frozenset({"adaptive", "xhigh", "max"})

REASONING_EFFORT_METADATA_KEY = "reasoning_effort"


def _at_least(effort: str | None, floor: str) -> str:
    if effort is None:
        return floor
    rank = _ORDER.get(effort.lower())
    if rank is None:
        # A vocabulary this policy does not know; do not second-guess it.
        return effort
    return floor if rank < _ORDER[floor] else effort


def adaptive_reasoning_effort(
    base: str | None,
    *,
    composer_mode: str | None = None,
    override: str | None = None,
    after_verify_failure: bool = False,
) -> str | None:
    """The effort this turn should run at, given the configured *base*."""
    if isinstance(override, str) and override.strip().lower() in _ALLOWED_OVERRIDES:
        return override.strip().lower()
    mode = (composer_mode or "").strip().lower()
    if mode == "plan":
        return _at_least(base, "high")
    if isinstance(base, str) and base.strip().lower() in _EXPLICIT_THINK:
        return base.strip().lower()
    # after_verify_failure used to bump the whole next turn into CoT. That
    # made the retry slower than the attempt that already failed.
    _ = after_verify_failure
    return "none"
