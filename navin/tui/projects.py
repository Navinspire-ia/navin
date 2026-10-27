# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Projects and sessions for the CLI sidebar, from the same data as the desktop.

The desktop keeps its recent projects, pinned chats and chat names in
``sidebar-state.json``; each project keeps its conversations under
``<project>/.navin/sessions``. This module reads and writes exactly those, so a
project added here shows in the desktop and the other way round. It has no
Textual import: the widget lives in :mod:`navin.tui.projects_panel`.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.tui.session_labels import session_display_title

#: Sessions shown under a project at first, and added by each "more".
PAGE_SIZE = 3

#: Folder colours, bright enough on dark and light themes. One per project,
#: stable from its path, so a folder keeps its colour from one launch to the next.
FOLDER_COLORS: tuple[str, ...] = (
    "#E5C07B", "#61AFEF", "#98C379", "#C678DD",
    "#E06C75", "#56B6C2", "#D19A66", "#7AA2F7",
)

_CHAT_CHANNELS = frozenset({"cli", "websocket"})


@dataclass(frozen=True)
class SessionEntry:
    key: str
    title: str
    updated_at: str
    pinned: bool = False


@dataclass
class ProjectEntry:
    path: str
    name: str
    color: str
    sessions: list[SessionEntry] = field(default_factory=list)


def folder_color(path: str) -> str:
    """The colour of a project's folder icon."""
    digest = zlib.crc32(_norm(path).encode("utf-8"))
    return FOLDER_COLORS[digest % len(FOLDER_COLORS)]


def _norm(path: str) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").lower()


def same_path(a: str | Path, b: str | Path) -> bool:
    return bool(a) and bool(b) and _norm(str(a)) == _norm(str(b))


def project_name(path: str, overrides: dict[str, str] | None = None) -> str:
    named = (overrides or {}).get(path)
    if named:
        return named
    return Path(path).name or str(path)


def next_visible(shown: int, total: int) -> int:
    """How many sessions to show after one "more": 3 more, never past the total."""
    return min(total, max(shown, PAGE_SIZE) + PAGE_SIZE)


def is_chat_key(key: str) -> bool:
    channel, _, rest = (key or "").partition(":")
    return channel in _CHAT_CHANNELS and bool(rest)


# -- desktop state -----------------------------------------------------------


def read_state() -> dict[str, Any]:
    from navin.webui.sidebar_state import read_webui_sidebar_state

    return read_webui_sidebar_state()


def _write_state(state: dict[str, Any]) -> None:
    from navin.webui.sidebar_state import write_webui_sidebar_state

    write_webui_sidebar_state(state)


def known_project_paths(current: str, state: dict[str, Any] | None = None) -> list[str]:
    """Recent projects in the desktop's order, with the one open here included."""
    state = state if state is not None else read_state()
    paths: list[str] = []
    for item in state.get("recent_projects") or []:
        path = str((item or {}).get("path") or "").strip()
        if path and not any(same_path(path, seen) for seen in paths):
            paths.append(path)
    if current and not any(same_path(current, seen) for seen in paths):
        paths.insert(0, str(current))
    return paths


def remember_project(path: str) -> None:
    """Put a project first in the recent list, as the desktop does when you open one."""
    state = read_state()
    recent = [
        item for item in state.get("recent_projects") or []
        if not same_path(str((item or {}).get("path") or ""), path)
    ]
    state["recent_projects"] = [{"path": str(path), "name": ""}, *recent]
    _write_state(state)


def register_project(path: str) -> bool:
    """Add a folder to the recent list when it is not there yet; True if it was added.

    Called for the folder the CLI runs in, so the sidebar lists every folder the
    CLI was ever used in. It never reorders the folders already known.
    """
    state = read_state()
    if any(same_path(str((i or {}).get("path") or ""), path) for i in state.get("recent_projects") or []):
        return False
    state["recent_projects"] = [*(state.get("recent_projects") or []), {"path": str(path), "name": ""}]
    _write_state(state)
    return True


def resolve_project_dir(raw: str, *, create: bool = False) -> Path:
    """A typed or pasted path as a folder, or ``ValueError`` saying why.

    With ``create`` a missing folder is made (its parents too), for "new project".
    """
    text = (raw or "").strip().strip("\"'")
    if not text:
        raise ValueError("Type a folder path.")
    path = Path(text).expanduser()
    if not path.exists() and create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"Cannot create {path}: {exc}") from exc
    if not path.is_dir():
        raise ValueError(f"Not a folder: {path}")
    return path.resolve()


def toggle_pin(key: str) -> bool:
    """Pin or unpin a chat; returns whether it is pinned afterwards."""
    state = read_state()
    pinned = [k for k in state.get("pinned_keys") or [] if k != key]
    now_pinned = key not in (state.get("pinned_keys") or [])
    if now_pinned:
        pinned.insert(0, key)
    state["pinned_keys"] = pinned
    _write_state(state)
    return now_pinned


# -- sessions ----------------------------------------------------------------


def list_project_sessions(path: str, state: dict[str, Any] | None = None) -> list[SessionEntry]:
    """Conversations of one project, pinned first, then the most recent.

    Reads only ``<project>/.navin/sessions`` when it exists: listing a folder
    must never create files in it.
    """
    if not (Path(path) / ".navin" / "sessions").is_dir():
        return []
    from navin.session.manager import SessionManager

    state = state if state is not None else read_state()
    pinned = set(state.get("pinned_keys") or [])
    titles = state.get("title_overrides") or {}
    archived = set(state.get("archived_keys") or [])
    try:
        rows = SessionManager(Path(path)).list_sessions()
    except (OSError, ValueError):
        return []
    entries: list[SessionEntry] = []
    for row in rows:
        key = str(row.get("key") or "")
        if not is_chat_key(key) or key in archived:
            continue
        if not (str(row.get("title") or "").strip() or str(row.get("preview") or "").strip()):
            continue  # opened and left empty: nothing to come back to
        entries.append(
            SessionEntry(
                key=key,
                title=titles.get(key) or session_display_title(row),
                updated_at=str(row.get("updated_at") or ""),
                pinned=key in pinned,
            )
        )
    entries.sort(key=lambda e: e.updated_at, reverse=True)
    entries.sort(key=lambda e: not e.pinned)
    return entries


def load_projects(current: str) -> list[ProjectEntry]:
    state = read_state()
    names = state.get("project_name_overrides") or {}
    return [
        ProjectEntry(
            path=path,
            name=project_name(path, names),
            color=folder_color(path),
            sessions=list_project_sessions(path, state),
        )
        for path in known_project_paths(current, state)
        if Path(path).is_dir()  # a folder that was moved or deleted is not listed
    ]


def rename_session(path: str, key: str, title: str, manager: Any = None) -> str:
    """Name a chat of any project. ``manager`` is the live one for the project open here."""
    if manager is None:
        from navin.session.manager import SessionManager

        manager = SessionManager(Path(path))
    return manager.set_title(key, title)


def delete_session(path: str, key: str, manager: Any = None) -> bool:
    """Delete a chat file (and its cache entry) of any project."""
    if manager is None:
        from navin.session.manager import SessionManager

        manager = SessionManager(Path(path))
    unpinned = [k for k in read_state().get("pinned_keys") or [] if k != key]
    removed = manager.delete_session(key)
    state = read_state()
    if key in (state.get("pinned_keys") or []):
        state["pinned_keys"] = unpinned
        _write_state(state)
    return removed


def new_session_key() -> str:
    import time

    return f"cli:{int(time.time() * 1000)}"
