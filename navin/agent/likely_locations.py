# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Definition sites for the code names a request mentions, before the first step.

A question like "where is ``model_pick_items`` built" used to cost the agent a
search step, a read step and a model round trip for each, 5 to 11 s apiece.
The code index already knows where those names are defined, so the answer is
put in front of the model with the request. It is a hint: it never blocks, it
only runs when the index is already loaded (a cold build is not worth the wait
here), and it stays silent when the request names no code identifier.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

MAX_SYMBOLS = 4
MAX_LINES = 8
_PER_SYMBOL = 3
_BUDGET_S = 2.5
_LOCK_WAIT_S = 0.3

_EXT = r"(?:py|ts|tsx|js|jsx|json|md|yml|yaml|toml|sql|rs|go|css|html)"
_NOT_FILE = rf"(?!\.{_EXT}\b)"
_BACKTICK_RE = re.compile(r"`([A-Za-z_][\w.]{2,80})(?:\(\))?`")
_SNAKE_RE = re.compile(rf"(?<![\w./-])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![\w/-]){_NOT_FILE}")
_UPPER_SNAKE_RE = re.compile(rf"(?<![\w./-])([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)(?![\w/-]){_NOT_FILE}")
_CAMEL_RE = re.compile(rf"(?<![\w./-])([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+|[a-z]+(?:[A-Z][a-z0-9]+)+)(?![\w/-]){_NOT_FILE}")
_FILE_SUFFIX_RE = re.compile(r"\.(?:py|ts|tsx|js|jsx|json|md|yml|yaml|toml|sql|rs|go|css|html)$", re.I)


def candidate_symbols(text: str | None) -> list[str]:
    """Code-looking names in ``text``, in order of appearance, at most MAX_SYMBOLS."""
    if not text:
        return []
    hits: list[tuple[int, str]] = []
    for pattern in (_BACKTICK_RE, _SNAKE_RE, _UPPER_SNAKE_RE, _CAMEL_RE):
        for match in pattern.finditer(text):
            hits.append((match.start(1), match.group(1)))
    found: list[str] = []
    seen: set[str] = set()
    for _pos, raw in sorted(hits):
        token = raw.strip(".")
        # `pkg.module.Name` is looked up by its last part, which is what the
        # index stores; a bare file name is a path, not a symbol.
        token = token.rsplit(".", 1)[-1] if "." in token and not _FILE_SUFFIX_RE.search(token) else token
        if len(token) < 5 or _FILE_SUFFIX_RE.search(token) or token.lower() in seen:
            continue
        seen.add(token.lower())
        found.append(token)
        if len(found) >= MAX_SYMBOLS:
            break
    return found


def likely_location_lines(index: Any, symbols: Sequence[str]) -> list[str]:
    """Rendered definition sites for ``symbols``; caller holds the index lock."""
    lines: list[str] = []
    for symbol in symbols:
        try:
            locations = index.definition(symbol)
        except Exception:  # noqa: BLE001 - a hint must never break the turn
            continue
        for loc in list(locations or ())[:_PER_SYMBOL]:
            lines.append(f"  {loc.render()}")
            if len(lines) >= MAX_LINES:
                return lines
    return lines


def _lookup(root: Path, symbols: Sequence[str]) -> list[str]:
    from navin.index import get_index

    index = get_index(root)
    # A cold load is about 2 s once (the on-disk cache is reused); a request
    # that names code is worth it, and a slower one finishes in the background.
    index.ensure()
    lock = index._lifecycle_lock
    if not lock.acquire(timeout=_LOCK_WAIT_S):
        return []
    try:
        return likely_location_lines(index, symbols)
    finally:
        lock.release()


async def likely_locations_context_provider(
    request: RequestContext,
) -> RuntimeContextBlock | None:
    """Definition sites of the code names the request mentions, if already indexed."""
    root = request.workspace
    symbols = candidate_symbols(request.original_user_text)
    if root is None or not symbols:
        return None
    try:
        lines = await asyncio.wait_for(asyncio.to_thread(_lookup, Path(root), symbols), _BUDGET_S)
    except (asyncio.TimeoutError, OSError):
        return None
    if not lines:
        return None
    content = wrap_runtime_context_lines([
        "Definition sites from the code index for names in the request "
        "(open these directly; no need to grep for them first):",
        *lines,
    ])
    if not content:
        return None
    return RuntimeContextBlock(source="likely_locations", content=content)
