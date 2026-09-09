# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Lexical lookup inside the durable memory note (``MEMORY.md``).

Recall spans two layers: what happened (episodes) and what Navin decided to
keep (MEMORY.md, curated by Dream and consolidation). This module only reads
the note, bounded in bytes, and never touches the file.
"""

from __future__ import annotations

import re
from pathlib import Path

from navin.workspace_layout import memory_file

MAX_NOTE_BYTES = 256 * 1024
MAX_SNIPPET_CHARS = 400

_BLOCK_SPLIT = re.compile(r"\n\s*\n|\n(?=\s*[-*] )|\n(?=#+ )")
_WS_RE = re.compile(r"\s+")


def _blocks(text: str) -> list[str]:
    out: list[str] = []
    for block in _BLOCK_SPLIT.split(text):
        block = _WS_RE.sub(" ", block).strip()
        if block and not block.startswith("#"):
            out.append(block)
    return out


def search_memory_notes(
    workspace: Path | str,
    tokens: list[str],
    *,
    limit: int = 3,
    max_bytes: int = MAX_NOTE_BYTES,
) -> list[tuple[int, str]]:
    """Top ``limit`` MEMORY.md blocks by distinct-token overlap, best first."""
    if not tokens:
        return []
    path = memory_file(workspace)
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_bytes)
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    scored: list[tuple[int, int, str]] = []
    for index, block in enumerate(_blocks(text)):
        lowered = block.lower()
        score = sum(1 for token in tokens if token in lowered)
        if score > 0:
            snippet = block if len(block) <= MAX_SNIPPET_CHARS else block[: MAX_SNIPPET_CHARS - 3] + "..."
            scored.append((score, -index, snippet))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [(score, snippet) for score, _index, snippet in scored[: max(1, limit)]]
