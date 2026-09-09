# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared path helpers for workspace-scoped tools."""

import difflib
import os
import shutil
import subprocess
from pathlib import Path

from navin.config.paths import get_media_dir
from navin.security.workspace_policy import (
    is_path_within,
    project_rooted_path,
    resolve_allowed_path,
)
from navin.utils.proc import no_window_kwargs

__all__ = [
    "closest_existing_match",
    "is_under",
    "project_rooted_path",
    "resolve_workspace_path",
]

_MATCH_SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", "target", ".navin", ".next", ".nuxt",
    ".cache", "vendor", ".turbo",
})
# Non-git fallback only. git ls-files is the fast path on real projects.
_MATCH_MAX_DIRS = 80
_GIT_LS_TIMEOUT_S = 2.0


def _git_basename_matches(root: Path, name: str) -> list[Path] | None:
    """Tracked/unignored files with this basename, or None when git cannot answer."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603
            [
                git, "-C", str(root), "ls-files",
                "--cached", "--others", "--exclude-standard", "-z",
            ],
            capture_output=True,
            timeout=_GIT_LS_TIMEOUT_S,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    matches: list[Path] = []
    for rel in completed.stdout.split(b"\0"):
        if not rel:
            continue
        text = rel.decode("utf-8", errors="replace")
        if Path(text).name != name:
            continue
        matches.append(root / text)
        if len(matches) > 1:
            return matches
    return matches


def _walk_basename_matches(root: Path, name: str) -> list[Path]:
    matches: list[Path] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        scanned += 1
        if scanned > _MATCH_MAX_DIRS:
            break
        dirnames[:] = [
            d for d in dirnames
            if d not in _MATCH_SKIP_DIRS and not d.startswith(".")
        ]
        if name in filenames:
            matches.append(Path(dirpath) / name)
            if len(matches) > 1:
                return matches
    return matches


def closest_existing_match(fp: Path, root: Path | None = None) -> Path | None:
    """Unambiguous stand-in for a missing file path, or None.

    Models routinely invent descriptive names for generated artifacts
    ("review-report-my-topic.html" when the tool wrote
    "review-report-20260810-1123.html") or look for the right basename in the
    wrong directory. Same directory first; then a unique basename via
    ``git ls-files`` (or a short skipped walk when git is absent). Never a
    full descent into node_modules.
    """
    parent = fp.parent
    if parent.is_dir() and fp.suffix:
        stem = fp.stem
        try:
            candidates = [
                f for f in parent.iterdir()
                if f.is_file() and f.suffix == fp.suffix and f.name != fp.name
            ]
        except OSError:
            candidates = []

        def _is_strong(candidate: Path) -> bool:
            # The invented and the real stem rarely have similar lengths
            # (descriptive guess vs stamped name), so the prefix bar is set
            # from the shorter of the two.
            min_prefix = max(8, min(len(candidate.stem), len(stem)) // 2)
            if len(os.path.commonprefix([candidate.stem, stem])) >= min_prefix:
                return True
            return difflib.SequenceMatcher(None, candidate.stem, stem).ratio() >= 0.75

        strong = [f for f in candidates if _is_strong(f)]
        if strong:
            try:
                return max(strong, key=lambda f: f.stat().st_mtime)
            except OSError:
                return strong[0]

    if root is None or not fp.name:
        return None
    root = Path(root).expanduser()
    if not root.is_dir():
        return None
    matches = _git_basename_matches(root, fp.name)
    if matches is None:
        matches = _walk_basename_matches(root, fp.name)
    return matches[0] if len(matches) == 1 else None


def is_under(path: Path, directory: Path) -> bool:
    """Return True when path resolves under directory."""
    return is_path_within(path, directory)


def resolve_workspace_path(
    path: str,
    workspace: Path | None = None,
    allowed_dir: Path | None = None,
    extra_allowed_dirs: list[Path] | None = None,
    extra_allowed_files: list[Path] | None = None,
    include_media_dir: bool = True,
) -> Path:
    """Resolve path against workspace and enforce allowed directory containment."""
    media_roots = [get_media_dir()] if include_media_dir else []
    extra_roots = [*media_roots, *(extra_allowed_dirs or [])] if allowed_dir else None
    path = project_rooted_path(
        path,
        workspace,
        [allowed_dir, *(extra_roots or [])] if allowed_dir is not None else [],
    )
    return resolve_allowed_path(
        path,
        workspace=workspace,
        allowed_root=allowed_dir,
        extra_allowed_roots=extra_roots,
        extra_allowed_files=extra_allowed_files,
    )
