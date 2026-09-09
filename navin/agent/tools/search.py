# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Search tools: file discovery and grep.

``grep`` prefers the native Rust backend (the ``navin_core`` extension, built
from ``navin-core/``), then ripgrep when it is on PATH, then a pure-Python
scan. All backends produce the same intermediate hit list, so output and
filtering semantics do not depend on which one ran.

Content results are ranked by relevance rather than emitted in directory order:
a match on a declaration line outranks a passing mention, and tests, generated
trees, and docs sink. Directory noise is filtered from one shared skip set
(:data:`navin.index.store.SKIP_DIRS`) so the agent and the code index agree on
what counts as ignorable.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import shutil
import subprocess
from contextlib import suppress
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, TypeVar

from navin.agent.tools.base import ToolResult
from navin.agent.tools.filesystem import ListDirTool, _FsTool
from navin.index.store import SKIP_DIRS as _INDEX_SKIP_DIRS
from navin.utils import text_decode
from navin.utils.native import native as _native
from navin.utils.proc import no_window_kwargs

_DEFAULT_HEAD_LIMIT = 250
_DEFAULT_FILE_HEAD_LIMIT = 200
_RG_TIMEOUT_S = 20
_RG_MAX_MATCHES = 20_000
T = TypeVar("T")

# One skip set for the agent's search tools and the code index. Dotfiles are
# deliberately *not* skipped: .github/workflows and .env.example are things the
# agent legitimately needs to grep.
_SEARCH_SKIP_DIRS = frozenset(ListDirTool._IGNORE_DIRS) | _INDEX_SKIP_DIRS
_TYPE_GLOB_MAP = {
    "py": ("*.py", "*.pyi"),
    "python": ("*.py", "*.pyi"),
    "js": ("*.js", "*.jsx", "*.mjs", "*.cjs"),
    "ts": ("*.ts", "*.tsx", "*.mts", "*.cts"),
    "tsx": ("*.tsx",),
    "jsx": ("*.jsx",),
    "json": ("*.json",),
    "md": ("*.md", "*.mdx"),
    "markdown": ("*.md", "*.mdx"),
    "go": ("*.go",),
    "rs": ("*.rs",),
    "rust": ("*.rs",),
    "java": ("*.java",),
    "sh": ("*.sh", "*.bash"),
    "yaml": ("*.yaml", "*.yml"),
    "yml": ("*.yaml", "*.yml"),
    "toml": ("*.toml",),
    "sql": ("*.sql",),
    "html": ("*.html", "*.htm"),
    "css": ("*.css", "*.scss", "*.sass"),
}


def _normalize_pattern(pattern: str) -> str:
    return pattern.strip().replace("\\", "/")


def _class_end(pattern: str, start: int) -> int | None:
    """Index of the ``]`` closing the class opened at ``start``, else None.

    Follows fnmatch: a leading ``!``/``^`` negates and a ``]`` in first
    position is a literal member.
    """
    j = start + 1
    n = len(pattern)
    if j < n and pattern[j] in "!^":
        j += 1
    if j < n and pattern[j] == "]":
        j += 1
    while j < n and pattern[j] != "]":
        j += 1
    return j if j < n else None


def _brace_end(pattern: str, start: int) -> int | None:
    """Index of the ``}`` closing the group opened at ``start``, else None."""
    depth = 0
    i, n = start, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "[":
            close = _class_end(pattern, i)
            i = (i if close is None else close) + 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _brace_alternatives(pattern: str, start: int, end: int) -> list[str]:
    """Split the group ``pattern[start:end]`` on its top-level commas."""
    alternatives: list[str] = []
    current: list[str] = []
    depth = 0
    i = start + 1
    while i < end:
        char = pattern[i]
        if char == "[":
            close = _class_end(pattern, i)
            if close is not None and close < end:
                current.append(pattern[i : close + 1])
                i = close + 1
                continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "," and depth == 0:
            alternatives.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    alternatives.append("".join(current))
    return alternatives


def _translate_glob_body(pattern: str) -> str:
    """Translate a glob into an unanchored regex. See ``_translate_glob``."""
    parts: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            if j - i >= 2:
                if j < n and pattern[j] == "/":
                    # "**/" including the case of zero segments.
                    parts.append(r"(?:[^/]+/)*")
                    i = j + 1
                else:
                    parts.append(r".*")
                    i = j
            else:
                parts.append(r"[^/]*")
                i = j
        elif char == "?":
            parts.append(r"[^/]")
            i += 1
        elif char == "[":
            # Pass character classes through, honouring fnmatch's "!" negation;
            # an unterminated "[" is taken literally, like fnmatch does.
            close = _class_end(pattern, i)
            if close is None:
                parts.append(re.escape(char))
                i += 1
            else:
                inner = pattern[i + 1 : close].replace("\\", "\\\\")
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                parts.append(f"[{inner}]")
                i = close + 1
        elif char == "{":
            # Brace alternation, as globset does it for "**/*.{ts,tsx}". Every
            # such glob used to be escaped literally, so the filter dropped all
            # candidates and grep reported "no matches" for a pattern that hit.
            close = _brace_end(pattern, i)
            if close is None:
                parts.append(re.escape(char))
                i += 1
            else:
                inner = "|".join(
                    _translate_glob_body(alt)
                    for alt in _brace_alternatives(pattern, i, close)
                )
                parts.append(f"(?:{inner})")
                i = close + 1
        else:
            parts.append(re.escape(char))
            i += 1
    return "".join(parts)


def _translate_glob(pattern: str) -> str:
    """Translate a glob into a regex where ``**`` really is recursive.

    ``PurePosixPath.match`` treats ``**`` as a single ``*`` before Python 3.13,
    so the pattern the tool schemas themselves advertise
    (``tests/**/test_*.py``) silently failed on nested files. Semantics here
    follow ripgrep's: ``**/`` spans zero or more path segments, ``*`` and ``?``
    never cross a ``/``, ``{a,b}`` alternates, and a pattern containing a slash
    is anchored to the search root.
    """
    return _translate_glob_body(pattern) + r"\Z"


@lru_cache(maxsize=256)
def _compiled_glob(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(_translate_glob(pattern))
    except re.error:
        return None


def _match_glob(rel_path: str, name: str, pattern: str) -> bool:
    normalized = _normalize_pattern(pattern)
    if not normalized:
        return False
    if "/" in normalized or "**" in normalized:
        compiled = _compiled_glob(normalized)
        return compiled is not None and compiled.match(rel_path) is not None
    if "{" in normalized:
        # fnmatch has no brace alternation, so "*.{ts,tsx}" would match nothing.
        compiled = _compiled_glob(normalized)
        return compiled is not None and compiled.match(name) is not None
    return fnmatch.fnmatch(name, normalized)


def _is_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    sample = raw[:4096]
    if not sample:
        return False
    non_text = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return (non_text / len(sample)) > 0.2


def _paginate(items: list[T], limit: int | None, offset: int) -> tuple[list[T], bool]:
    if limit is None:
        return items[offset:], False
    sliced = items[offset : offset + limit]
    truncated = len(items) > offset + limit
    return sliced, truncated


def _pagination_note(limit: int | None, offset: int, truncated: bool) -> str | None:
    if truncated:
        if limit is None:
            return f"(pagination: offset={offset})"
        return f"(pagination: limit={limit}, offset={offset})"
    if offset > 0:
        return f"(pagination: offset={offset})"
    return None


def _matches_type(name: str, file_type: str | None) -> bool:
    if not file_type:
        return True
    lowered = file_type.strip().lower()
    if not lowered:
        return True
    patterns = _TYPE_GLOB_MAP.get(lowered, (f"*.{lowered}",))
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in patterns)


_GLOB_METACHARS = ("*", "?", "[", "{")


def _no_files_found(query: str | None, glob: str | None) -> str:
    """Explain an empty result when the query was written as a pattern.

    ``query`` matches substrings, so a wildcard silently matches nothing. That
    reads as "the file is not there" and sends the agent looking elsewhere, so
    the likely mix-up is named instead.
    """
    if query and not glob and any(char in query for char in _GLOB_METACHARS):
        return (
            f"No files found. Note that query matches substrings, so the wildcards in "
            f"{query!r} matched nothing literally. Use glob={query!r} for pattern "
            f"matching, or drop the wildcards from query."
        )
    return "No files found"


def _matches_query(rel_path: str, query: str | None) -> bool:
    if not query:
        return True
    haystack = rel_path.lower()
    terms = [part for part in query.lower().split() if part]
    return all(term in haystack for term in terms)


# ---------------------------------------------------------------------------
# Relevance
# ---------------------------------------------------------------------------

_DEFINITION_RE = re.compile(
    r"""^\s*
    (?:@\w[\w.]*\s*(?:\([^)]*\))?\s*)?
    (?:(?:export|default|public|private|protected|internal|static|final
        |abstract|async|declare|pub|open|override|inline|extern)\s+)*
    (?:def|class|struct|interface|enum|trait|impl|type|typedef
      |func|fn|function|module|package|namespace|record
      |const|let|var|val)\b
    """,
    re.VERBOSE,
)
_SQL_DEFINITION_RE = re.compile(
    r"^\s*create\s+(?:or\s+replace\s+)?"
    r"(?:table|view|index|function|procedure|trigger|type)\b",
    re.IGNORECASE,
)
# Module-level assignment (column 0), which is how most languages spell a
# top-level constant or a route/handler table.
_ASSIGN_DEFINITION_RE = re.compile(r"^[A-Za-z_$][\w.$]*\s*(?::[^=\n]+)?=\s*\S")

_TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:tests?|__tests__|spec|specs|e2e|fixtures?|testdata)(?:/|$)"
    r"|(?:^|/)(?:test_[^/]+|[^/]+_test|[^/]+\.test|[^/]+\.spec)\.[^/.]+$",
    re.IGNORECASE,
)
_GENERATED_PATH_RE = re.compile(
    r"(?:^|/)(?:dist|build|generated|__generated__|migrations|snapshots?)(?:/|$)"
    r"|\.(?:min|bundle|generated|pb|g)\.[^/.]+$"
    r"|(?:^|/)(?:package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock)$",
    re.IGNORECASE,
)

_DOC_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
_CONFIG_SUFFIXES = frozenset({
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".lock", ".env",
})
_CODE_SUFFIXES = frozenset({
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".kt", ".kts", ".cs", ".rb", ".php", ".swift", ".m", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".sql", ".vue", ".svelte", ".sh", ".bash", ".zsh",
})


def _is_definition_line(line: str) -> bool:
    """Whether a matching line reads as a declaration rather than a use."""
    return bool(
        _DEFINITION_RE.match(line)
        or _SQL_DEFINITION_RE.match(line)
        or _ASSIGN_DEFINITION_RE.match(line)
    )


@dataclass(slots=True)
class _FileHit:
    """Matches found in one file, independent of the backend that found them."""

    path: Path
    display_path: str
    rel_path: str
    mtime: float
    # (line number, line text) in ascending line order.
    matches: list[tuple[int, str]] = field(default_factory=list)

    @property
    def definition_matches(self) -> int:
        return sum(1 for _, text in self.matches if _is_definition_line(text))

    def relevance(self) -> int:
        """Higher is more likely to be what the caller was looking for.

        Deliberately coarse and explainable: a declaration dominates, match
        density breaks near-ties, and tests, generated trees, docs, and deep
        paths are pushed down.
        """
        is_test = bool(_TEST_PATH_RE.search(self.rel_path))
        is_generated = bool(_GENERATED_PATH_RE.search(self.rel_path))

        score = 0
        # A test that declares ``test_rate_limit`` is not the definition of rate
        # limiting, so the declaration bonus is withheld from tests and from
        # generated trees; otherwise they outrank the code they cover.
        if self.definition_matches and not (is_test or is_generated):
            score += 100
        score += 3 * min(len(self.matches), 8)

        suffix = PurePosixPath(self.rel_path).suffix.lower()
        if suffix in _CODE_SUFFIXES:
            score += 10
        elif suffix in _DOC_SUFFIXES:
            score -= 15
        elif suffix in _CONFIG_SUFFIXES:
            score -= 5

        if is_test:
            score -= 40
        if is_generated:
            score -= 60
        score -= 2 * self.rel_path.count("/")
        return score


def _sort_hits(hits: list[_FileHit], sort: str) -> list[_FileHit]:
    if sort == "path":
        return sorted(hits, key=lambda hit: hit.display_path)
    if sort == "modified":
        return sorted(hits, key=lambda hit: (-hit.mtime, hit.display_path))
    return sorted(
        hits,
        key=lambda hit: (-hit.relevance(), -hit.mtime, hit.display_path),
    )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def _native_module() -> Any | None:
    """Resolve the Rust extension. Separate function so tests can force the fallback."""
    return _native()


def _native_scan(
    target: Path,
    pattern: str,
    *,
    fixed_strings: bool,
    case_insensitive: bool,
    include_ignored: bool,
    max_file_bytes: int,
) -> dict[str, list[tuple[int, str]]] | None:
    """Scan with the Rust backend, or None to fall back.

    Returns None when the extension is not installed or when its regex engine
    rejects the pattern (lookaround, backreferences - the same limits as
    ripgrep), so the caller retries with the next backend.
    """
    module = _native_module()
    if module is None:
        return None
    try:
        return module.grep_scan(
            str(target),
            pattern,
            fixed_strings=fixed_strings,
            case_insensitive=case_insensitive,
            include_ignored=include_ignored,
            max_file_bytes=max_file_bytes,
            skip_dirs=sorted(_SEARCH_SKIP_DIRS),
            max_total_matches=_RG_MAX_MATCHES,
        )
    except (ValueError, OSError, RuntimeError):
        return None


def _rg_binary() -> str | None:
    """Resolve ripgrep. Separate function so tests can force the fallback."""
    return shutil.which("rg")


def _rg_scan(
    target: Path,
    pattern: str,
    *,
    fixed_strings: bool,
    case_insensitive: bool,
    include_ignored: bool,
    max_file_bytes: int,
    multiline: bool = False,
) -> dict[str, list[tuple[int, str]]] | None:
    """Match line numbers and text per absolute path, or None to fall back.

    Returns None whenever ripgrep is unavailable or cannot handle the pattern
    (its regex engine rejects lookaround and backreferences), so the caller
    retries in Python instead of reporting a failure the user cannot act on.
    ``multiline`` lets the pattern span lines (``.`` matches a newline); a
    match is reported on the line where it starts.
    """
    rg = _rg_binary()
    if rg is None:
        return None
    cmd = [
        rg, "--json", "--no-follow", "--no-messages", "--hidden",
        "--max-filesize", str(max_file_bytes),
        "--ignore-case" if case_insensitive else "--case-sensitive",
    ]
    if fixed_strings:
        cmd.append("--fixed-strings")
    if multiline:
        cmd.extend(["--multiline", "--multiline-dotall"])
    if include_ignored:
        cmd.append("--no-ignore-vcs")
    for skip in sorted(_SEARCH_SKIP_DIRS):
        cmd.extend(["--glob", f"!{skip}/"])
    # Glob and type filters are applied in Python for both backends so the two
    # never disagree; ripgrep only prunes directory noise here.
    cmd.extend(["--", pattern, str(target)])
    try:
        out = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_RG_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode not in (0, 1):  # 1 = no matches; 2 = unsupported pattern
        return None

    hits: dict[str, list[tuple[int, str]]] = {}
    total = 0
    for raw in out.stdout.splitlines():
        if total >= _RG_MAX_MATCHES:
            break
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path_text = ((data.get("path") or {}).get("text")) or ""
        if not path_text:
            continue  # binary or non-UTF-8 payload
        text = ((data.get("lines") or {}).get("text")) or ""
        line_no = int(data.get("line_number") or 0)
        if line_no <= 0:
            continue
        # A multiline match carries every line it spans; the block format is
        # one line per match and context_after shows the rest.
        hits.setdefault(path_text, []).append((line_no, text.rstrip("\n").split("\n", 1)[0]))
        total += 1
    return hits


def _multiline_matches(content: str, regex: re.Pattern[str]) -> list[tuple[int, str]]:
    """``(line_number, first_line_of_match)`` for every spanning match in ``content``.

    Mirrors what ripgrep --multiline reports so the Python fallback and the
    binary agree on where a match starts.
    """
    out: list[tuple[int, str]] = []
    for found in regex.finditer(content):
        if found.end() == found.start():
            continue
        line_no = content.count("\n", 0, found.start()) + 1
        line_start = content.rfind("\n", 0, found.start()) + 1
        line_end = content.find("\n", found.start())
        text = content[line_start:] if line_end < 0 else content[line_start:line_end]
        out.append((line_no, text))
        if len(out) >= _RG_MAX_MATCHES:
            break
    return out


# Upper bound on entries any file-listing backend returns for one call. A
# monorepo with vendored trees outside the skip set must still answer in
# bounded time; the result carries a note when the bound was hit.
_WALK_MAX_ENTRIES = 300_000


def _rg_files(target: Path, *, include_ignored: bool) -> list[str] | None:
    """Every file under ``target`` per ``rg --files``, or None to fall back.

    Measured 2026-09-02 on this repository: 3 951 tracked files in 22 ms,
    against 25.7 s for the ``os.walk`` plus three ``stat`` calls per entry that
    ``find_files`` used to run on the event loop. ripgrep honours .gitignore
    exactly the way :func:`_rg_scan` does for ``grep``, so both tools see the
    same tree.
    """
    rg = _rg_binary()
    if rg is None:
        return None
    cmd = [rg, "--files", "--null", "--hidden", "--no-follow", "--no-messages"]
    if include_ignored:
        cmd.append("--no-ignore-vcs")
    for skip in sorted(_SEARCH_SKIP_DIRS):
        cmd.extend(["--glob", f"!{skip}/"])
    cmd.extend(["--", str(target)])
    try:
        out = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_RG_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # 1 means "no files matched", which is a valid empty answer for --files.
    if out.returncode not in (0, 1):
        return None
    return [chunk for chunk in out.stdout.split("\0") if chunk][:_WALK_MAX_ENTRIES]


def _native_walk(target: Path, *, include_dirs: bool) -> list[tuple[str, bool]] | None:
    """``(path, is_dir)`` per entry from the Rust walker, or None to fall back."""
    module = _native_module()
    if module is None:
        return None
    try:
        rows = module.walk(
            str(target),
            skip_dirs=sorted(_SEARCH_SKIP_DIRS),
            include_dirs=include_dirs,
            max_entries=_WALK_MAX_ENTRIES,
        )
    except (ValueError, OSError, RuntimeError, TypeError):
        return None
    return [(str(path), bool(is_dir)) for path, is_dir, _mtime, _size in rows]


def _scandir_walk(target: Path, *, include_dirs: bool) -> list[tuple[str, bool]]:
    """Pure-Python walk: one ``scandir`` per directory, no per-file ``stat``.

    ``DirEntry.is_dir`` answers from the directory listing itself on every
    platform that reports ``d_type``, so this costs one syscall per directory
    instead of three per file.
    """
    out: list[tuple[str, bool]] = []
    stack = [str(target)]
    while stack and len(out) < _WALK_MAX_ENTRIES:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if entry.name in _SEARCH_SKIP_DIRS:
                    continue
                stack.append(entry.path)
                if include_dirs:
                    out.append((entry.path, True))
            else:
                out.append((entry.path, False))
    return out


def _gitignored_paths(root: Path, paths: list[Path]) -> set[Path]:
    """The subset of ``paths`` that .gitignore excludes.

    The ripgrep backend honours .gitignore natively; without this the pure
    Python fallback searched ignored files, so a pattern ripgrep cannot compile
    (lookahead, backreferences) silently widened the search. One batch
    ``git check-ignore --stdin`` call keeps it a single subprocess. This is an
    approximation by design: when git is missing or the target is not a work
    tree, nothing is filtered, which matches what ripgrep does outside a repo.
    """
    if not paths:
        return set()
    git = shutil.which("git")
    if git is None:
        return set()
    try:
        out = subprocess.run(  # noqa: S603
            [git, "-C", str(root), "check-ignore", "--stdin", "-z"],
            input="\0".join(str(p) for p in paths),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_RG_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    # 0 = some ignored, 1 = none; anything else (128 = not a repo) means git
    # could not answer, so nothing is filtered rather than everything.
    if out.returncode not in (0, 1):
        return set()
    return {Path(chunk) for chunk in out.stdout.split("\0") if chunk}


def _read_text_file(
    file_path: Path, max_file_bytes: int,
) -> tuple[str | None, str | None]:
    """Return (content, skip_reason)."""
    try:
        raw = file_path.read_bytes()
    except OSError:
        return None, "binary"
    if len(raw) > max_file_bytes:
        return None, "large"
    if _is_binary(raw):
        return None, "binary"
    # Same decoder as read_file, so a cp1252 or BOM-prefixed file is searchable
    # rather than reported as binary by grep and readable by read_file.
    decoded = text_decode.decode(raw)
    if decoded is None:
        return None, "binary"
    return decoded.text, None


class _SearchTool(_FsTool):
    _IGNORE_DIRS = set(_SEARCH_SKIP_DIRS)

    def _display_path(self, target: Path, root: Path) -> str:
        workspace = self._display_workspace()
        if workspace:
            with suppress(ValueError):
                return target.relative_to(workspace).as_posix()
        return target.relative_to(root).as_posix()

    def _iter_files(self, root: Path) -> Iterable[Path]:
        if root.is_file():
            yield root
            return

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
            current = Path(dirpath)
            for filename in sorted(filenames):
                yield current / filename


class FindFilesTool(_SearchTool):
    """Find files by path fragment, glob, or type."""
    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "find_files"

    @property
    def description(self) -> str:
        return (
            "Find files by path fragment, glob, or file type. "
            "Use this before read_file when you need to locate files, and "
            "prefer it over shell find/ls for ordinary workspace discovery. "
            "Returns workspace-relative paths and skips common dependency/build "
            "directories and anything .gitignore excludes (pass include_ignored "
            "to list those too)."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in (default '.')",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Optional case-insensitive path fragment search. "
                        "Whitespace-separated terms must all be present."
                    ),
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "Optional file filter, e.g. '*.py', 'tests/**/test_*.py' "
                        "or '**/*.{json,rs,toml}' (brace alternation supported)"
                    ),
                },
                "type": {
                    "type": "string",
                    "description": "Optional file type shorthand, e.g. 'py', 'ts', 'md', 'json'",
                },
                "include_dirs": {
                    "type": "boolean",
                    "description": "Include matching directories as well as files (default false)",
                },
                "include_ignored": {
                    "type": "boolean",
                    "description": "Also list files that .gitignore excludes (default false)",
                },
                "sort": {
                    "type": "string",
                    "enum": ["path", "modified"],
                    "description": "Sort by path or most recently modified first (default path)",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Maximum number of paths to return (default 200, 0 for all, max 1000)",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N results before applying head_limit",
                    "minimum": 0,
                    "maximum": 100000,
                },
            },
        }

    def _list_entries(
        self, target: Path, *, include_dirs: bool, include_ignored: bool,
    ) -> tuple[list[tuple[str, bool]], bool]:
        """``(path, is_dir)`` per candidate and whether .gitignore was already applied.

        ripgrep first (it prunes ignored trees while walking, which is what
        makes it fast), then the Rust walker, then ``scandir``. The walkers
        do not read .gitignore; the caller filters their *matches* through
        ``git check-ignore`` so every backend returns the same answer.
        """
        if target.is_file():
            return [(str(target), False)], True
        if include_dirs:
            files = None  # ``rg --files`` lists files only.
        else:
            files = _rg_files(target, include_ignored=include_ignored)
        if files is not None:
            return [(path, False) for path in files], True
        rows = _native_walk(target, include_dirs=include_dirs)
        if rows is None:
            rows = _scandir_walk(target, include_dirs=include_dirs)
        if include_dirs:
            rows.insert(0, (str(target), True))
        return rows, False

    def _collect_matches(
        self,
        target: Path,
        root: Path,
        *,
        query: str | None,
        glob: str | None,
        type: str | None,
        include_dirs: bool,
        include_ignored: bool,
        want_mtime: bool,
    ) -> tuple[list[tuple[str, float]], bool]:
        """Filtered ``(display_path, mtime)`` rows and whether the listing was cut."""
        entries, gitignore_applied = self._list_entries(
            target, include_dirs=include_dirs, include_ignored=include_ignored,
        )
        truncated = len(entries) >= _WALK_MAX_ENTRIES
        # Every backend prefixes its rows with ``target`` verbatim, so the
        # relative part is a string slice. ``Path.relative_to`` cost 185 us a
        # row here (0.9 s of a 1.2 s call on 5k rows); this loop is 20x cheaper.
        root_prefix = str(root).rstrip(os.sep) + os.sep
        display_prefix = root_prefix
        workspace = self._display_workspace()
        if workspace:
            display_prefix = str(workspace).rstrip(os.sep) + os.sep
        kept: list[tuple[Path, bool, str]] = []
        for path_text, is_dir in entries:
            if path_text.startswith(root_prefix):
                rel_path = path_text[len(root_prefix):].replace(os.sep, "/")
            elif path_text == str(root):
                rel_path = "."
            else:
                continue
            name = rel_path.rsplit("/", 1)[-1]
            if glob and not _match_glob(rel_path, name, glob):
                continue
            if is_dir and type:
                continue
            if not is_dir and not _matches_type(name, type):
                continue
            if path_text.startswith(display_prefix):
                display_path = path_text[len(display_prefix):].replace(os.sep, "/")
            else:
                display_path = rel_path
            if not _matches_query(display_path, query):
                continue
            kept.append((Path(path_text), is_dir, display_path))

        if kept and not gitignore_applied and not include_ignored:
            ignored = _gitignored_paths(root, [candidate for candidate, _d, _s in kept])
            if ignored:
                kept = [row for row in kept if row[0] not in ignored]

        matches: list[tuple[str, float]] = []
        for candidate, is_dir, display_path in kept:
            mtime = 0.0
            if want_mtime:
                try:
                    mtime = candidate.stat().st_mtime
                except OSError:
                    mtime = 0.0
            matches.append((display_path + ("/" if is_dir else ""), mtime))
        return matches, truncated

    async def execute(
        self,
        path: str = ".",
        query: str | None = None,
        glob: str | None = None,
        type: str | None = None,
        include_dirs: bool = False,
        include_ignored: bool = False,
        sort: str = "path",
        head_limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        try:
            target = await self._bound_path(path or ".", write=False)
            if not target.exists():
                return self._missing_path_msg("Path", path or ".", target)
            if not (target.is_dir() or target.is_file()):
                return ToolResult.error(f"Error: Unsupported path: {path}")

            if sort not in {"path", "modified"}:
                return ToolResult.error("Error: sort must be 'path' or 'modified'")

            limit = (
                _DEFAULT_FILE_HEAD_LIMIT
                if head_limit is None
                else None if head_limit == 0 else head_limit
            )
            root = target if target.is_dir() else target.parent

            # Listing a tree is disk work; it must never stall the event loop
            # that every other session and the WebUI share.
            matches, walk_truncated = await asyncio.to_thread(
                self._collect_matches,
                target,
                root,
                query=query,
                glob=glob,
                type=type,
                include_dirs=include_dirs,
                include_ignored=include_ignored,
                want_mtime=sort == "modified",
            )

            if sort == "modified":
                matches.sort(key=lambda item: (-item[1], item[0]))
            else:
                matches.sort(key=lambda item: item[0])

            paths = [item[0] for item in matches]
            paged, truncated = _paginate(paths, limit, offset)
            if not paged:
                return _no_files_found(query, glob)

            result = "\n".join(paged)
            note = _pagination_note(limit, offset, truncated)
            if note:
                result += "\n\n" + note
            if walk_truncated:
                result += (
                    f"\n\nNote: the tree has more than {_WALK_MAX_ENTRIES:,} entries; "
                    "only the first were considered. Narrow with path or glob."
                )
            return result
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error finding files: {e}")


class GrepTool(_SearchTool):
    """Search file contents using a regex-like pattern."""
    _scopes = {"core", "subagent"}

    _MAX_RESULT_CHARS = 128_000
    _MAX_FILE_BYTES = 2_000_000

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search file contents with a regex pattern. Results are ranked by "
            "relevance: declaration lines outrank passing mentions, and tests, "
            "generated trees, and docs rank last. Default output_mode is "
            "files_with_matches (file paths only); use content mode for matching "
            "lines with context. Prefer this over shell grep for ordinary "
            "workspace searches. Skips binary files, files >2 MB, dependency "
            "and build directories, and anything .gitignore excludes (pass "
            "include_ignored to search those too)."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex or plain text pattern to search for",
                    "minLength": 1,
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (default '.')",
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "Optional file filter, e.g. '*.py', 'tests/**/test_*.py' "
                        "or '**/*.{json,rs,toml}' (brace alternation supported)"
                    ),
                },
                "type": {
                    "type": "string",
                    "description": "Optional file type shorthand, e.g. 'py', 'ts', 'md', 'json'",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default false)",
                },
                "fixed_strings": {
                    "type": "boolean",
                    "description": "Treat pattern as plain text instead of regex (default false)",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": (
                        "content: matching lines with optional context; "
                        "files_with_matches: only matching file paths; "
                        "count: matching line counts per file. "
                        "Default: files_with_matches"
                    ),
                },
                "context_before": {
                    "type": "integer",
                    "description": "Number of lines of context before each match",
                    "minimum": 0,
                    "maximum": 20,
                },
                "context_after": {
                    "type": "integer",
                    "description": "Number of lines of context after each match",
                    "minimum": 0,
                    "maximum": 20,
                },
                "head_limit": {
                    "type": "integer",
                    "description": (
                        "Maximum number of results to return. In content mode this limits "
                        "matching line blocks; in other modes it limits file entries. "
                        "Default 250"
                    ),
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N results before applying head_limit",
                    "minimum": 0,
                    "maximum": 100000,
                },
                "sort": {
                    "type": "string",
                    "enum": ["relevance", "path", "modified"],
                    "description": (
                        "relevance: declarations and dense matches first (default); "
                        "path: alphabetical; modified: most recently changed first"
                    ),
                },
                "include_ignored": {
                    "type": "boolean",
                    "description": (
                        "Also search files excluded by .gitignore (default false)"
                    ),
                },
                "multiline": {
                    "type": "boolean",
                    "description": (
                        "Let the pattern span lines ('.' matches newlines), e.g. "
                        "'class Foo.*?def bar'. Default false: one line at a time"
                    ),
                },
            },
            "required": ["pattern"],
        }

    @staticmethod
    def _format_block(
        display_path: str,
        lines: list[str] | None,
        match_line: int,
        match_text: str,
        before: int,
        after: int,
    ) -> str:
        if lines is None or (before == 0 and after == 0):
            return f"{display_path}:{match_line}\n> {match_line}| {match_text}"
        start = max(1, match_line - before)
        end = min(len(lines), match_line + after)
        block = [f"{display_path}:{match_line}"]
        for line_no in range(start, end + 1):
            marker = ">" if line_no == match_line else " "
            block.append(f"{marker} {line_no}| {lines[line_no - 1]}")
        return "\n".join(block)

    def _hit_for(
        self, file_path: Path, root: Path, glob: str | None, file_type: str | None,
    ) -> _FileHit | None:
        """Build an empty hit if the file passes the glob and type filters."""
        try:
            rel_path = file_path.relative_to(root).as_posix()
        except ValueError:
            return None
        if glob and not _match_glob(rel_path, file_path.name, glob):
            return None
        if not _matches_type(file_path.name, file_type):
            return None
        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return _FileHit(
            path=file_path,
            display_path=self._display_path(file_path, root),
            rel_path=rel_path,
            mtime=mtime,
        )

    def _collect_hits(
        self,
        target: Path,
        root: Path,
        regex: re.Pattern[str],
        pattern: str,
        *,
        fixed_strings: bool,
        case_insensitive: bool,
        glob: str | None,
        file_type: str | None,
        include_ignored: bool,
        multiline: bool = False,
    ) -> tuple[list[_FileHit], int | None, int | None]:
        """Return (hits, skipped_binary, skipped_large).

        The skip counts are None when the native or ripgrep backend did the
        scan: both enforce the same binary and size limits themselves but do
        not say how many files they passed over, and reporting a fabricated
        zero read as "nothing was skipped". Unknown is the honest answer, so
        no skip note is printed on those paths; only the Python fallback
        counts exactly.
        """
        # The Rust scanner matches line by line; a spanning pattern goes to
        # ripgrep (--multiline) or to the whole-file Python search below.
        scanned = None if multiline else _native_scan(
            target,
            pattern,
            fixed_strings=fixed_strings,
            case_insensitive=case_insensitive,
            include_ignored=include_ignored,
            max_file_bytes=self._MAX_FILE_BYTES,
        )
        if scanned is None:
            scanned = _rg_scan(
                target,
                pattern,
                fixed_strings=fixed_strings,
                case_insensitive=case_insensitive,
                include_ignored=include_ignored,
                max_file_bytes=self._MAX_FILE_BYTES,
                multiline=multiline,
            )
        if scanned is not None:
            hits: list[_FileHit] = []
            for path_text, matches in scanned.items():
                hit = self._hit_for(Path(path_text), root, glob, file_type)
                if hit is None:
                    continue
                hit.matches = sorted(matches)
                hits.append(hit)
            return hits, None, None

        hits = []
        skipped_binary = 0
        skipped_large = 0
        candidates = list(self._iter_files(target))
        ignored = set() if include_ignored else _gitignored_paths(root, candidates)
        for file_path in candidates:
            if file_path in ignored:
                continue
            hit = self._hit_for(file_path, root, glob, file_type)
            if hit is None:
                continue
            content, skip_reason = _read_text_file(file_path, self._MAX_FILE_BYTES)
            if content is None:
                if skip_reason == "large":
                    skipped_large += 1
                else:
                    skipped_binary += 1
                continue
            if multiline:
                hit.matches.extend(_multiline_matches(content, regex))
            else:
                for idx, line in enumerate(content.splitlines(), start=1):
                    if regex.search(line):
                        hit.matches.append((idx, line))
            if hit.matches:
                hits.append(hit)
        return hits, skipped_binary, skipped_large

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        type: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int | None = None,
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        sort: str = "relevance",
        include_ignored: bool = False,
        multiline: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            target = await self._bound_path(path or ".", write=False)
            if not target.exists():
                return self._missing_path_msg("Path", path or ".", target)
            if not (target.is_dir() or target.is_file()):
                return ToolResult.error(f"Error: Unsupported path: {path}")
            if sort not in {"relevance", "path", "modified"}:
                return ToolResult.error(
                    "Error: sort must be 'relevance', 'path', or 'modified'"
                )

            flags = re.IGNORECASE if case_insensitive else 0
            if multiline:
                flags |= re.DOTALL | re.MULTILINE
            try:
                needle = re.escape(pattern) if fixed_strings else pattern
                regex = re.compile(needle, flags)
            except re.error as e:
                return ToolResult.error(f"Error: invalid regex pattern: {e}")

            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif output_mode == "content" and max_matches is not None:
                limit = max_matches
            elif output_mode != "content" and max_results is not None:
                limit = max_results
            else:
                limit = _DEFAULT_HEAD_LIMIT
            truncated = False
            size_truncated = False
            root = target if target.is_dir() else target.parent

            hits, skipped_binary, skipped_large = await asyncio.to_thread(
                self._collect_hits,
                target,
                root,
                regex,
                pattern,
                fixed_strings=fixed_strings,
                case_insensitive=case_insensitive,
                glob=glob,
                file_type=type,
                include_ignored=include_ignored,
                multiline=multiline,
            )
            hits = _sort_hits(hits, sort)
            total_matches = sum(len(hit.matches) for hit in hits)

            blocks: list[str] = []
            if output_mode == "files_with_matches":
                if not hits:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    paged, truncated = _paginate(
                        [hit.display_path for hit in hits], limit, offset
                    )
                    result = "\n".join(paged)
            elif output_mode == "count":
                if not hits:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    paged_hits, truncated = _paginate(hits, limit, offset)
                    result = "\n".join(
                        f"{hit.display_path}: {len(hit.matches)}" for hit in paged_hits
                    )
            else:
                result_chars = 0
                seen_content_matches = 0
                wants_context = context_before > 0 or context_after > 0
                for hit in hits:
                    file_lines: list[str] | None = None
                    if wants_context:
                        content, _ = _read_text_file(hit.path, self._MAX_FILE_BYTES)
                        file_lines = None if content is None else content.splitlines()
                    for line_no, text in hit.matches:
                        seen_content_matches += 1
                        if seen_content_matches <= offset:
                            continue
                        if limit is not None and len(blocks) >= limit:
                            truncated = True
                            break
                        block = self._format_block(
                            hit.display_path,
                            file_lines,
                            line_no,
                            text,
                            context_before,
                            context_after,
                        )
                        extra_sep = 2 if blocks else 0
                        if result_chars + extra_sep + len(block) > self._MAX_RESULT_CHARS:
                            size_truncated = True
                            break
                        blocks.append(block)
                        result_chars += extra_sep + len(block)
                    if truncated or size_truncated:
                        break
                if not blocks:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    result = "\n\n".join(blocks)

            notes: list[str] = []
            if output_mode == "content" and truncated:
                notes.append(
                    f"(pagination: limit={limit}, offset={offset})"
                )
            elif output_mode == "content" and size_truncated:
                notes.append("(output truncated due to size)")
            elif truncated and output_mode in {"count", "files_with_matches"}:
                notes.append(
                    f"(pagination: limit={limit}, offset={offset})"
                )
            elif output_mode in {"count", "files_with_matches"} and offset > 0:
                notes.append(f"(pagination: offset={offset})")
            elif output_mode == "content" and offset > 0 and blocks:
                notes.append(f"(pagination: offset={offset})")
            if skipped_binary:
                notes.append(f"(skipped {skipped_binary} binary/unreadable files)")
            if skipped_large:
                notes.append(f"(skipped {skipped_large} large files)")
            if output_mode == "count" and hits:
                notes.append(
                    f"(total matches: {total_matches} in {len(hits)} files)"
                )
            if notes:
                result += "\n\n" + "\n".join(notes)
            return result
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error searching files: {e}")
