# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""TUI-only preferences stored next to the Navin config (``~/.navin/tui.json``).

Kept separate from ``config.json`` so the terminal UI never rewrites the
desktop/gateway configuration.

Several CLIs run at once and each reads this file when it starts. ``save``
therefore writes only what this CLI changed, on top of what the file holds
now: a CLI opened hours ago no longer puts back the theme, the last sessions
or anything else another CLI saved since. The prompts of the Up and Down
arrows are not kept here but in ``prompt-history.jsonl``
(see ``navin.tui.prompt_history``).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from loguru import logger

from navin.tui.prompt_history import LOG_NAME as HISTORY_NAME
from navin.tui.prompt_history import RECALL_LIMIT, PromptHistory
from navin.utils.atomic_io import InterProcessLock, atomic_write_text

DEFAULT_THEME = "navin"
_LEGACY_DEFAULT_THEMES = {"tokyo-night", "textual-dark"}
DEFAULT_MODE = "agent"  # same default as the desktop composer
_LOCK_TIMEOUT_S = 3.0
_MISSING = object()


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
    # Prompts sent from the project on screen, for the Up and Down arrows.
    # Not saved in tui.json: they live in prompt-history.jsonl (load_history).
    history: list[str] = field(default_factory=list)
    vim_submit: bool = False  # when True, Enter inserts newline and Ctrl+Enter/Ctrl+S submits
    project_root: str = ""  # folder analysed by Graph / Evolve ("" = launch directory)

    def __post_init__(self) -> None:
        # Plain attributes, not fields: they are never saved.
        self._baseline: dict[str, Any] = self._state()
        self._history_folder = ""

    @classmethod
    def path(cls) -> Path:
        try:
            from navin.config.paths import get_config_path

            base = Path(get_config_path()).parent
        except Exception:  # noqa: BLE001
            base = Path.home() / ".navin"
        return base / "tui.json"

    @classmethod
    def history_path(cls) -> Path:
        return cls.path().with_name(HISTORY_NAME)

    @classmethod
    def _lock_path(cls) -> Path:
        path = cls.path()
        return path.with_name(path.name + ".lock")

    @classmethod
    def load(cls) -> "TuiPrefs":
        data = _read_json(cls.path())
        prefs = cls()
        names = {item.name for item in fields(cls)}
        for key, value in data.items():
            # "history" is read by load_history, which moves it out of this file.
            if key in names and key != "history":
                setattr(prefs, key, value)
        if not isinstance(prefs.last_sessions, dict):
            prefs.last_sessions = {}
        prefs.last_sessions = {
            str(k): str(v) for k, v in prefs.last_sessions.items() if str(k).strip() and str(v).strip()
        }
        if not prefs.theme_explicit and prefs.theme in _LEGACY_DEFAULT_THEMES:
            # Files written by earlier builds carry the old default; follow the new one.
            prefs.theme = DEFAULT_THEME
        if not prefs.mode_explicit:
            # Earlier builds saved "chat" without the user choosing it.
            prefs.mode = DEFAULT_MODE
        if not prefs.sidebar_explicit:
            # Use the whole terminal unless the user explicitly opens the panel.
            prefs.sidebar = False
        prefs._baseline = prefs._state()
        return prefs

    def _state(self) -> dict[str, Any]:
        """The saved preferences as plain data. The prompts are not among them."""
        return {item.name: copy.deepcopy(getattr(self, item.name)) for item in fields(self) if item.name != "history"}

    def snapshot(self) -> str:
        """Serialized prefs, taken on the thread that mutates them.

        Taken on the UI thread so that a worker never walks a dict the UI is
        changing, and so that a save in flight cannot see a later change.
        """
        return json.dumps(self._state(), ensure_ascii=False, indent=2)

    def save(self, snapshot: str | None = None) -> bool:
        """Write what this CLI changed since it loaded or last saved the preferences.

        Pass a ``snapshot()`` taken earlier to write from a worker. The other
        keys, and the last sessions of the other projects, stay as the file
        holds them now. False, once logged, when the file cannot be written.
        """
        path = self.path()
        try:
            current = json.loads(self.snapshot() if snapshot is None else snapshot)
        except ValueError as exc:
            logger.warning("Could not save the CLI preferences: the snapshot is unreadable ({})", exc)
            return False
        baseline = self._baseline
        changed = {key: value for key, value in current.items() if baseline.get(key, _MISSING) != value}
        if not changed:
            return True
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with InterProcessLock(self._lock_path(), timeout=_LOCK_TIMEOUT_S):
                merged = _merged(_read_json(path), changed, baseline)
                atomic_write_text(path, json.dumps(merged, ensure_ascii=False, indent=2))
        except OSError as exc:
            logger.warning("Could not save the CLI preferences in {}: {}", path, exc)
            return False
        self._baseline = current
        return True

    def load_history(self, workspace: str | Path) -> None:
        """Recall the prompts sent from ``workspace`` for the Up and Down arrows.

        The first CLI to run after the list left tui.json moves the old
        prompts into the log, once, for every project to recall.
        """
        folder = _folder_id(workspace)
        store = PromptHistory(self.history_path())
        self._move_old_history(store)
        self._history_folder = folder
        self.history = store.recent(folder) if folder else []

    def _move_old_history(self, store: PromptHistory) -> None:
        path = self.path()
        try:
            with InterProcessLock(self._lock_path(), timeout=_LOCK_TIMEOUT_S):
                data = _read_json(path)
                if "history" not in data:
                    return
                old = data.pop("history")
                prompts = [str(item) for item in old] if isinstance(old, list) else []
                if prompts and not store.import_legacy(prompts):
                    # Left in tui.json: the next start moves it.
                    logger.warning("The old prompt history stays in {} until it can be moved", path)
                    return
                atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
        except OSError as exc:
            logger.warning("Could not move the old prompt history out of {}: {}", path, exc)

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

    def remember(self, text: str, session: str = "") -> bool:
        """Note a prompt just sent, for the arrows and for the next time the project opens.

        False when it is empty or repeats the previous one.
        """
        text = text.strip()
        if not text:
            return False
        if self.history and self.history[-1] == text:
            return False
        self.history.append(text)
        self.history = self.history[-RECALL_LIMIT:]
        if self._history_folder:
            PromptHistory(self.history_path()).append(self._history_folder, session, text)
        return True


def _read_json(path: Path) -> dict[str, Any]:
    """The preferences file as a dict; empty, after a warning, when it is unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Could not read the CLI preferences {}: {}", path, exc)
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        logger.warning("The CLI preferences {} are not valid JSON ({}); the defaults apply", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("The CLI preferences {} are not an object; the defaults apply", path)
        return {}
    return data


def _merged(disk: dict[str, Any], changed: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """``disk`` with the keys this CLI changed; the last sessions are merged folder by folder."""
    merged = dict(disk)
    for key, value in changed.items():
        if key == "last_sessions" and isinstance(value, dict):
            known = baseline.get(key) if isinstance(baseline.get(key), dict) else {}
            on_disk = merged.get(key) if isinstance(merged.get(key), dict) else {}
            merged[key] = {**on_disk, **{folder: session for folder, session in value.items() if known.get(folder) != session}}
        else:
            merged[key] = value
    return merged


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
