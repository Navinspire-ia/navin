"""Workspace-scoped directory listing for the WebUI Dev mode explorer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.security.workspace_access import WorkspaceScope
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path

MAX_TREE_ENTRIES = 800

# Noisy directories collapsed by default in the explorer; still listed so the
# user can open them explicitly, but flagged for the UI.
_HEAVY_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".ruff_cache",
    ".pytest_cache", "dist", "build", ".next", ".cache", ".checkpoints",
}


class WebUIFileTreeError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def file_tree_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    max_entries: int = MAX_TREE_ENTRIES,
) -> dict[str, Any]:
    """List one directory inside the session workspace scope (lazy tree)."""

    root = scope.project_path
    target = (raw_path or "").strip() or str(root)

    try:
        resolved = resolve_allowed_path(
            target,
            workspace=root,
            allowed_root=root if scope.restrict_to_workspace else None,
            strict=True,
        )
    except FileNotFoundError as e:
        raise WebUIFileTreeError(404, "directory not found") from e
    except WorkspaceBoundaryError as e:
        raise WebUIFileTreeError(403, "path is outside the current workspace") from e
    except OSError as e:
        raise WebUIFileTreeError(400, "invalid path") from e

    if not resolved.is_dir():
        raise WebUIFileTreeError(400, "path is not a directory")

    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        children = sorted(
            resolved.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except OSError as e:
        raise WebUIFileTreeError(500, "failed to list directory") from e

    for child in children:
        if len(entries) >= max_entries:
            truncated = True
            break
        try:
            is_dir = child.is_dir()
            size = 0 if is_dir else child.stat().st_size
        except OSError:
            continue
        entry: dict[str, Any] = {
            "name": child.name,
            "path": str(child),
            "type": "dir" if is_dir else "file",
            "size": size,
        }
        if is_dir and child.name in _HEAVY_DIRS:
            entry["heavy"] = True
        entries.append(entry)

    return {
        "path": str(resolved),
        "display_path": _display_path(resolved, root),
        "project_path": str(root),
        "entries": entries,
        "truncated": truncated,
    }


def _display_path(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root).as_posix()
        return rel if rel != "." else ""
    except ValueError:
        return path.as_posix()
