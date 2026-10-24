# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-application permission levels for the computer tool.

Levels, each including the previous ones: ``see`` < ``click`` < ``type`` < ``launch``.
Rules map an app pattern (substring, or glob with ``*``/``?``, case-insensitive)
to a level. Persisted in ``.navin/computer_permissions.json``. With no rule
matching, the check allows (the existing policy stays the main gate).
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path

LEVELS: tuple[str, ...] = ("see", "click", "type", "launch")

_ACTION_LEVEL: dict[str, str] = {
    "type": "type",
    "key": "type",
    "hold_key": "type",
    "focus_window": "launch",
    "permissions": "launch",
}
_READ_ONLY = {
    "screenshot", "zoom", "snapshot", "windows", "screen_info", "cursor_position",
    "status", "wait", "resume", "close",
}


def required_level(action: str) -> str:
    """Minimum level an action needs; reads need ``see``, other input ``click``."""
    if action in _READ_ONLY:
        return "see"
    return _ACTION_LEVEL.get(action, "click")


@dataclass(frozen=True, slots=True)
class PermVerdict:
    allowed: bool
    reason: str = ""
    rule: str = ""


def _match(pattern: str, name: str) -> bool:
    p, n = pattern.lower(), name.lower()
    if any(c in p for c in "*?"):
        return fnmatch.fnmatchcase(n, p)
    return p in n


class AppPermissions:
    def __init__(self, rules: dict[str, str] | None = None, path: Path | None = None) -> None:
        self.rules: dict[str, str] = {}
        self.path = path
        for app, level in (rules or {}).items():
            self.set(app, level)

    @classmethod
    def default_path(cls, root: Path | None = None) -> Path:
        return (root or Path.cwd()) / ".navin" / "computer_permissions.json"

    @classmethod
    def load(cls, path: Path | None = None) -> AppPermissions:
        path = path or cls.default_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("apps", {}) if isinstance(data, dict) else {}
        except (OSError, ValueError):
            raw = {}
        perms = cls(path=path)
        for app, level in raw.items():
            if isinstance(app, str) and level in LEVELS:
                perms.rules[app] = level
        return perms

    def set(self, app: str, level: str) -> None:
        if level not in LEVELS:
            raise ValueError(f"unknown level {level!r}; expected one of {', '.join(LEVELS)}")
        if not app.strip():
            raise ValueError("app pattern is empty")
        self.rules[app.strip()] = level

    def remove(self, app: str) -> bool:
        return self.rules.pop(app, None) is not None

    def save(self) -> Path:
        path = self.path or self.default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"apps": self.rules}, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def check(self, action: str, app: str | None) -> PermVerdict:
        if not self.rules or not app:
            return PermVerdict(True)
        need = required_level(action)
        for pattern, level in self.rules.items():
            if _match(pattern, app):
                if LEVELS.index(level) >= LEVELS.index(need):
                    return PermVerdict(True, rule=f"perm:{pattern}")
                return PermVerdict(
                    False,
                    f"{app} is limited to '{level}' by permissions; '{action}' needs '{need}'",
                    rule=f"perm:{pattern}",
                )
        return PermVerdict(True)
