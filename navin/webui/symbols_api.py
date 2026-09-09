# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Symbol lookup endpoint backing editor autocompletion and quick-open.

Serves the code index to the Dev workbench: the editor asks for symbols
matching a prefix and gets project-wide completions with kind, signature and
origin, so completion is not limited to words already visible in the buffer.
"""

from __future__ import annotations

import re
from typing import Any

from navin.security.workspace_access import WorkspaceScope
from navin.utils.path import normalize_relative_path

_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


class SymbolsError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def symbols_payload(
    scope: WorkspaceScope,
    query: str,
    *,
    path: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Return symbol completions for ``query`` within the project.

    Symbols defined in ``path`` (the file being edited) and symbols it imports
    are ranked first, since those are the ones actually in scope.
    """
    root = scope.project_path
    if not root.is_dir():
        raise SymbolsError(404, "project directory not found")

    from navin.index import get_index

    capped = max(1, min(limit, _MAX_LIMIT))
    index = get_index(root)
    index.ensure()

    local_paths: set[str] = set()
    if path:
        cleaned = normalize_relative_path(path)
        resolved = cleaned if cleaned in index.entries else None
        if resolved is None:
            candidates = index.candidates_for(cleaned)
            resolved = candidates[0] if len(candidates) == 1 else None
        if resolved is not None:
            local_paths = {resolved, *index.dependencies(resolved)}

    term = (query or "").strip()

    def as_item(rel: str, symbol: Any, in_scope: bool) -> dict[str, Any]:
        return {
            "name": symbol.name,
            "qualname": symbol.qualname or symbol.name,
            "kind": symbol.kind,
            "detail": symbol.signature or symbol.doc,
            "path": rel,
            "line": symbol.line,
            "in_scope": in_scope,
        }

    # In-scope symbols are gathered before any truncation, otherwise a common
    # name elsewhere in the project can push the local definitions out of the
    # result set entirely.
    scored: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    seen: set[tuple[str, str, int]] = set()

    def consider(rel: str, symbol: Any, in_scope: bool) -> None:
        key = (rel, symbol.name, symbol.line)
        if key in seen:
            return
        if term:
            rank = _match_rank(symbol.name, term)
            if rank is None:
                return
        else:
            # No prefix typed: only in-scope, public symbols are useful.
            if not in_scope or not (symbol.exported or rel == path):
                return
            rank = 0
        seen.add(key)
        scored.append(((0 if in_scope else 1, rank, symbol.name.lower()), as_item(rel, symbol, in_scope)))

    for rel in sorted(local_paths):
        entry = index.entries.get(rel)
        if entry is None:
            continue
        for symbol in entry.symbols:
            consider(rel, symbol, True)

    if term:
        for rel, entry in index.entries.items():
            if rel in local_paths:
                continue
            for symbol in entry.symbols:
                consider(rel, symbol, False)

    scored.sort(key=lambda item: item[0])
    items = [item for _, item in scored[:capped]]
    return {"query": term, "items": items, "total": len(items)}


def _resolve_project_rel(scope: WorkspaceScope, path: str | None) -> str:
    """Absolute editor paths or project-relative keys → index path."""
    from pathlib import Path

    cleaned = (path or "").strip()
    if not cleaned:
        raise SymbolsError(400, "missing path")
    root = scope.project_path.resolve(strict=False)
    candidate = Path(cleaned).expanduser()
    if candidate.is_absolute():
        try:
            return candidate.resolve(strict=False).relative_to(root).as_posix()
        except ValueError as exc:
            raise SymbolsError(400, "path outside project") from exc
    rel = normalize_relative_path(cleaned)
    if not rel or ".." in Path(rel).parts:
        raise SymbolsError(400, "invalid path")
    return rel


def outline_payload(
    scope: WorkspaceScope,
    *,
    path: str,
    limit: int = _MAX_LIMIT,
) -> dict[str, Any]:
    """Return document symbols for one file (Outline panel), ordered by line."""
    root = scope.project_path
    if not root.is_dir():
        raise SymbolsError(404, "project directory not found")
    cleaned = _resolve_project_rel(scope, path)

    from navin.index import get_index

    index = get_index(root)
    index.ensure()
    resolved = index.outline(cleaned)
    if resolved is None:
        return {"path": cleaned, "items": [], "total": 0, "source": "index"}
    rel, symbols = resolved
    capped = max(1, min(limit, _MAX_LIMIT))
    items: list[dict[str, Any]] = []
    for symbol in sorted(symbols, key=lambda s: (s.line, s.name.lower())):
        items.append(
            {
                "name": symbol.name,
                "qualname": symbol.qualname or symbol.name,
                "kind": symbol.kind,
                "detail": symbol.signature or symbol.doc,
                "path": rel,
                "line": symbol.line,
                "in_scope": True,
            }
        )
        if len(items) >= capped:
            break
    return {"path": rel, "items": items, "total": len(items), "source": "index"}


def goto_definition_payload(
    scope: WorkspaceScope,
    *,
    symbol: str,
    path: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Resolve ``symbol`` to definition locations for editor F12 / Ctrl-click."""
    root = scope.project_path
    if not root.is_dir():
        raise SymbolsError(404, "project directory not found")
    name = (symbol or "").strip()
    if not name:
        raise SymbolsError(400, "missing symbol")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise SymbolsError(400, "invalid symbol")

    from navin.index import get_index

    index = get_index(root)
    index.ensure()
    capped = max(1, min(limit, _MAX_LIMIT))
    locations = index.definition(name)
    items: list[dict[str, Any]] = []
    local_first: list[dict[str, Any]] = []
    cleaned_path = normalize_relative_path(path) if path else ""
    for loc in locations:
        row = {
            "name": name,
            "path": loc.path,
            "line": loc.line,
            "kind": getattr(loc, "kind", "") or "",
        }
        if cleaned_path and loc.path == cleaned_path:
            local_first.append(row)
        else:
            items.append(row)
    ordered = [*local_first, *items][:capped]
    return {"symbol": name, "items": ordered, "total": len(ordered)}


def _match_rank(name: str, term: str) -> int | None:
    """Completion relevance: lower is better, ``None`` means no match."""
    if name == term:
        return 0
    if name.startswith(term):
        return 1
    lowered_name, lowered_term = name.lower(), term.lower()
    if lowered_name == lowered_term:
        return 2
    if lowered_name.startswith(lowered_term):
        return 3
    if lowered_term in lowered_name:
        return 4
    return None
