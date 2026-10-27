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
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, TypeVar

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
# Wall-clock budget of the pure-Python scan for one grep call (every pattern of
# a multi-pattern call shares it). Measured 2026-10-21 on the Windows desktop:
# with no native module and no ripgrep, four patterns over a 55 000-file WSL
# project read through \\wsl.localhost took ten minutes while the chat showed
# nothing but "Preparing grep". A partial answer that says so beats that.
_FALLBACK_BUDGET_S = 60.0
# A pattern matching this many files is a topic word, not a target: the agent
# then reads a pile of loosely related files instead of the code it needs.
_BROAD_PATTERN_FILES = 40
# Files whose match lines are kept for the read that usually follows a search.
_HIT_FILES_REMEMBERED = 50
_REGEX_META = re.compile(r"[()\[\]{}+?*^$\\]")


def _no_match_message(
    pattern: str,
    path: str,
    target: Path,
    case_insensitive: bool,
    fixed_strings: bool,
    glob: str | None,
    file_type: str | None,
) -> str:
    """Zero-result text with the concrete next steps instead of a bare 'no match'.

    Session data showed most empty greps were followed by a guessed retry; the
    hints name the cheap checks first so the retry is a deliberate one.
    """
    head = f"No matches found for pattern '{pattern}' in {path}"
    steps: list[str] = []
    if not case_insensitive and target.is_file():
        try:
            if target.stat().st_size > 2_000_000:
                raise OSError("too large for the case check")
            text = target.read_text(encoding="utf-8", errors="ignore")
            needle = re.escape(pattern) if fixed_strings else pattern
            if re.search(needle, text, re.IGNORECASE):
                steps.append("it DOES match with case_insensitive=true")
        except (OSError, re.error):
            pass
    elif not case_insensitive:
        steps.append("retry with case_insensitive=true")
    if not fixed_strings and _REGEX_META.search(pattern):
        steps.append(
            "the pattern has regex metacharacters: use fixed_strings=true for a literal"
        )
    if glob or file_type:
        steps.append("drop glob/type to check other file kinds")
    if target.is_file():
        steps.append("widen path to the parent directory")
    if "|" in pattern and not fixed_strings:
        steps.append("no alternative matched: search one exact identifier from the request")
    if not steps:
        steps.append("check the spelling or widen path")
    return head + " (next step: " + "; ".join(steps) + ")"
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
    # One C call instead of a Python loop over 4 096 bytes per file (half a
    # millisecond each, 3 s per grep on this repository).
    non_text = len(sample.translate(None, _TEXT_BYTES))
    return (non_text / len(sample)) > 0.2


# Bytes that may appear in text: tab, LF, VT, FF, CR and everything from space up.
_TEXT_BYTES = bytes([9, 10, 11, 12, 13, *range(32, 256)])


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
    flags = _rg_scan_flags(
        fixed_strings=fixed_strings,
        case_insensitive=case_insensitive,
        include_ignored=include_ignored,
        max_file_bytes=max_file_bytes,
        multiline=multiline,
    )
    out = _run_rg([rg, *flags, "--", pattern, str(target)])
    if out is None or out.returncode not in (0, 1):  # 1 = no matches; 2 = unsupported pattern
        return None
    return _parse_rg_json(out.stdout)


def _rg_scan_flags(
    *,
    fixed_strings: bool,
    case_insensitive: bool,
    include_ignored: bool,
    max_file_bytes: int,
    multiline: bool,
) -> list[str]:
    flags = [
        "--json", "--no-follow", "--no-messages", "--hidden",
        "--max-filesize", str(max_file_bytes),
        "--ignore-case" if case_insensitive else "--case-sensitive",
    ]
    if fixed_strings:
        flags.append("--fixed-strings")
    if multiline:
        flags.extend(["--multiline", "--multiline-dotall"])
    if include_ignored:
        flags.append("--no-ignore-vcs")
    # Glob and type filters are applied in Python for every backend so they
    # never disagree; ripgrep only prunes directory noise here.
    for skip in sorted(_SEARCH_SKIP_DIRS):
        flags.extend(["--glob", f"!{skip}/"])
    return flags


def _run_rg(cmd: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_RG_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None


# Runs ripgrep inside the distribution, or exits 127 when it is not installed
# there: a missing binary through ``wsl.exe --exec`` exits 1, which ripgrep
# uses for "no matches", and a silent empty answer is the worst outcome.
_WSL_RG_SCRIPT = 'command -v rg >/dev/null 2>&1 || exit 127; exec rg "$@"'


def _wsl_target(target: Path, *, platform: str | None = None) -> tuple[str, PurePosixPath] | None:
    """``(distro, linux_path)`` for a ``\\\\wsl.localhost`` target seen from Windows."""
    if (platform or sys.platform) != "win32":
        return None
    from navin.utils import wsl

    location = wsl.parse_unc(str(target))
    if location is None or not wsl.wsl_executable():
        return None
    return wsl.resolve_distro(location.distro) or location.distro, location.path


def _wsl_rg_scan(
    target: Path,
    pattern: str,
    *,
    fixed_strings: bool,
    case_insensitive: bool,
    include_ignored: bool,
    max_file_bytes: int,
    multiline: bool = False,
    platform: str | None = None,
) -> dict[str, list[tuple[int, str]]] | None:
    """Search a WSL project from Windows with the distribution's own ripgrep.

    Every file read through the ``\\\\wsl.localhost`` bridge crosses the 9P
    redirector, so the native module, a Windows ripgrep and the Python scan
    all crawl there. Inside the distribution the same search reads ext4
    directly. Returns None when the target is not a WSL path seen from
    Windows, or when the distribution has no ripgrep, so the caller tries the
    next backend. Paths come back spelled under ``target``, as the other
    backends report them.
    """
    found = _wsl_target(target, platform=platform)
    if found is None:
        return None
    distro, linux_path = found
    from navin.utils import wsl

    flags = _rg_scan_flags(
        fixed_strings=fixed_strings,
        case_insensitive=case_insensitive,
        include_ignored=include_ignored,
        max_file_bytes=max_file_bytes,
        multiline=multiline,
    )
    out = _run_rg([
        *wsl.command_prefix(distro), "sh", "-c", _WSL_RG_SCRIPT, "sh",
        *flags, "--", pattern, str(linux_path),
    ])
    if out is None or out.returncode not in (0, 1):
        return None

    # Every match line names its file: thousands of lines for a few hundred
    # files. Map each path once, by plain string work (pathlib relative_to
    # cost over five seconds for 20k lines of a common word).
    prefix = str(linux_path).rstrip("/") + "/"
    mapped: dict[str, str] = {}

    def to_target(text: str) -> str:
        hit = mapped.get(text)
        if hit is None:
            if text.startswith(prefix):
                hit = str(target.joinpath(*text[len(prefix):].split("/")))
            elif text == prefix[:-1]:
                hit = str(target)
            else:
                hit = wsl.to_unc(distro, text)
            mapped[text] = hit
        return hit

    return _parse_rg_json(out.stdout, to_target)


def _wsl_mtimes(
    target: Path, paths: list[str], *, platform: str | None = None,
) -> dict[str, float] | None:
    """Modification times of ``paths`` (spelled under ``target``), read inside WSL.

    Ranking needs one mtime per matched file. Asked through ``\\\\wsl.localhost``
    that is one 9P round trip per file: a common word over a large project
    took ten seconds of stats after a 0.3 s search. One ``stat`` inside the
    distribution answers for all of them. None when not applicable.
    """
    found = _wsl_target(target, platform=platform)
    if found is None or not paths:
        return None
    distro, linux_root = found
    from navin.utils import wsl

    by_linux: dict[str, str] = {}
    for text in paths:
        try:
            rel = Path(text).relative_to(target)
        except ValueError:
            continue
        by_linux[str(linux_root / PurePosixPath(rel.as_posix()))] = text
    if not by_linux:
        return None
    payload = "\0".join(by_linux).encode("utf-8") + b"\0"
    try:
        out = subprocess.run(  # noqa: S603
            [*wsl.command_prefix(distro), "xargs", "-0", "-r", "stat", "--printf=%Y %n\\0", "--"],
            input=payload, capture_output=True, timeout=_RG_TIMEOUT_S, **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    mtimes: dict[str, float] = {}
    for record in out.stdout.split(b"\0"):
        seconds, _, name = record.decode("utf-8", errors="replace").partition(" ")
        original = by_linux.get(name)
        if original is not None and seconds.isdigit():
            mtimes[original] = float(seconds)
    return mtimes


def _parse_rg_json(
    stdout: str, map_path: Callable[[str], str] | None = None,
) -> dict[str, list[tuple[int, str]]]:
    hits: dict[str, list[tuple[int, str]]] = {}
    total = 0
    for raw in stdout.splitlines():
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
        if map_path is not None:
            path_text = map_path(path_text)
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


def _wsl_rg_files(
    target: Path, *, include_ignored: bool, platform: str | None = None,
) -> list[str] | None:
    """``_rg_files`` for a WSL project seen from Windows, run inside the distribution.

    Listing through ``\\\\wsl.localhost`` reads every directory over the 9P
    bridge, whatever the backend. None when the target is not such a path or
    the distribution has no ripgrep, so the caller lists another way. Paths
    come back spelled under ``target``.
    """
    found = _wsl_target(target, platform=platform)
    if found is None:
        return None
    distro, linux_path = found
    from navin.utils import wsl

    flags = ["--files", "--null", "--hidden", "--no-follow", "--no-messages"]
    if include_ignored:
        flags.append("--no-ignore-vcs")
    for skip in sorted(_SEARCH_SKIP_DIRS):
        flags.extend(["--glob", f"!{skip}/"])
    out = _run_rg([
        *wsl.command_prefix(distro), "sh", "-c", _WSL_RG_SCRIPT, "sh",
        *flags, "--", str(linux_path),
    ])
    if out is None or out.returncode not in (0, 1):
        return None
    rows: list[str] = []
    for chunk in out.stdout.split("\0"):
        if not chunk:
            continue
        try:
            rows.append(str(target / PurePosixPath(chunk).relative_to(linux_path)))
        except ValueError:
            rows.append(wsl.to_unc(distro, chunk))
        if len(rows) >= _WALK_MAX_ENTRIES:
            break
    return rows


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
    from navin.utils import wsl
    from navin.utils.git_argv import git_route

    route = git_route(root)
    if route is None:
        return set()
    # Through wsl.exe, git reads Linux paths: translate them there and back.
    spelled: dict[str, Path] = {}
    for p in paths:
        location = wsl.parse_unc(str(p)) if route.distro else None
        spelled[location.posix if location else str(p)] = p
    try:
        out = subprocess.run(  # noqa: S603
            [*route.argv, "check-ignore", "--stdin", "-z"],
            input="\0".join(spelled),
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
    return {spelled.get(chunk, Path(chunk)) for chunk in out.stdout.split("\0") if chunk}


def _git_visible_files(root: Path, target: Path) -> list[Path] | None:
    """The files git sees under ``target`` (tracked plus untracked, ignores applied).

    The pure-Python fallback (no Rust scanner, no ripgrep) used to walk the
    whole tree and ask ``git check-ignore`` about every entry: on this
    repository that is 137 000 paths, of which 131 000 are build output, and
    the question alone took 11 s, with the walk and the reads 14 s per grep.
    ``git ls-files`` answers from the index in 15 ms with exactly the files
    ripgrep would search, so the fallback is as fast as the real backends on
    anything git tracks. ``None`` means not a work tree: the caller walks.
    """
    from navin.utils.git_argv import git_route

    route = git_route(root)
    if route is None or route.distro:
        # Through wsl.exe the path spellings differ on both sides; the walk
        # plus check-ignore path handles that case and stays correct.
        return None
    try:
        rel = target.relative_to(root).as_posix() if target != root else "."
    except ValueError:
        return None
    try:
        out = subprocess.run(
            [*route.argv, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", rel],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_RG_TIMEOUT_S,
            cwd=str(root),
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    skip = _SEARCH_SKIP_DIRS
    files: list[Path] = []
    for chunk in out.stdout.split("\0"):
        if not chunk:
            continue
        parts = chunk.split("/")
        if any(part in skip for part in parts[:-1]):
            continue
        files.append(root / chunk)
    return files[:_WALK_MAX_ENTRIES]


def _rel_posix(file_path: Path, root: Path) -> str | None:
    """``file_path`` relative to ``root`` with forward slashes, by string slicing.

    ``Path.relative_to`` re-parses both paths on every call: 1.6 s of a 3.4 s
    pure-Python grep over 5 600 files went there.
    """
    text = str(file_path)
    prefix = str(root)
    if text == prefix:
        return "."
    if not prefix.endswith(os.sep):
        prefix += os.sep
    if not text.startswith(prefix):
        try:
            return file_path.relative_to(root).as_posix()
        except ValueError:
            return None
    rel = text[len(prefix):]
    return rel.replace(os.sep, "/") if os.sep != "/" else rel


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
        self, target: Path, *, include_dirs: bool, include_ignored: bool, root: Path | None = None,
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
            files = _wsl_rg_files(target, include_ignored=include_ignored)
            if files is None:
                files = _rg_files(target, include_ignored=include_ignored)
            if files is None and not include_ignored:
                # No ripgrep: git's own file list is the same answer in 15 ms,
                # against a walk of 137 000 entries here (0.5 s and more).
                visible = _git_visible_files(root or target, target)
                if visible is not None:
                    files = [str(path) for path in visible]
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
            target, include_dirs=include_dirs, include_ignored=include_ignored, root=root,
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


_SYMBOL_QUERY = re.compile(
    r"^(?:(?P<decl>def|class|function|func|fn|interface|type)\s+)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\b[\\(]*$"
)
_INDEX_HINT_LIMIT = 2
# The index parses source code; docs and config matches get no nudge.
_CODE_FILE = re.compile(
    r"\.(?:py|pyi|ts|tsx|js|jsx|mjs|go|rs|java|kt|rb|php|cs|swift|c|cc|cpp|h|hpp|vue|svelte)\b"
)


def symbol_index_hint(pattern: str) -> str:
    """Point a symbol grep to code_index, which answers from parsed code.

    Sessions showed grep and read_file for every "where is X / who calls X"
    question while code_index sat unused. The nudge only fires for patterns
    that are clearly identifiers (CamelCase, snake_case, a declaration).
    """
    name = symbol_query_name(pattern)
    if name is None:
        return ""
    match = _SYMBOL_QUERY.match((pattern or "").strip())
    action = "definition" if match is not None and match.group("decl") else "references"
    return (
        f"\n\nTip: `code_index action={action} name={name}` answers this from parsed code "
        f"(also callers, and impact before changing a signature)."
    )


def symbol_query_name(pattern: str) -> str | None:
    """The identifier a grep pattern asks about, or None for plain text."""
    match = _SYMBOL_QUERY.match((pattern or "").strip())
    if match is None:
        return None
    name = match.group("name")
    # TODO / FIXME / HTTP are words to find, not symbols to resolve.
    shouting = name.isupper() and "_" not in name
    identifier = not shouting and ("_" in name or "." in name or any(char.isupper() for char in name[1:]))
    if not (match.group("decl") or (identifier and len(name) >= 4)):
        return None
    return name


_INDEX_FIRST_LIMIT = 6


def index_first_lines(root: Path, pattern: str) -> list[str]:
    """Definitions of the identifier in ``pattern`` from the already-loaded index.

    The agent greps for a symbol name a hundred times a day: when the index of
    this workspace is in memory, the parsed definitions come first, in about a
    millisecond, with their exact line and kind. The text matches follow as
    usual, so nothing is lost; on a slow filesystem (a WSL project opened from
    Windows) this header is often the whole answer. The index is never built
    or refreshed here: a grep must stay a grep when no index is warm.
    """
    name = symbol_query_name(pattern)
    if name is None:
        return []
    from navin.index.service import loaded_index

    index = loaded_index(root)
    if index is None:
        return []
    try:
        locations = index.definition(name)
        if not locations and index.revalidate(force=True):
            # The agent greps for what it just wrote: one stat walk (tens of
            # milliseconds) catches a file saved since the last look.
            locations = index.definition(name)
    except (OSError, RuntimeError, ValueError):
        # The index is a bonus here: a grep stays a grep when it cannot answer.
        return []
    if not locations:
        return []
    shown = locations[:_INDEX_FIRST_LIMIT]
    header = (
        f"Index: {len(locations)} definition(s) of '{name}' (parsed code)"
        + (f", first {len(shown)}" if len(locations) > len(shown) else "")
        + ":"
    )
    callers_hint = f"  (code_index action=callers name={name} lists who calls it)"
    return [header, *(f"  {loc.render()}" for loc in shown), callers_hint, ""]


class GrepTool(_SearchTool):
    """Search file contents using a regex-like pattern."""
    _scopes = {"core", "subagent"}
    _index_hints_shown = 0

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
            "include_ignored to search those too). Several searches to run: "
            "pass them all in `patterns` in ONE call, never one call per pattern. "
            "For a symbol name (where is X defined, who calls X) the answer leads "
            "with the parsed definitions from the code index when it is loaded; "
            "code_index action=definition/references/callers gives the full picture."
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
                "patterns": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 8,
                    "description": (
                        "Several patterns searched in ONE call (instead of pattern), "
                        "with the same path, glob and options; each gets its own "
                        "section. Never one call per pattern."
                    ),
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
        *, mtime: float | None = None,
    ) -> _FileHit | None:
        """Build an empty hit if the file passes the glob and type filters.

        ``mtime`` is given when the caller already knows it (read inside WSL).
        """
        try:
            rel_path = file_path.relative_to(root).as_posix()
        except ValueError:
            return None
        if glob and not _match_glob(rel_path, file_path.name, glob):
            return None
        if not _matches_type(file_path.name, file_type):
            return None
        if mtime is None:
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
        deadline: float | None = None,
    ) -> tuple[list[_FileHit], int | None, int | None, bool]:
        """Return (hits, skipped_binary, skipped_large, stopped_early).

        The skip counts are None when the native or ripgrep backend did the
        scan: both enforce the same binary and size limits themselves but do
        not say how many files they passed over, and reporting a fabricated
        zero read as "nothing was skipped". Unknown is the honest answer, so
        no skip note is printed on those paths; only the Python fallback
        counts exactly.

        ``stopped_early`` is True when the Python fallback reached
        ``deadline`` (a ``time.monotonic`` value) before reading every file:
        the hits are then only those found in the part already read.
        """
        # A WSL project seen from Windows: search inside the distribution
        # before anything has to read the files through the 9P bridge.
        scanned = _wsl_rg_scan(
            target,
            pattern,
            fixed_strings=fixed_strings,
            case_insensitive=case_insensitive,
            include_ignored=include_ignored,
            max_file_bytes=self._MAX_FILE_BYTES,
            multiline=multiline,
        )
        # The Rust scanner matches line by line; a spanning pattern goes to
        # ripgrep (--multiline) or to the whole-file Python search below.
        if scanned is None and not multiline:
            scanned = _native_scan(
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
        # Inside WSL, one stat for every matched file instead of one 9P
        # round trip each (see _wsl_mtimes).
        mtimes = _wsl_mtimes(target, list(scanned)) if scanned else None
        if scanned is not None:
            hits: list[_FileHit] = []
            for path_text, matches in scanned.items():
                hit = self._hit_for(
                    Path(path_text), root, glob, file_type,
                    mtime=None if mtimes is None else mtimes.get(path_text, 0.0),
                )
                if hit is None:
                    continue
                hit.matches = sorted(matches)
                hits.append(hit)
            return hits, None, None, False

        def out_of_time() -> bool:
            return deadline is not None and time.monotonic() >= deadline

        hits = []
        skipped_binary = 0
        skipped_large = 0
        stopped = False
        # Git knows the visible files already (15 ms on a 5 600-file repo):
        # a walk of the whole tree plus check-ignore on every entry took 14 s
        # here, most of it on build output git ignores anyway.
        visible = None if include_ignored else _git_visible_files(root, target)
        if visible is not None:
            candidates = visible
            ignored: set[Path] = set()
        else:
            # Walked lazily against the clock: over a slow mount the listing
            # alone can outlast the whole budget.
            candidates = []
            for file_path in self._iter_files(target):
                if out_of_time():
                    stopped = True
                    break
                candidates.append(file_path)
            ignored = set() if include_ignored else _gitignored_paths(root, candidates)
        for file_path in candidates:
            if out_of_time():
                stopped = True
                break
            if file_path in ignored:
                continue
            # The glob and type filters first, on the path string: they cut
            # most files, and they cost nothing next to a read.
            rel_path = _rel_posix(file_path, root)
            if rel_path is None:
                continue
            name = rel_path.rsplit("/", 1)[-1]
            if glob and not _match_glob(rel_path, name, glob):
                continue
            if not _matches_type(name, file_type):
                continue
            content, skip_reason = _read_text_file(file_path, self._MAX_FILE_BYTES)
            if content is None:
                if skip_reason == "large":
                    skipped_large += 1
                else:
                    skipped_binary += 1
                continue
            if multiline:
                found = _multiline_matches(content, regex)
            elif regex.search(content) is None:
                # One scan of the whole file instead of one per line: the
                # common case of a file without the needle costs a C call.
                continue
            else:
                found = [
                    (idx, line) for idx, line in enumerate(content.splitlines(), start=1)
                    if regex.search(line)
                ]
            if not found:
                continue
            hit = self._hit_for(file_path, root, None, None)
            if hit is None:
                continue
            hit.matches.extend(found)
            hits.append(hit)
        return hits, skipped_binary, skipped_large, stopped

    _MAX_PATTERNS = 8
    # The whole reply stays under the tool result limit (16 000 characters by
    # default), or it is written to disk and has to be read back.
    _BATCH_CHARS = 12_000

    async def execute(
        self,
        pattern: str | None = None,
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
        patterns: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        options: dict[str, Any] = {
            "path": path, "glob": glob, "type": type, "case_insensitive": case_insensitive,
            "fixed_strings": fixed_strings, "output_mode": output_mode,
            "context_before": context_before, "context_after": context_after,
            "max_matches": max_matches, "max_results": max_results, "head_limit": head_limit,
            "offset": offset, "sort": sort, "include_ignored": include_ignored,
            "multiline": multiline,
            # One budget for the whole call, so eight patterns cannot each
            # spend it in turn.
            "deadline": time.monotonic() + _FALLBACK_BUDGET_S,
        }
        if patterns:
            if pattern:
                return ToolResult.error("Error: pass either pattern or patterns, not both.")
            if len(patterns) > self._MAX_PATTERNS:
                return ToolResult.error(f"Error: at most {self._MAX_PATTERNS} patterns per call.")
            share = max(1_500, self._BATCH_CHARS // len(patterns))
            sections: list[str] = []
            succeeded = 0
            for item in patterns:
                if not isinstance(item, str) or not item:
                    sections.append("=== (empty pattern) ===")
                    continue
                result = await self._search_once(item, **options)
                text = str(result)
                if len(text) > share:
                    text = text[:share] + "\n(section truncated: narrow this search with path, glob or head_limit)"
                sections.append(f"=== pattern: {item} ===\n{text}")
                succeeded += 0 if getattr(result, "is_error", False) else 1
            body = "\n\n".join(sections)
            return ToolResult(body) if succeeded else ToolResult.error(body)
        if not pattern:
            return ToolResult.error("Error: grep requires 'pattern' (or 'patterns' for several searches).")
        return await self._search_once(pattern, **options)

    async def _search_once(
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
        deadline: float | None = None,
    ) -> str:
        try:
            if deadline is None:
                deadline = time.monotonic() + _FALLBACK_BUDGET_S
            target = await self._bound_path(path or ".", write=False)
            missing_path = None
            recovery_note = ""
            if not target.exists():
                missing_path = self._missing_path_msg("Path", path or ".", target)
                workspace = self._display_workspace()
                # An invented filename should not start a chain of guesses.
                # Search its existing project directory for the same symbols,
                # and state the wider scope explicitly when matches are found.
                if (
                    not target.suffix
                    or workspace is None
                    or not target.is_relative_to(workspace)
                    or not target.parent.is_dir()
                ):
                    return missing_path
                target = target.parent
                recovery_note = (
                    f"[Note: {path} does not exist. Searched {self._display(target)} "
                    "for the same pattern instead.]\n\n"
                )
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

            hits, skipped_binary, skipped_large, stopped_early = await asyncio.to_thread(
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
                deadline=deadline,
            )
            hits = _sort_hits(hits, sort)
            if missing_path is not None and not hits:
                return missing_path
            states = self._file_states
            for hit in hits[:_HIT_FILES_REMEMBERED]:
                states.record_search_hits(hit.path, [line for line, _ in hit.matches])
            total_matches = sum(len(hit.matches) for hit in hits)

            blocks: list[str] = []
            if output_mode == "files_with_matches":
                if not hits:
                    result = _no_match_message(
                        pattern, path, target, case_insensitive, fixed_strings, glob, type
                    )
                else:
                    paged, truncated = _paginate(
                        [hit.display_path for hit in hits], limit, offset
                    )
                    result = "\n".join(paged) or (
                        f"No entries at offset {offset} ({len(hits)} files match); "
                        "lower the offset to see them"
                    )
            elif output_mode == "count":
                if not hits:
                    result = _no_match_message(
                        pattern, path, target, case_insensitive, fixed_strings, glob, type
                    )
                else:
                    paged_hits, truncated = _paginate(hits, limit, offset)
                    result = "\n".join(
                        f"{hit.display_path}: {len(hit.matches)}" for hit in paged_hits
                    ) or (
                        f"No entries at offset {offset} ({len(hits)} files match); "
                        "lower the offset to see them"
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
                if not blocks and total_matches and offset >= total_matches:
                    result = (
                        f"No entries at offset {offset} for pattern '{pattern}' "
                        f"({total_matches} matches in {len(hits)} files); "
                        "lower the offset to see them"
                    )
                elif not blocks:
                    result = _no_match_message(
                        pattern, path, target, case_insensitive, fixed_strings, glob, type
                    )
                else:
                    result = "\n\n".join(blocks)

            notes: list[str] = []
            if stopped_early:
                notes.append(
                    f"(partial: the search stopped after {_FALLBACK_BUDGET_S:.0f}s before reading "
                    "every file, so files not yet read may also match. No fast search backend "
                    "(ripgrep) is available here: narrow path or glob to search the rest)"
                )
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
            elif len(hits) >= _BROAD_PATTERN_FILES:
                notes.append(
                    f"(broad pattern: {len(hits)} files match. Search the exact identifier, "
                    "string or component name from the request, or scope path or glob, "
                    "before reading any of these files)"
                )
            if notes:
                result += "\n\n" + "\n".join(notes)
            if hits and output_mode in {"files_with_matches", "content"}:
                # Parsed definitions first when the index is warm: exact line
                # and kind in a millisecond, the text matches below as always.
                index_lines = index_first_lines(root, pattern)
                if index_lines:
                    result = "\n".join(index_lines) + result
                    # The header already says where the symbol lives; the
                    # separate tip would repeat it.
                    self._index_hints_shown = _INDEX_HINT_LIMIT
            search_key = json.dumps(
                [pattern, str(target), glob, type, case_insensitive, fixed_strings, output_mode,
                 context_before, context_after, limit, offset, sort, include_ignored, multiline],
                default=str,
            )
            if states.repeats_search(search_key, result):
                result += (
                    "\n\n(Same search as an earlier call in this session, same result: nothing "
                    "changed since. Act on that result, or change the pattern, path or glob, "
                    "instead of running it again.)"
                )
            hint = (
                symbol_index_hint(pattern)
                if self._index_hints_shown < _INDEX_HINT_LIMIT and _CODE_FILE.search(result)
                else ""
            )
            if hint:
                self._index_hints_shown += 1
            return recovery_note + result + hint
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error searching files: {e}")
