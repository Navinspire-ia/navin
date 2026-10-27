# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Index parse worker: ``python -m navin.index.parse_worker``.

The engine parses a first index (thousands of files) in a few of these
instead of one long Python loop: parsing holds the GIL, so threads cannot
share it, and ``multiprocessing`` would re-import the engine's ``__main__`` in
every child (slow, and impossible from a frozen app or a script fed on stdin).
A worker is a bare interpreter that imports only the parser; it reads the
``root`` then one relative path per line on stdin, parses each file itself,
and writes one JSON line per file to stdout, in the index cache's own format
(entry without references, references apart). Only the resulting entries
travel back, never the file contents, and nothing is unpickled.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from navin.index.symbols import FileEntry, read_and_extract


def parse_one(root: str, rel: str) -> dict[str, Any]:
    """Read and index one project file as a JSON-ready row."""
    entry = read_and_extract(Path(root), rel)
    if entry is None:
        return {"rel": rel, "entry": None}
    return {"rel": rel, "entry": entry.to_json(), "refs": entry.refs_to_json()}


def row_to_entry(row: dict[str, Any]) -> tuple[str, FileEntry | None]:
    """The parent's half: rebuild the entry, references included."""
    rel = str(row["rel"])
    raw = row.get("entry")
    if raw is None:
        return rel, None
    entry = FileEntry.from_json(rel, raw)
    refs = row.get("refs")
    if isinstance(refs, dict):
        entry.load_refs_json(refs)
    return rel, entry


def main() -> int:
    lines = sys.stdin.read().split("\n")
    if not lines or not lines[0]:
        return 0
    root = lines[0]
    out = sys.stdout
    for rel in lines[1:]:
        if rel:
            out.write(json.dumps(parse_one(root, rel), ensure_ascii=False))
            out.write("\n")
    out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
