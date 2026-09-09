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

DEFAULT_THEME = "navin"
_LEGACY_DEFAULT_THEMES = {"tokyo-night", "textual-dark"}
DEFAULT_MODE = "agent"  # same default as the desktop composer


@dataclass
class TuiPrefs:
    theme: str = DEFAULT_THEME
    theme_explicit: bool = False  # True once the user picked a theme themselves
    mode: str = DEFAULT_MODE
    mode_explicit: bool = False  # True once the user switched modes themselves
    sidebar: bool = True
    sidebar_explicit: bool = False  # True once the user hid or showed the panel
    show_reasoning: bool = True
    show_tools: bool = True
    compact_tools: bool = False
    last_session: str = "cli:direct"
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
        prefs.history = [str(h) for h in prefs.history][-200:]
        if not prefs.theme_explicit and prefs.theme in _LEGACY_DEFAULT_THEMES:
            # Files written by earlier builds carry the old default; follow the new one.
            prefs.theme = DEFAULT_THEME
        if not prefs.mode_explicit:
            # Earlier builds saved "chat" without the user choosing it.
            prefs.mode = DEFAULT_MODE
        if not prefs.sidebar_explicit:
            # Earlier builds saved a closed panel while iterating the dock.
            prefs.sidebar = True
        return prefs

    def save(self) -> None:
        path = self.path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass

    def remember(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.history and self.history[-1] == text:
            return
        self.history.append(text)
        self.history = self.history[-200:]
