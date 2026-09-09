# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Fuzzy file, folder, and symbol lookup backing @-mentions in the composer.

Serves the code index rather than walking the tree, so results honor
``.gitignore`` exactly and cost nothing after the first refresh. Matching is
deliberately forgiving: a basename prefix wins, but a subsequence still
matches, which is what makes ``dwb`` find ``DevWorkbench.tsx``.

Symbols share the ranking with paths instead of living in their own list. A
user typing ``@handleSubmit`` does not first decide whether the thing they want
is a file or a function, and splitting the menu would make them choose.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from navin.security.workspace_access import WorkspaceScope
from navin.utils.path import normalize_relative_path

_MAX_LIMIT = 100
_DEFAULT_LIMIT = 12
_MAX_DIRS = 4
_MAX_SYMBOLS = 5
# One character matches thousands of symbols and none of them usefully.
_MIN_SYMBOL_TERM = 2

# Ordering between result kinds at equal match quality. A path is the more
# common intent, so `@app` offers app.py before a function named app.
_KIND_ORDER = {"file": 0, "directory": 1, "symbol": 2}

# Matching tiers, lower is better.
_RANK_EXACT_STEM = 0
_RANK_EXACT_NAME = 1
_RANK_NAME_PREFIX = 2
_RANK_NAME_SUBSTRING = 3
_RANK_PATH_SUBSTRING = 4
_RANK_NAME_SUBSEQUENCE = 5
_RANK_PATH_SUBSEQUENCE = 6

_TEST_HINTS = ("test", "spec", "__mocks__", "fixture")


class FileSearchError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _is_boundary(text: str, pos: int) -> bool:
    """Whether ``pos`` starts a word: string start, after a separator, or a hump."""
    if pos == 0:
        return True
    previous = text[pos - 1]
    if not previous.isalnum():
        return True
    return text[pos].isupper() and not previous.isupper()


def _subsequence_score(
    needle: str, haystack: str, original: str,
) -> tuple[int, int, int] | None:
    """``(boundary_hits, start, span)`` for a subsequence match, else None.

    Boundary hits are what separate a real abbreviation from coincidence:
    ``dwb`` lands on three word starts in ``DevWorkBench`` but drifts through
    the middle of ``gradlew.bat``.
    """
    positions: list[int] = []
    cursor = 0
    for char in needle:
        found = haystack.find(char, cursor)
        if found < 0:
            return None
        positions.append(found)
        cursor = found + 1
    boundaries = sum(1 for pos in positions if _is_boundary(original, pos))
    return boundaries, positions[0], positions[-1] - positions[0]


@dataclass(frozen=True, slots=True)
class _Match:
    rank: int
    boundaries: int = 0
    start: int = 0
    span: int = 0

    @property
    def quality(self) -> int:
        """Higher is better. Weighs word starts against how far the match drifts.

        Counting boundaries alone is not enough: a long filename can collect
        more word starts than the file actually named after the term, so the
        span and offset have to pull back against it.
        """
        return self.boundaries * 8 - self.span - self.start // 2


def _match_path(rel_path: str, term: str) -> _Match | None:
    """Relevance of one path for ``term``; lower rank is better."""
    lowered_path = rel_path.lower()
    name = PurePosixPath(rel_path).name
    lowered_name = name.lower()
    stem = PurePosixPath(lowered_name).stem

    # A term containing a separator is a path fragment, so match it as one.
    if "/" in term:
        if term in lowered_path:
            return _Match(_RANK_PATH_SUBSTRING, start=lowered_path.index(term))
        scored = _subsequence_score(term, lowered_path, rel_path)
        if scored is None:
            return None
        return _Match(_RANK_PATH_SUBSEQUENCE, *scored)

    if stem == term:
        return _Match(_RANK_EXACT_STEM)
    if lowered_name == term:
        return _Match(_RANK_EXACT_NAME)
    if lowered_name.startswith(term):
        return _Match(_RANK_NAME_PREFIX)
    if term in lowered_name:
        return _Match(_RANK_NAME_SUBSTRING, start=lowered_name.index(term))
    if term in lowered_path:
        return _Match(_RANK_PATH_SUBSTRING, start=lowered_path.index(term))
    # Subsequence matching stays on the basename. Allowing it across the whole
    # path makes almost everything match: "ratelim" would reach
    # templates/word/nda_mutuel/image.png through unrelated characters.
    scored = _subsequence_score(term, lowered_name, name)
    if scored is None:
        return None
    return _Match(_RANK_NAME_SUBSEQUENCE, *scored)


def _looks_like_test(rel_path: str) -> bool:
    lowered = rel_path.lower()
    return any(hint in lowered for hint in _TEST_HINTS)


def _sort_key(rel_path: str, match: _Match, *, kind: str = "file") -> tuple:
    """Rank first, then match quality, then prefer shallow non-test files."""
    return (
        match.rank,
        -match.quality,
        _KIND_ORDER.get(kind, 0),
        1 if _looks_like_test(rel_path) else 0,
        rel_path.count("/"),
        len(rel_path),
        rel_path,
    )


def _match_name(name: str, term: str) -> _Match | None:
    """Relevance of a bare identifier, on the same scale as a path."""
    lowered = name.lower()
    if lowered == term:
        return _Match(_RANK_EXACT_STEM)
    if lowered.startswith(term):
        return _Match(_RANK_NAME_PREFIX)
    if term in lowered:
        return _Match(_RANK_NAME_SUBSTRING, start=lowered.index(term))
    scored = _subsequence_score(term, lowered, name)
    if scored is None:
        return None
    return _Match(_RANK_NAME_SUBSEQUENCE, *scored)


def file_search_payload(
    scope: WorkspaceScope,
    query: str,
    *,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Return file and folder completions for ``query`` within the project."""
    root = scope.project_path
    if not root.is_dir():
        raise FileSearchError(404, "project directory not found")

    from navin.index import get_index

    capped = max(1, min(limit, _MAX_LIMIT))
    term = normalize_relative_path(query).lower()

    index = get_index(root)
    index.ensure()
    all_files = index.all_files

    if not term:
        # Nothing typed yet: offer the shallowest files so the palette is not
        # empty, since an empty menu reads as "no files" rather than "keep typing".
        blank = _Match(_RANK_EXACT_STEM)
        ranked = sorted(all_files, key=lambda rel: _sort_key(rel, blank))[:capped]
        items = [_file_item(rel) for rel in ranked]
        return {"query": "", "items": items, "total": len(items)}

    scored: list[tuple[tuple, dict[str, Any]]] = []
    for rel in all_files:
        match = _match_path(rel, term)
        if match is None:
            continue
        scored.append((_sort_key(rel, match), _file_item(rel)))

    for rel, match in _matching_directories(all_files, term):
        scored.append((_sort_key(rel, match, kind="directory"), _dir_item(rel)))

    for item, match in _matching_symbols(index, term):
        scored.append((_sort_key(item["path"], match, kind="symbol"), item))

    scored.sort(key=lambda row: row[0])
    items = [item for _, item in scored[:capped]]
    return {"query": term, "items": items, "total": len(items)}


def _matching_directories(
    all_files: list[str], term: str,
) -> list[tuple[str, _Match]]:
    """Directories worth offering, so a whole folder can be attached."""
    seen: set[str] = set()
    out: list[tuple[str, _Match]] = []
    for rel in all_files:
        parent = PurePosixPath(rel).parent
        while str(parent) not in (".", ""):
            candidate = str(parent)
            if candidate in seen:
                break
            seen.add(candidate)
            match = _match_path(candidate, term)
            if match is not None:
                out.append((candidate, match))
            parent = parent.parent
    out.sort(key=lambda row: _sort_key(row[0], row[1], kind="directory"))
    return out[:_MAX_DIRS]


def _matching_symbols(index: Any, term: str) -> list[tuple[dict[str, Any], _Match]]:
    """Definitions worth offering, so a function can be named directly.

    A term carrying a separator is a path fragment, not an identifier, and the
    index has nothing to say about it.
    """
    if len(term) < _MIN_SYMBOL_TERM or "/" in term:
        return []
    try:
        locations = index.search(term, limit=_MAX_SYMBOLS * 6)
    except Exception:
        # The palette must still list files if symbol lookup fails.
        return []

    out: list[tuple[dict[str, Any], _Match]] = []
    seen: set[tuple[str, str]] = set()
    for location in locations:
        name = _bare_name(location.name)
        key = (name, location.path)
        if key in seen:
            continue
        match = _match_name(name, term)
        if match is None:
            continue
        seen.add(key)
        out.append((_symbol_item(name, location), match))
    out.sort(key=lambda row: _sort_key(row[0]["path"], row[1], kind="symbol"))
    return out[:_MAX_SYMBOLS]


def _bare_name(display: str) -> str:
    """The identifier out of a display label like ``class Foo`` or ``Foo.bar()``."""
    name = display.strip().split()[-1] if display.strip() else ""
    return name.split("(", 1)[0].strip()


def _file_item(rel: str) -> dict[str, Any]:
    return {
        "path": rel,
        "name": PurePosixPath(rel).name,
        "kind": "file",
    }


def _dir_item(rel: str) -> dict[str, Any]:
    return {
        "path": rel,
        "name": PurePosixPath(rel).name,
        "kind": "directory",
    }


def _symbol_item(name: str, location: Any) -> dict[str, Any]:
    return {
        "path": location.path,
        "name": name,
        "kind": "symbol",
        "line": int(getattr(location, "line", 0) or 0),
        "symbolKind": str(getattr(location, "kind", "") or ""),
    }
