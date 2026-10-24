"""Project instruction files beyond the workspace root.

The context builder already loads the workspace-root ``AGENTS.md`` (and its
``.navin/`` copy, with ``@path`` imports resolved). This module adds two
sources, governed by ``NAVIN_INSTRUCTION_FILES``:

- ``agents-md`` (default): root files as before, plus ancestor files and
  nested files.
- ``root-only``: root files only, as before.
- ``none``: project instruction files are dropped from the context.

Ancestor files are the ``AGENTS.md`` / ``.navin/AGENTS.md`` of directories
above the workspace, up to the enclosing git root (or the filesystem root),
appended after the root files with the nearest directory last.

Nested files are the ones of directories between the workspace root and a
file the agent reads or edits. They ride on that tool result, once per
session, and never repeat a file already in the context (same resolved path,
or same content).
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

ENV_VAR = "NAVIN_INSTRUCTION_FILES"
MODES = ("agents-md", "root-only", "none")
DEFAULT_MODE = "agents-md"

FILE_NAMES = ("AGENTS.md", ".navin/AGENTS.md")
ROOT_FILE_NAMES = ("AGENTS.md", ".navin/AGENTS.md", "CLAUDE.md")
TOOLS = frozenset({"read_file", "edit_file", "write_file", "apply_patch"})
# Tools whose own result already carries the nested files.
INLINE_TOOLS = frozenset({"read_file"})

MAX_FILE_BYTES = 32 * 1024
MAX_ATTACHMENT_BYTES = 64 * 1024
MAX_SESSIONS = 64
_IMPORT_DEPTH = 3
_AT_IMPORT = re.compile(r"^@(\S+)\s*$", re.MULTILINE)

Resolver = Callable[[str, Path], str]

# Disk answers reused for a short while. attach() runs after every read and
# edit; on a WSL project opened from Windows each resolve() or is_file()
# crosses the 9P bridge (about 20 ms), and the walk repeated them for every
# directory of every path, on every call: 33 of 182 captured event-loop
# blocks of one desktop day were here. Instruction files rarely change; an
# agent write to one clears the cache at once, others show up within the TTL.
CACHE_TTL_S = 30.0
_CACHE_MAX = 4096
_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}
_LOCK = threading.Lock()
_MISSING = object()


def _cached(key: tuple[Any, ...], compute: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key, _MISSING)
    if hit is not _MISSING and hit[0] > now:  # type: ignore[index]
        return hit[1]  # type: ignore[index]
    value = compute()
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = (now + CACHE_TTL_S, value)
    return value


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def mode() -> str:
    value = (os.environ.get(ENV_VAR) or "").strip().lower()
    return value if value in MODES else DEFAULT_MODE


# -- helpers ----------------------------------------------------------------

def _resolve(path: Path) -> Path:
    return _cached(("resolve", str(path)), lambda: _resolve_now(path))


def _resolve_now(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _read(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def _hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _cap(text: str, limit: int = MAX_FILE_BYTES) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    kept = raw[:limit].decode("utf-8", errors="ignore")
    return kept + f"\n\n[truncated: {len(raw) - limit} more bytes]"


def _files_in(directory: Path, *, skip_navin: bool = False, fallback: bool = False) -> list[Path]:
    return list(_cached(
        ("files", str(directory), skip_navin, fallback),
        lambda: tuple(_files_in_now(directory, skip_navin=skip_navin, fallback=fallback)),
    ))


def _files_in_now(directory: Path, *, skip_navin: bool, fallback: bool) -> list[Path]:
    out = []
    for name in FILE_NAMES:
        if skip_navin and name.startswith(".navin/"):
            continue
        candidate = directory / name
        if candidate.is_file():
            out.append(candidate)
    if not out and fallback and (directory / "CLAUDE.md").is_file():
        # Same per-directory fallback as the read tool's nested lookup.
        out.append(directory / "CLAUDE.md")
    return out


def _imports_of(path: Path, text: str, seen: set[Path], depth: int = 0) -> None:
    """Add every file *text* ``@``-imports (transitively) to *seen*."""
    if depth >= _IMPORT_DEPTH or "@" not in text:
        return
    for match in _AT_IMPORT.finditer(text):
        target = _resolve(path.parent / match.group(1))
        if target in seen:
            continue
        body = _read(target)
        if body is None:
            continue
        seen.add(target)
        _imports_of(target, body, seen, depth + 1)


def _label(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


# -- ancestors --------------------------------------------------------------

def ancestor_files(workspace: Path) -> list[Path]:
    """Instruction files above *workspace*, farthest first, nearest last.

    The walk stops at the enclosing git root (included) or the filesystem
    root. A workspace that is itself a git root has no ancestors. The home
    directory's ``.navin/`` is Navin's own state, not a project file.
    """
    root = _resolve(workspace)
    if (root / ".git").exists():
        return []
    home = _resolve(Path.home())
    found: list[list[Path]] = []
    for directory in root.parents:
        found.append(_files_in(directory, skip_navin=directory == home))
        if (directory / ".git").exists():
            break
    return [p for group in reversed(found) for p in group]


def _root_loaded(workspace: Path) -> tuple[set[Path], set[str]]:
    """Paths and content hashes the root context already carries."""
    root = _resolve(workspace)
    paths: set[Path] = set()
    hashes: set[str] = set()
    for name in ROOT_FILE_NAMES:
        path = root / name
        text = _read(path)
        if text is None:
            continue
        resolved = _resolve(path)
        paths.add(resolved)
        _imports_of(resolved, text, paths)
    for path in list(paths):
        text = _read(path)
        if text is not None and text.strip():
            hashes.add(_hash(text))
    return paths, hashes


def ancestor_section(workspace: Path, resolver: Resolver | None = None) -> str:
    """Rendered ancestor files for the system prompt ("" when none)."""
    paths, hashes = _root_loaded(workspace)
    parts: list[str] = []
    for path in ancestor_files(workspace):
        resolved = _resolve(path)
        text = _read(path)
        if resolved in paths or text is None or not text.strip() or _hash(text) in hashes:
            continue
        paths.add(resolved)
        hashes.add(_hash(text))
        body = resolver(text, path) if resolver else text
        parts.append(f"## AGENTS.md ({path.as_posix()})\n\n{_cap(body)}")
    return "\n\n".join(parts)


def apply_to_bootstrap(parts: list[str], workspace: Path, resolver: Resolver | None = None) -> list[str]:
    """Adjust the root bootstrap sections for the configured mode.

    ``none`` drops the sections that come from the project's own root
    instruction files; ``agents-md`` appends ancestor files; ``root-only``
    leaves the list untouched.
    """
    current = mode()
    if current == "root-only":
        return parts
    if current == "none":
        root = Path(workspace)
        project = set()
        for name in ("AGENTS.md", "CLAUDE.md"):
            text = _read(root / name)
            if text is not None:
                body = resolver(text, root / name) if resolver else text
                project.add(f"## {name}\n\n{body}")
        return [p for p in parts if p not in project]
    try:
        extra = ancestor_section(Path(workspace), resolver)
    except OSError:
        extra = ""
    return [*parts, extra] if extra else parts


# -- nested files -----------------------------------------------------------

def nested_files_for(path: Path | str, workspace: Path) -> list[Path]:
    """Instruction files between *workspace* (excluded) and *path*, outermost first."""
    root = _resolve(workspace)
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    target = _resolve(target)
    directory = target if target.is_dir() else target.parent
    try:
        rel = directory.relative_to(root)
    except ValueError:
        return []
    out: list[Path] = []
    current = root
    for part in rel.parts:
        current = current / part
        if part in (".git", ".navin", "node_modules"):
            break
        out.extend(_files_in(current, fallback=True))
    return out


class _SessionSeen:
    def __init__(self) -> None:
        self.paths: set[Path] = set()
        self.hashes: set[str] = set()


_SESSIONS: OrderedDict[str, _SessionSeen] = OrderedDict()


def _session(key: str) -> _SessionSeen:
    with _LOCK:
        return _session_locked(key)


def _session_locked(key: str) -> _SessionSeen:
    seen = _SESSIONS.get(key)
    if seen is None:
        seen = _SESSIONS[key] = _SessionSeen()
        while len(_SESSIONS) > MAX_SESSIONS:
            _SESSIONS.popitem(last=False)
    else:
        _SESSIONS.move_to_end(key)
    return seen


def reset(session: str | None = None) -> None:
    clear_cache()
    with _LOCK:
        if session is None:
            _SESSIONS.clear()
        else:
            _SESSIONS.pop(session, None)


def _tool_paths(tool_name: str, params: Any) -> list[str]:
    if not isinstance(params, dict):
        return []
    out: list[str] = []
    if isinstance(params.get("path"), str):
        out.append(params["path"])
    for key in ("files", "edits"):
        items = params.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    out.append(item["path"])
    return [p for p in out if p.strip()]


def _root_context(root: Path) -> tuple[frozenset[Path], frozenset[str]]:
    """Root and ancestor files already in the prompt: paths and content hashes."""
    def compute() -> tuple[frozenset[Path], frozenset[str]]:
        paths, hashes = _root_loaded(root)
        for path in ancestor_files(root):
            paths.add(_resolve(path))
            body = _read(path)
            if body and body.strip():
                hashes.add(_hash(body))
        return frozenset(paths), frozenset(hashes)

    return _cached(("root", str(root)), compute)


_INSTRUCTION_NAMES = frozenset({"AGENTS.md", "CLAUDE.md"})


def attach(tool_name: str, params: Any, workspace: Path | None, session: str) -> tuple[str, list[str]]:
    """Model text and the list of attached file labels for one tool call."""
    if tool_name not in TOOLS or workspace is None or mode() != "agents-md":
        return "", []
    tool_paths = _tool_paths(tool_name, params)
    if tool_name != "read_file" and any(Path(p).name in _INSTRUCTION_NAMES for p in tool_paths):
        # The agent just wrote an instruction file: no stale answer for it.
        clear_cache()
    root = _resolve(Path(workspace))
    seen = _session(session or "default")
    root_paths, root_hashes = _root_context(root)
    blocks: list[str] = []
    labels: list[str] = []
    budget = MAX_ATTACHMENT_BYTES
    for raw in tool_paths:
        for path in nested_files_for(raw, root):
            if budget <= 0:
                break
            resolved = _resolve(path)
            if resolved in seen.paths or resolved in root_paths:
                continue
            text = _read(path)
            if text is None or not text.strip():
                seen.paths.add(resolved)
                continue
            digest = _hash(text)
            seen.paths.add(resolved)
            if digest in seen.hashes or digest in root_hashes:
                continue
            seen.hashes.add(digest)
            label = _label(resolved, root)
            block = f"## {label}\n\n{_cap(text)}"
            size = len(block.encode("utf-8"))
            if size > budget:
                block = f"## {label}\n\n{_cap(text, max(budget - 200, 0))}"
                size = budget
            budget -= size
            blocks.append(block)
            labels.append(label)
    if not blocks:
        return "", []
    if tool_name in INLINE_TOOLS:
        # The read tool already appends the nested files to its own result
        # (navin/agent/project_instructions.py); only the notice is new here.
        return "", labels
    text = (
        "Project instructions for this part of the tree (follow them like "
        "AGENTS.md instructions):\n\n" + "\n\n".join(blocks)
    )
    return text, labels


def attachment_for_tool(tool_name: str, params: Any, workspace: Path | None, session: str) -> str:
    """Text to append to the tool result for the model ("" when nothing new)."""
    return attach(tool_name, params, workspace, session)[0]


def notice(labels: list[str]) -> str:
    return "Loaded instructions: " + ", ".join(labels) if labels else ""
