# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""File discovery and incremental persistence for the code index.

Discovery prefers ``git ls-files`` so the index honors ``.gitignore`` exactly,
which is what users expect: build artifacts and vendored trees never pollute
symbol search. Non-git projects, and repos whose ``.gitignore`` is an opt-in
whitelist rather than an artifact filter, fall back to a filtered walk.

The cache lives outside the project (under the navin config directory) so
indexing never dirties the working tree or shows up in ``git status``.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.index.symbols import FileEntry, language_for
from navin.utils.git_argv import git_argv
from navin.utils.proc import no_window_kwargs

_GIT_TIMEOUT_S = 20
_MAX_FILES = 60_000
_CACHE_VERSION = 3

SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".tox", "dist",
    "build", ".next", ".nuxt", ".cache", ".parcel-cache", ".metadata",
    ".idea", ".vscode", "coverage", "htmlcov", "target", ".gradle",
    ".terraform", "vendor", "Pods", ".checkpoints", ".navin", "site-packages",
    ".turbo", ".svelte-kit", "bower_components", ".serverless", ".output",
})


@dataclass(frozen=True, slots=True)
class RefreshStats:
    """Outcome of one index refresh."""

    total: int
    parsed: int
    reused: int
    removed: int
    truncated: bool
    duration_ms: int
    discovery: str

    def summary(self) -> str:
        parts = [
            f"{self.total} files indexed",
            f"{self.parsed} parsed",
            f"{self.reused} reused from cache",
        ]
        if self.removed:
            parts.append(f"{self.removed} removed")
        if self.truncated:
            parts.append(f"truncated at {_MAX_FILES}")
        return ", ".join(parts) + f" ({self.duration_ms} ms, via {self.discovery})"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _git_files(root: Path) -> list[str] | None:
    """Tracked + untracked-but-not-ignored files, or None when not a git repo."""
    git = git_argv(root)
    if git is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603
            [
                *git, "ls-files",
                "--cached", "--others", "--exclude-standard", "-z",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    files = [item for item in out.stdout.split("\0") if item]
    # A submodule or empty repo can legitimately return nothing; treat an empty
    # listing as "git had no answer" so the walk fallback still finds files.
    return files or None


_DENY_ALL_PATTERNS = frozenset({"*", "/*", "**", "/**"})


def _uses_optin_gitignore(root: Path) -> bool:
    """Whether ``.gitignore`` ignores everything and whitelists a few exceptions.

    ``git ls-files`` is the best available answer to "what here is source and what
    is a build artifact" - right up to the point where ``.gitignore`` stops
    answering that question. A repo whose rules begin by ignoring everything is
    not describing artifacts, it is committing a narrow slice on purpose: navin's
    own agent workspace commits only ``memory/`` so Dream's snapshots stay
    readable, and its ``.gitignore`` starts with ``/*``.

    Inheriting that whitelist as the index's visibility filter is what made the
    agent blind to the projects it stores alongside its memory - the graph showed
    seven memory files and reported full coverage. So when the whitelist shape is
    detected, discovery walks the tree instead and relies on ``SKIP_DIRS``, which
    is the rule written for artifacts in the first place.
    """
    try:
        text = (root / ".gitignore").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(
        line.strip() in _DENY_ALL_PATTERNS
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )


def _is_agent_workspace(root: Path) -> bool:
    """Whether ``root`` is navin's own agent workspace rather than a code project.

    Only asked so that navin's bookkeeping can be left out of its own project map,
    and only answered from files navin itself scaffolds. The check has to be this
    specific because the noisy directory is called ``sessions``: skipping that name
    everywhere would hide a real Django or Rails app's source.
    """
    if (root / ".navin" / "SOUL.md").is_file() and (root / ".navin" / "memory").is_dir():
        return True
    # Legacy layout (brain files at the workspace root, pre-.navin migration).
    return (root / "SOUL.md").is_file() and (root / "memory").is_dir()


# Runtime state navin writes inside its workspace. Session files are named by
# base64-encoding the session key, so they surface in a graph as three dozen
# unreadable blobs that crowd out the handful of real documents beside them.
_AGENT_STATE_DIRS = frozenset({"sessions"})
_AGENT_STATE_FILES = frozenset({"memory/history.jsonl", ".navin/memory/history.jsonl"})


def _walk_files(root: Path) -> list[str]:
    agent_state = _is_agent_workspace(root)

    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        # navin's state is skipped at the workspace root only. A project checked
        # out *inside* that workspace may legitimately own a ``sessions/``
        # directory full of source, and hiding it would repeat the very blindness
        # the walk was added to fix.
        at_root = agent_state and base == root
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS
            and not (d.startswith(".") and d != ".github")
            and not (at_root and d in _AGENT_STATE_DIRS)
        ]
        for name in filenames:
            if name.startswith("."):
                continue
            rel = (base / name).relative_to(root).as_posix()
            if agent_state and rel in _AGENT_STATE_FILES:
                continue
            out.append(rel)
            # One past the cap, so discover_files can tell "exactly at the
            # cap" from "stopped early": a walk that returned exactly
            # _MAX_FILES entries used to make truncation undetectable.
            if len(out) > _MAX_FILES:
                return out
    return out


def discover_files(root: Path) -> tuple[list[str], str, bool]:
    """Return (relative paths, discovery method, truncated)."""
    files = None if _uses_optin_gitignore(root) else _git_files(root)
    method = "git"
    if files is None:
        files = _walk_files(root)
        method = "walk"
    else:
        files = [
            f for f in files
            if not any(part in SKIP_DIRS for part in f.split("/")[:-1])
        ]
    truncated = len(files) > _MAX_FILES
    if truncated:
        files = files[:_MAX_FILES]
    return files, method, truncated


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _slug_for(root: Path) -> str:
    resolved = str(root.resolve(strict=False))
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in root.name)[:40] or "project"
    return f"{slug}-{digest}"


def cache_path(root: Path) -> Path:
    """Stable per-project cache file under the navin config directory.

    Sibling caches (fulltext, semantic, notes) derive their names from it. The
    index itself is stored next to it as JSON Lines (see :func:`_index_path`).
    """
    from navin.config.loader import get_config_path

    return get_config_path().parent / "index" / f"{_slug_for(root)}.json"


def _index_path(path: Path) -> Path:
    """Where a cache is really stored: one JSON line per file.

    A single JSON document of 30-70 MB was parsed by one ``json.loads`` call,
    which holds the GIL for its whole run: a background warm froze the CLI
    and the gateway for 0.3-0.9 s at a time. Line by line, every other thread
    gets the interpreter back between files. A new name, not a new format
    under the old one, so an older build sharing this directory keeps its own
    cache instead of rebuilding it on every switch.
    """
    return path.with_suffix(".jsonl")


def refs_cache_path(root: Path) -> Path:
    """Reference cache, kept separate so it can be loaded only when queried.

    References outnumber symbols several times over. Splitting them out keeps
    definition lookups, search and the dependency graph as cheap as they were
    before the call graph existed.
    """
    return cache_path(root).with_name(f"{_slug_for(root)}-refs.jsonl")


_gc_pause_lock = threading.Lock()
_gc_pause_depth = 0
_gc_was_enabled = False


@contextmanager
def gc_paused() -> Iterator[None]:
    """Hold off the cyclic garbage collector while a cache is turned into objects.

    Loading a large index allocates millions of small objects, which triggered
    full collections of 12-125 ms each, every one holding the GIL: the CLI and
    the gateway froze while a background thread warmed the index. Paused, the
    same load ran 4x faster and never blocked another thread for more than
    ~10 ms. Reference counting still frees everything as usual; nested and
    concurrent loads share one pause.
    """
    global _gc_pause_depth, _gc_was_enabled
    with _gc_pause_lock:
        if _gc_pause_depth == 0:
            _gc_was_enabled = gc.isenabled()
            gc.disable()
        _gc_pause_depth += 1
    try:
        yield
    finally:
        with _gc_pause_lock:
            _gc_pause_depth -= 1
            if _gc_pause_depth == 0 and _gc_was_enabled:
                gc.enable()


def _write_lines(path: Path, header: dict[str, Any], files: Any) -> None:
    """Atomically write a header line, then one ``[rel, payload]`` line per file.

    ``files`` yields ``(rel, payload)`` pairs; failures are non-fatal by design.
    """
    target = _index_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent, delete=False, suffix=".tmp"
        ) as handle:
            handle.write(json.dumps({**header, "version": _CACHE_VERSION}, separators=(",", ":")))
            handle.write("\n")
            for rel, payload in files:
                handle.write(json.dumps([rel, payload], separators=(",", ":")))
                handle.write("\n")
            temp = Path(handle.name)
        temp.replace(target)
    except OSError:
        # An unwritable cache only costs performance, never correctness.
        pass


def _read_lines(path: Path) -> dict[str, Any]:
    """Per-file payloads of a cache written by :func:`_write_lines`, or {}."""
    files: dict[str, Any] = {}
    try:
        with _index_path(path).open(encoding="utf-8") as handle:
            header = json.loads(handle.readline() or "null")
            if not isinstance(header, dict) or header.get("version") != _CACHE_VERSION:
                return {}
            for line in handle:
                row = json.loads(line)
                if isinstance(row, list) and len(row) == 2 and isinstance(row[1], dict):
                    files[str(row[0])] = row[1]
    except (OSError, ValueError):
        # A torn or foreign file is a cold cache, never an error.
        return {}
    return files


def load_cache(root: Path) -> dict[str, FileEntry]:
    with gc_paused():
        return {
            rel: FileEntry.from_json(rel, entry)
            for rel, entry in _read_lines(cache_path(root)).items()
        }


def save_cache(root: Path, entries: dict[str, FileEntry], meta: dict[str, Any]) -> None:
    """Atomically persist the index; failures are non-fatal by design."""
    _write_lines(
        cache_path(root),
        {"root": str(root.resolve(strict=False)), "meta": meta},
        ((rel, entry.to_json()) for rel, entry in entries.items()),
    )


# Coalesced background saves: the full cache is megabytes of JSON (seconds
# to encode on a large repo), and revalidate() runs on the turn's critical
# path after every agent edit. Only the latest snapshot per root is written.
_SAVE_DELAY_S = 1.0
_pending_saves: dict[str, tuple[Path, dict[str, FileEntry], dict[str, Any]]] = {}
_save_timers: dict[str, threading.Timer] = {}
_save_lock = threading.Lock()


def save_cache_soon(root: Path, entries: dict[str, FileEntry], meta: dict[str, Any]) -> None:
    """Schedule :func:`save_cache` off the caller's thread, latest snapshot wins.

    ``entries`` must not be mutated afterwards; the index replaces its dict on
    every change instead of editing it in place.
    """
    key = str(root)
    with _save_lock:
        _pending_saves[key] = (root, entries, meta)
        if key in _save_timers:
            return
        timer = threading.Timer(_SAVE_DELAY_S, _flush_pending_save, args=(key,))
        timer.daemon = True
        _save_timers[key] = timer
    timer.start()


def _flush_pending_save(key: str) -> None:
    with _save_lock:
        timer = _save_timers.pop(key, None)
        pending = _pending_saves.pop(key, None)
    if timer is not None:
        timer.cancel()
    if pending is not None:
        save_cache(*pending)


def flush_pending_save(root: Path) -> None:
    """Write a scheduled snapshot now, so a following disk read sees it."""
    _flush_pending_save(str(root))


def load_refs_cache(root: Path) -> dict[str, dict[str, Any]]:
    """Raw per-file reference payloads, validated against file stamps later."""
    with gc_paused():
        return _read_lines(refs_cache_path(root))


def save_refs_cache(root: Path, entries: dict[str, FileEntry]) -> None:
    """Persist references for every entry, including the empty ones.

    A file with no references still needs a record: without one it would miss
    the cache on every load, be re-parsed, and trigger a full rewrite each time.
    """
    _write_lines(
        refs_cache_path(root),
        {"root": str(root.resolve(strict=False))},
        ((rel, entry.refs_to_json()) for rel, entry in entries.items()),
    )


def indexable(rel_path: str) -> bool:
    """True when the file has a language the index understands."""
    return bool(language_for(rel_path))
