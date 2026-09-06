"""File discovery and incremental persistence for the code index.

Discovery prefers ``git ls-files`` so the index honors ``.gitignore`` exactly,
which is what users expect: build artifacts and vendored trees never pollute
symbol search. Non-git projects, and repos whose ``.gitignore`` is an opt-in
whitelist rather than an artifact filter, fall back to a filtered walk.

The cache lives outside the project (under the navin config directory) so
indexing never dirties the working tree or shows up in ``git status``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.index.symbols import FileEntry, language_for
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
    git = shutil.which("git")
    if git is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603
            [
                git, "-C", str(root), "ls-files",
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
    """Stable per-project cache file under the navin config directory."""
    from navin.config.loader import get_config_path

    return get_config_path().parent / "index" / f"{_slug_for(root)}.json"


def refs_cache_path(root: Path) -> Path:
    """Reference cache, kept separate so it can be loaded only when queried.

    References outnumber symbols several times over. Splitting them out keeps
    definition lookups, search and the dependency graph as cheap as they were
    before the call graph existed.
    """
    return cache_path(root).with_name(f"{_slug_for(root)}-refs.json")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write JSON; failures are non-fatal by design."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
        ) as handle:
            json.dump(payload, handle, separators=(",", ":"))
            temp = Path(handle.name)
        temp.replace(path)
    except OSError:
        # An unwritable cache only costs performance, never correctness.
        pass


def _read_versioned(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("version") != _CACHE_VERSION:
        return {}
    return raw


def load_cache(root: Path) -> dict[str, FileEntry]:
    files = _read_versioned(cache_path(root)).get("files")
    if not isinstance(files, dict):
        return {}
    out: dict[str, FileEntry] = {}
    for rel, entry in files.items():
        if isinstance(entry, dict):
            out[str(rel)] = FileEntry.from_json(str(rel), entry)
    return out


def save_cache(root: Path, entries: dict[str, FileEntry], meta: dict[str, Any]) -> None:
    """Atomically persist the index; failures are non-fatal by design."""
    _write_json(
        cache_path(root),
        {
            "version": _CACHE_VERSION,
            "root": str(root.resolve(strict=False)),
            "meta": meta,
            "files": {rel: entry.to_json() for rel, entry in entries.items()},
        },
    )


def load_refs_cache(root: Path) -> dict[str, dict[str, Any]]:
    """Raw per-file reference payloads, validated against file stamps later."""
    files = _read_versioned(refs_cache_path(root)).get("files")
    if not isinstance(files, dict):
        return {}
    return {
        str(rel): payload
        for rel, payload in files.items()
        if isinstance(payload, dict)
    }


def save_refs_cache(root: Path, entries: dict[str, FileEntry]) -> None:
    """Persist references for every entry, including the empty ones.

    A file with no references still needs a record: without one it would miss
    the cache on every load, be re-parsed, and trigger a full rewrite each time.
    """
    _write_json(
        refs_cache_path(root),
        {
            "version": _CACHE_VERSION,
            "root": str(root.resolve(strict=False)),
            "files": {rel: entry.refs_to_json() for rel, entry in entries.items()},
        },
    )


def indexable(rel_path: str) -> bool:
    """True when the file has a language the index understands."""
    return bool(language_for(rel_path))
