"""CLI side of .navin-session files: export the current chat, open a shared one.

The file is the same one the desktop menu "Export session" and the guest page
produce, so a session moves freely between the CLI, the desktop and a guest.
"""

from __future__ import annotations

import re
import shlex
import time
from pathlib import Path
from typing import Any

from navin.session.bundle import (
    BUNDLE_SUFFIX,
    ExportResult,
    ImportResult,
    export_session_bundle,
    import_session_bundle,
    read_manifest,
)

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def default_export_dir(workspace: Path) -> Path:
    """Downloads when it exists (like the desktop), else ``<workspace>/exports``.

    Never the project itself: a bundle there would end up in the next export
    and in ``git status``.
    """
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path(workspace) / "exports"


def default_export_path(folder: Path, title: str = "", *, now: float | None = None) -> Path:
    """``<folder>/<title>-<stamp>.navin-session``, a name safe on every OS."""
    slug = _SLUG.sub("-", title or "").strip("-.")[:60] or "session"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    return Path(folder) / f"{slug}-{stamp}{BUNDLE_SUFFIX}"


def export_cli_session(
    sessions_workspace: Path,
    session_key: str,
    *,
    project_root: Path | None = None,
    title: str = "",
    output: Path | None = None,
) -> ExportResult:
    """Write the CLI chat ``session_key`` (and its project) as a .navin-session."""
    workspace = Path(sessions_workspace).expanduser().resolve()
    root = Path(project_root).expanduser().resolve() if project_root else None
    target = Path(output).expanduser() if output else default_export_path(default_export_dir(workspace), title)
    return export_session_bundle(
        workspace,
        session_key,
        target,
        project_root=root if root and root != workspace else None,
    )


def describe_bundle(path: Path) -> dict[str, Any]:
    """The manifest of a .navin-session, or a ValueError for any other file."""
    path = Path(path).expanduser()
    if path.suffix.lower() != BUNDLE_SUFFIX or not path.is_file():
        raise ValueError(f"Choose a {BUNDLE_SUFFIX} file: {path}")
    return read_manifest(path)


def free_project_dir(base: Path, manifest: dict[str, Any]) -> Path:
    """``<base>/<project name>``, or ``<name>-2``, ``-3``... when it is taken."""
    name = _SLUG.sub("-", str(manifest.get("project_name") or "")).strip("-.") or "navin-session"
    candidate = Path(base) / name
    index = 2
    while candidate.exists() and any(candidate.iterdir()):
        candidate = Path(base) / f"{name}-{index}"
        index += 1
    return candidate


def open_cli_bundle(bundle: Path, into: Path) -> ImportResult:
    """Rebuild the project and its chat in ``into``, like ``navin sessions open``."""
    return import_session_bundle(Path(bundle).expanduser(), Path(into).expanduser())


def continue_command(result: ImportResult) -> str:
    """The command that resumes the rebuilt chat in its own project."""
    return f"navin-cli {shlex.quote(str(result.project_dir))} --session {shlex.quote(result.session_key)}"
