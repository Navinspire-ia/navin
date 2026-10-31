# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""TUI-only preferences stored next to the Navin config (``~/.navin/tui.json``).

Kept separate from ``config.json`` so the terminal UI never rewrites the
desktop/gateway configuration.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from navin.utils.atomic_io import atomic_write_text

DEFAULT_THEME = "navin"
_LEGACY_DEFAULT_THEMES = {"tokyo-night", "textual-dark"}
DEFAULT_MODE = "agent"  # same default as the desktop composer


@dataclass
class TuiPrefs:
    theme: str = DEFAULT_THEME
    theme_explicit: bool = False  # True once the user picked a theme themselves
    mode: str = DEFAULT_MODE
    mode_explicit: bool = False  # True once the user switched modes themselves
    sidebar: bool = False
    sidebar_explicit: bool = False  # True once the user hid or showed the panel
    projects_panel: bool = True  # left column with every project and its sessions
    projects_panel_explicit: bool = False  # True once the user hid or showed it
    show_reasoning: bool = True
    show_tools: bool = True
    compact_tools: bool = False
    # Session on screen when the CLI last closed, whatever the project. Kept
    # for files written by earlier builds only: read it through session_for.
    last_session: str = "cli:direct"
    # Last session of each project, by folder. One key for every project made
    # a CLI opened in project B resume the chat of project A under the same
    # key, so its title, pins, display history and agents mixed with A's.
    last_sessions: dict[str, str] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    vim_submit: bool = False  # when True, Enter inserts newline and Ctrl+Enter/Ctrl+S submits
    project_root: str = ""  # folder analysed by Graph / Evolve ("" = launch directory)

    @classmethod
    def path(cls) -> Path:
        try:
            from navin.config.paths import get_config_path

            base = Path(get_config_path()).parent
        except Exception:  # noqa: BLE001
            base = Path.home() / ".navin"
        return base / "tui.json"

    @classmethod
    def load(cls) -> "TuiPrefs":
        path = cls.path()
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return cls()
        prefs = cls()
        for key, value in data.items():
            if hasattr(prefs, key):
                setattr(prefs, key, value)
        if not isinstance(prefs.history, list):
            prefs.history = []
        if not isinstance(prefs.last_sessions, dict):
            prefs.last_sessions = {}
        prefs.last_sessions = {
            str(k): str(v) for k, v in prefs.last_sessions.items() if str(k).strip() and str(v).strip()
        }
        prefs.history = [str(h) for h in prefs.history][-200:]
        if not prefs.theme_explicit and prefs.theme in _LEGACY_DEFAULT_THEMES:
            # Files written by earlier builds carry the old default; follow the new one.
            prefs.theme = DEFAULT_THEME
        if not prefs.mode_explicit:
            # Earlier builds saved "chat" without the user choosing it.
            prefs.mode = DEFAULT_MODE
        if not prefs.sidebar_explicit:
            # Use the whole terminal unless the user explicitly opens the panel.
            prefs.sidebar = False
        return prefs

    def snapshot(self) -> str:
        """Serialized prefs, taken on the thread that mutates them.

        ``asdict`` walks ``history``; running it in a worker while the UI
        appends to that list could save a torn or failing copy.
        """
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    def save(self, snapshot: str | None = None) -> None:
        """Write prefs. Pass a ``snapshot()`` taken earlier to write from a worker."""
        path = self.path()
        try:
            text = self.snapshot() if snapshot is None else snapshot
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, text, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def remember_session(self, workspace: str | Path, key: str) -> bool:
        """Note ``key`` as the last session of ``workspace``; True when it changed."""
        key = str(key or "").strip()
        folder = _folder_id(workspace)
        if not key or not folder:
            return False
        changed = self.last_sessions.get(folder) != key or self.last_session != key
        self.last_sessions[folder] = key
        self.last_session = key
        return changed

    def session_for(self, workspace: str | Path) -> str:
        """The session to open in ``workspace``: never one of another project.

        The project's own last session first; else the global last session
        only when that chat is saved in this project; else the most recent
        CLI chat of the project; else a new key, unique to this project.
        """
        from navin.tui.projects import latest_cli_session, new_session_key, session_saved_in

        folder = _folder_id(workspace)
        remembered = self.last_sessions.get(folder) if folder else None
        if remembered:
            return remembered
        legacy = str(self.last_session or "").strip()
        if legacy and session_saved_in(workspace, legacy):
            return legacy
        return latest_cli_session(workspace) or new_session_key()

    def remember(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.history and self.history[-1] == text:
            return
        self.history.append(text)
        self.history = self.history[-200:]


def _folder_id(workspace: str | Path) -> str:
    """One spelling per project folder, as the map of last sessions keys it."""
    text = str(workspace or "").strip()
    if not text:
        return ""
    try:
        text = str(Path(text).expanduser().resolve())
    except (OSError, RuntimeError):
        pass
    return text.replace("\\", "/").rstrip("/") or text
