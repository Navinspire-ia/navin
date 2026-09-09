"""What the desktop tool asks about, and how it notices the user took over.

Three guards, all cheap and all explainable to the person being asked:

* :func:`classify_risk` names the key combos and typed commands that close,
  lock, wipe or pay. They go through the approval gate under the default
  ``ask: destructive`` posture and are refused outright when nobody can answer.
* :class:`TakeoverMonitor` compares where the tool left the cursor with where
  it is now. A cursor that moved on its own means a human is at the desk, and
  the run pauses instead of fighting them for the mouse. Parking the cursor in
  the top-left corner is the classic panic gesture and stops the run too.
* :class:`AuditLog` keeps a JSONL trail (and the screenshot each step saw) so
  a session can be replayed after the fact.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.computer.keys import KeyCombo

MUTATING_ACTIONS: frozenset[str] = frozenset(
    {
        "left_click",
        "right_click",
        "middle_click",
        "double_click",
        "triple_click",
        "click",
        "left_click_drag",
        "drag",
        "left_mouse_down",
        "left_mouse_up",
        "mouse_down",
        "mouse_up",
        "scroll",
        "type",
        "key",
        "hold_key",
        "focus_window",
        "mouse_move",
        "move",
        "permissions",
    }
)

READ_ONLY_ACTIONS: frozenset[str] = frozenset(
    {
        "screenshot",
        "cursor_position",
        "zoom",
        "windows",
        "snapshot",
        "screen_info",
        "status",
        "wait",
        "resume",
        "close",
    }
)


@dataclass(frozen=True, slots=True)
class Risk:
    rule: str
    description: str


_RISKY_COMBOS: dict[tuple[str, ...], Risk] = {
    ("alt", "f4"): Risk("close-window", "Alt+F4 closes the active window, unsaved work included"),
    ("ctrl", "alt", "delete"): Risk("secure-screen", "Ctrl+Alt+Del opens the security screen"),
    ("meta", "l"): Risk("lock-session", "locks the session"),
    ("ctrl", "meta", "q"): Risk("lock-session", "locks the session (macOS)"),
    ("meta", "q"): Risk("quit-app", "quits the front application"),
    ("ctrl", "q"): Risk("quit-app", "quits the application"),
    ("ctrl", "shift", "q"): Risk("logout", "logs the desktop session out"),
    ("shift", "delete"): Risk("permanent-delete", "deletes permanently, bypassing the trash"),
    ("alt", "meta", "eject"): Risk("power", "sleep / shutdown shortcut"),
    ("ctrl", "alt", "backspace"): Risk("kill-x", "kills the X server"),
    ("meta", "ctrl", "power"): Risk("power", "power shortcut"),
}

_RISKY_TEXT: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (rule, re.compile(pattern, re.IGNORECASE))
    for rule, pattern in (
        ("rm-rf", r"\brm\s+(-[a-z]*\s+)*-[a-z]*[rf][a-z]*\b"),
        ("sudo", r"^\s*(sudo|doas|su)\b"),
        ("mkfs", r"\bmkfs(\.[a-z0-9]+)?\b"),
        ("dd", r"\bdd\s+if="),
        ("shutdown", r"\b(shutdown|reboot|poweroff|halt)\b"),
        ("format", r"\bformat\s+[a-z]:"),
        ("del-force", r"\b(del|erase)\s+/[fqs]"),
        ("rmdir-s", r"\b(rd|rmdir)\s+/s\b"),
        ("remove-item", r"remove-item\b.*-recurse"),
        ("diskpart", r"\bdiskpart\b"),
        ("fork-bomb", r":\(\)\s*\{"),
        (
            "git-destructive",
            r"\bgit\s+(push\b.*(--force|-f)\b|reset\s+--hard|clean\s+-[a-z]*f|branch\s+-D)",
        ),
        ("sql-drop", r"\b(drop\s+(table|database|schema)|truncate\s+table)\b"),
        ("chmod-777", r"\bchmod\s+(-R\s+)?777\b"),
        ("pipe-to-shell", r"\b(curl|wget)\b.*\|\s*(ba|z|da)?sh\b"),
        ("registry", r"\breg\s+delete\b"),
        ("crypto-wipe", r"\b(cipher\s+/w|shred\s+-)"),
        # 13-19 digits with optional spaces / dashes: a card number typed blind.
        ("card-number", r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
    )
)


def classify_risk(action: str, kwargs: dict[str, Any]) -> Risk | None:
    """The destructive rule this call matches, or None when it is routine."""
    if action in {"key", "hold_key"}:
        text = str(kwargs.get("text") or kwargs.get("key") or "")
        try:
            from navin.computer.keys import parse_combo

            combo = parse_combo(text)
        except ValueError:
            return None
        return risk_for_combo(combo)
    if action == "type":
        text = str(kwargs.get("text") or "")
        return risk_for_text(text)
    return None


def risk_for_combo(combo: KeyCombo) -> Risk | None:
    key = tuple(sorted(combo.modifiers)) + (combo.key,)
    for pattern, risk in _RISKY_COMBOS.items():
        if tuple(sorted(pattern[:-1])) + (pattern[-1],) == key:
            return risk
    return None


def risk_for_text(text: str) -> Risk | None:
    if not text:
        return None
    for rule, pattern in _RISKY_TEXT:
        if pattern.search(text):
            return Risk(rule, f"the typed text looks like a destructive command ({rule})")
    return None


@dataclass
class TakeoverMonitor:
    """Detects a human at the keyboard between two tool actions."""

    threshold_px: int = 48
    failsafe_corner: bool = True
    corner_px: int = 4
    #: Where the tool last left the cursor. None until the first pointer action.
    expected: tuple[int, int] | None = None
    paused: bool = False
    reason: str = ""

    def observe(
        self, cursor: tuple[int, int] | None, screen_left: int = 0, screen_top: int = 0
    ) -> str | None:
        """Called before each action. Returns why the run must pause, or None."""
        if cursor is None:
            return None
        if (
            self.failsafe_corner
            and cursor[0] - screen_left <= self.corner_px
            and cursor[1] - screen_top <= self.corner_px
        ):
            self.paused = True
            self.reason = (
                "the cursor is parked in the top-left corner (fail-safe gesture): computer use "
                "is paused until the user says to continue"
            )
            return self.reason
        if self.threshold_px > 0 and self.expected is not None:
            dx = cursor[0] - self.expected[0]
            dy = cursor[1] - self.expected[1]
            if dx * dx + dy * dy > self.threshold_px * self.threshold_px:
                self.paused = True
                self.reason = (
                    f"the mouse moved {int((dx * dx + dy * dy) ** 0.5)} px since the last action: "
                    "someone is using this computer. Computer use is paused; ask the user before continuing"
                )
                return self.reason
        return None

    def left_cursor_at(self, cursor: tuple[int, int] | None) -> None:
        self.expected = cursor

    def resume(self) -> None:
        self.paused = False
        self.reason = ""
        self.expected = None


@dataclass
class AuditLog:
    """JSONL trail of one session plus the screenshot each step returned."""

    directory: Path | None
    keep_screenshots: bool = True
    _steps: int = 0
    _started: float = field(default_factory=time.time)

    def write_meta(self, meta: dict[str, Any]) -> None:
        """``session.json`` next to the trail: who, when, under which policy."""
        if self.directory is None:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / "session.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        except OSError:
            return

    def record(self, entry: dict[str, Any], screenshot_png: bytes | None = None) -> str | None:
        if self.directory is None:
            return None
        self._steps += 1
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            shot_path: Path | None = None
            if screenshot_png and self.keep_screenshots:
                shot_path = self.directory / f"step-{self._steps:04d}.png"
                shot_path.write_bytes(screenshot_png)
            row = {"step": self._steps, "t": round(time.time() - self._started, 3), **entry}
            if shot_path is not None:
                row["screenshot"] = str(shot_path)
            with (self.directory / "actions.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            return str(shot_path) if shot_path else None
        except OSError:
            return None
