# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Collapse long pasted text in the TUI the way Cursor chips a paste.

The composer and the transcript show ``[Pasted Content 11448 chars]``.
The full body stays in a sidecar map and is expanded again before send.
"""

from __future__ import annotations

import re

PASTED_CONTENT_THRESHOLD = 1000
PASTE_PREFIX_KEEP_CHARS = 240
PASTE_TOKEN_RE = re.compile(r"\[Pasted Content (\d+) chars(?: #([A-Za-z0-9]+))?\]")


def pasted_content_label(chars: int, suffix: str | None = None) -> str:
    if suffix:
        return f"[Pasted Content {chars} chars #{suffix}]"
    return f"[Pasted Content {chars} chars]"


def should_collapse_pasted_text(
    text: str,
    *,
    threshold: int = PASTED_CONTENT_THRESHOLD,
) -> bool:
    return len(text or "") > threshold


def allocate_paste_token(chars: int, existing: dict[str, str]) -> str:
    base = pasted_content_label(chars)
    if base not in existing:
        return base
    index = 2
    while True:
        token = pasted_content_label(chars, str(index))
        if token not in existing:
            return token
        index += 1


def expand_pasted_content(display: str, pastes: dict[str, str] | None) -> str:
    if not display or not pastes:
        return display or ""

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return pastes.get(token, token)

    return PASTE_TOKEN_RE.sub(_replace, display)


def split_long_user_text(
    text: str,
    *,
    threshold: int = PASTED_CONTENT_THRESHOLD,
    prefix_limit: int = PASTE_PREFIX_KEEP_CHARS,
) -> tuple[str, str | None]:
    """Keep a short leading prompt; chip the long remainder.

    Returns ``(prefix_or_full, collapsed_rest_or_none)``.
    """
    raw = text or ""
    if len(raw) <= threshold:
        return raw, None
    paragraph, _sep, rest = raw.partition("\n\n")
    if not rest:
        paragraph, _sep, rest = raw.partition("\n")
    if rest and len(paragraph) <= prefix_limit and len(rest) > threshold:
        return paragraph, rest
    return "", raw


def collapse_text_for_composer(
    text: str,
    pastes: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Turn an already-expanded blob back into a chip plus a paste map."""
    store = dict(pastes or {})
    raw = text or ""
    if PASTE_TOKEN_RE.search(raw):
        return raw, store
    if not should_collapse_pasted_text(raw):
        return raw, store
    prefix, rest = split_long_user_text(raw)
    body = rest if rest is not None else raw
    token = allocate_paste_token(len(body), store)
    store[token] = body
    if prefix:
        return f"{prefix}\n{token}", store
    return token, store
