# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Respect explicit effort and apply mode defaults only to Auto."""

from __future__ import annotations

_ORDER = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5}
_ALLOWED_OVERRIDES = frozenset(_ORDER) | {"adaptive", "max", "ultra"}

REASONING_EFFORT_METADATA_KEY = "reasoning_effort"


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
    if override == "":
        base = None
    if isinstance(base, str) and base.strip():
        return base.strip().lower()
    mode = (composer_mode or "").strip().lower()
    if mode == "plan":
        return "high"
    # after_verify_failure used to bump the whole next turn into CoT. That
    # made the retry slower than the attempt that already failed.
    _ = after_verify_failure
    return "none"
