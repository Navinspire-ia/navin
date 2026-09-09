# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Workspace path boundary helpers.

These helpers are application-level guards.  They make path decisions
consistent across tools, but they are not a replacement for an OS sandbox.
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path
from typing import Iterable

WORKSPACE_BOUNDARY_NOTE = (
    " (restrict to workspace is on; a card in the chat asks before "
    "leaving the project)"
)


class WorkspaceBoundaryError(PermissionError):
    """Raised when a requested path escapes an allowed workspace boundary."""


def resolve_path(path: str | Path, workspace: str | Path | None = None, *, strict: bool = False) -> Path:
    """Resolve *path*, interpreting relative paths against *workspace* when set."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and workspace is not None:
        candidate = Path(workspace).expanduser() / candidate
    return candidate.resolve(strict=strict)


def _resolve_logical_path(path: str | Path, workspace: str | Path | None = None) -> Path:
    """Return an absolute normalized path without following symlinks."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and workspace is not None:
        candidate = Path(workspace).expanduser() / candidate
    return Path(os.path.abspath(candidate))


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.fspath(path))


def is_path_within(path: str | Path, root: str | Path) -> bool:
    """Return True when *path* resolves to *root* or a descendant of *root*."""
    try:
        resolved_path = Path(path).expanduser().resolve(strict=False)
        resolved_root = Path(root).expanduser().resolve(strict=False)
        resolved_path.relative_to(resolved_root)
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def is_path_allowed(path: str | Path, roots: Iterable[str | Path]) -> bool:
    """Return True when *path* is inside any allowed root."""
    return any(is_path_within(path, root) for root in roots)


def _is_path_exactly_allowed(
    logical_path: Path,
    resolved_path: Path,
    files: Iterable[str | Path],
) -> bool:
    """Return True when *path* resolves exactly to one of the allowed files."""
    logical_key = _path_key(logical_path)
    if _path_key(resolved_path) != logical_key:
        return False
    for file in files:
        try:
            allowed_file = _resolve_logical_path(file)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if _path_key(allowed_file) == logical_key:
            return True
    return False


def require_path_within(
    path: str | Path,
    root: str | Path,
    *,
    message: str | None = None,
) -> Path:
    """Resolve *path* and require it to be inside *root*."""
    resolved = Path(path).expanduser().resolve(strict=False)
    if not is_path_within(resolved, root):
        raise WorkspaceBoundaryError(
            message
            or f"Path {path} is outside allowed directory {Path(root).expanduser()}"
            + WORKSPACE_BOUNDARY_NOTE
        )
    return resolved


def _has_drive(path: str) -> bool:
    return bool(os.path.splitdrive(path)[0])


def _workspace_rooted_candidate(path: str, workspace: Path) -> Path | None:
    """Build the project-relative reading of a leading-slash path."""
    if not path or _has_drive(path):
        return None
    if not path.startswith(("/", "\\")):
        return None
    relative = path.lstrip("/\\")
    if not relative:
        return None
    try:
        return Path(workspace).expanduser() / relative
    except (OSError, RuntimeError, ValueError):
        return None


def _literal_exists(path: str) -> bool:
    try:
        return Path(path).expanduser().exists()
    except (OSError, RuntimeError, ValueError):
        return False


def _literal_parent_is_dir(path: str) -> bool:
    """Whether the path names a real place to create something."""
    try:
        return Path(path).expanduser().parent.is_dir()
    except (OSError, RuntimeError, ValueError):
        return False


def _prefer_workspace(path: str, candidate: Path, roots: list[Path]) -> bool:
    """Decide whether to read a leading-slash path as project-relative.

    ``roots`` is empty when no boundary is configured, which is the default and
    means the agent may legitimately reach the whole filesystem. The two modes
    need different answers for a path that exists nowhere: confined to a project
    the literal reading is unreachable anyway, so reading it against the project
    yields a truthful "not found" instead of a policy error; unconfined, an
    absolute path is taken at its word so that writing to ``/tmp/report.txt``
    still lands in ``/tmp``.
    """
    if _literal_exists(path):
        if not roots or is_path_allowed(Path(path).expanduser(), roots):
            return False
        # Out of bounds as written. Prefer a project counterpart when there is
        # one; otherwise leave it to fail as the boundary violation it is,
        # rather than disguising a policy stop as a missing file.
        return candidate.exists()
    if candidate.exists():
        return True
    if roots:
        return True
    # Unconfined and absent everywhere: respect the literal path when its parent
    # directory is real, since that is a deliberate destination such as
    # ``/tmp/report.txt`` rather than a project path written with a slash.
    return not _literal_parent_is_dir(path)


def _posix_separators(path: str, workspace: Path | str | None) -> str:
    """Read backslashes as separators on POSIX, where they are not.

    A model that has seen Windows paths in the conversation, or that is editing
    a project it previously discussed on Windows, writes ``src\\app.py``. Windows
    accepts that natively; POSIX reads it as one file whose name contains a
    backslash, and the agent gets a bewildering "not found" for a file it can
    see in the listing. A name really containing a backslash is legal on POSIX
    though, so an existing literal file always wins, whether it is named
    absolutely or relative to the project.
    """
    if os.sep == "\\" or "\\" not in path:
        return path
    if _literal_exists(path):
        return path
    if workspace is not None and not Path(path).is_absolute():
        with suppress(OSError, RuntimeError, ValueError):
            if (Path(workspace).expanduser() / path).exists():
                return path
    return path.replace("\\", "/")


def project_rooted_path(
    path: str,
    workspace: Path | str | None,
    allowed_roots: Iterable[Path | str | None] = (),
) -> str:
    """Read a leading-slash path as relative to the project root.

    Models routinely write ``/src/app.py`` meaning the ``src`` directory at the
    top of the project rather than of the filesystem. Applying this uniformly
    also keeps the failure honest: a mistyped ``/src/ap.py`` is reported as a
    missing file inside the project instead of a policy violation, which is what
    the agent needs in order to retry sensibly.

    Returns the path unchanged when it does not apply. The rewrite can only ever
    point further inside the project, never out of it.
    """
    path = _posix_separators(path, workspace)
    if workspace is None:
        return path
    candidate = _workspace_rooted_candidate(path, Path(workspace))
    if candidate is None:
        return path
    roots = [Path(root) for root in allowed_roots if root is not None]
    return str(candidate) if _prefer_workspace(path, candidate, roots) else path


def resolve_allowed_path(
    path: str | Path,
    *,
    workspace: str | Path | None = None,
    allowed_root: str | Path | None = None,
    extra_allowed_roots: Iterable[str | Path] | None = None,
    extra_allowed_files: Iterable[str | Path] | None = None,
    strict: bool = False,
) -> Path:
    """Resolve a path and enforce containment in allowed roots when configured."""
    resolved = resolve_path(path, workspace, strict=False)
    files = list(extra_allowed_files or [])
    if allowed_root is None and not files:
        return resolve_path(path, workspace, strict=strict) if strict else resolved

    roots = []
    if allowed_root is not None:
        roots.append(allowed_root)
    roots.extend(extra_allowed_roots or [])
    exact_allowed = bool(files) and _is_path_exactly_allowed(
        _resolve_logical_path(path, workspace),
        resolved,
        files,
    )
    if not is_path_allowed(resolved, roots) and not exact_allowed:
        boundary = Path(allowed_root).expanduser() if allowed_root is not None else "allowed files"
        raise WorkspaceBoundaryError(
            f"Path {path} is outside allowed directory {boundary}"
            + WORKSPACE_BOUNDARY_NOTE
        )
    if strict:
        return resolve_path(path, workspace, strict=True)
    return resolved
