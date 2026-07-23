"""Directory browsing API for the Dev project picker.

Lets the WebUI browse host directories (names only, no file contents) so the
user can pick a project root like VS Code's "Open Folder" dialog. Exposes
environment-aware quick roots: home, filesystem root, Windows drives when
running inside WSL, and /Volumes on macOS.
"""

from __future__ import annotations

import platform
import re
from pathlib import Path
from typing import Any

MAX_DIR_ENTRIES = 400


class FsBrowseError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _is_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except OSError:
        return False
    return "microsoft" in release.lower() or "wsl" in release.lower()


def host_environment() -> str:
    """Coarse environment label: wsl | linux | macos | windows."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    if _is_wsl():
        return "wsl"
    return "linux"


def _windows_drive_mounts() -> list[Path]:
    """Windows drives mounted inside WSL (/mnt/c, /mnt/d, …)."""
    mnt = Path("/mnt")
    if not mnt.is_dir():
        return []
    drives: list[Path] = []
    for entry in sorted(mnt.iterdir()):
        if entry.is_dir() and re.fullmatch(r"[a-z]", entry.name):
            drives.append(entry)
    return drives


def fs_roots_payload(*, default_project_path: str | None = None) -> dict[str, Any]:
    """Quick-access roots for the folder picker, tagged by environment."""
    env = host_environment()
    roots: list[dict[str, str]] = []

    def add(kind: str, label: str, path: Path) -> None:
        if path.is_dir():
            roots.append({"kind": kind, "label": label, "path": str(path)})

    if default_project_path:
        add("workspace", "Workspace", Path(default_project_path))
    add("home", "Home", Path.home())
    if env == "wsl":
        for drive in _windows_drive_mounts():
            add("windows", f"Windows ({drive.name.upper()}:)", drive)
    if env == "macos":
        add("volumes", "Volumes", Path("/Volumes"))
    add("system", "/", Path("/"))
    return {"environment": env, "roots": roots}


def fs_list_payload(raw_path: str, *, show_hidden: bool = False) -> dict[str, Any]:
    """List sub-directories of an absolute path (names only)."""
    cleaned = (raw_path or "").strip()
    if not cleaned:
        raise FsBrowseError("missing path")
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        raise FsBrowseError("path must be absolute")
    try:
        path = path.resolve()
    except OSError as exc:
        raise FsBrowseError(f"cannot resolve path: {exc}") from exc
    if not path.is_dir():
        raise FsBrowseError("not a directory", status=404)

    dirs: list[dict[str, str]] = []
    truncated = False
    try:
        entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except PermissionError as exc:
        raise FsBrowseError("permission denied", status=403) from exc
    except OSError as exc:
        raise FsBrowseError(f"cannot list directory: {exc}") from exc
    for entry in entries:
        if not show_hidden and entry.name.startswith("."):
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        if len(dirs) >= MAX_DIR_ENTRIES:
            truncated = True
            break
        dirs.append({"name": entry.name, "path": str(entry)})

    parent = str(path.parent) if path.parent != path else None
    return {
        "path": str(path),
        "parent": parent,
        "directories": dirs,
        "truncated": truncated,
    }
