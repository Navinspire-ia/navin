# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-application rules for the desktop tool, and the global kill switch.

Rules match the *active* window (title or application name) with
case-insensitive patterns: a plain word is a substring match, ``*`` / ``?``
make it a glob. Four lists, evaluated in this order:

* ``protected_apps``: password managers and the like. While one of them is in
  front the agent may not even look (no screenshot, no snapshot) and may not
  act. Defaults cover the common vaults.
* ``blocked_apps``: the agent can see them but never clicks or types there.
* ``allowed_apps``: when set, acting is only permitted inside these apps
  (plus ``focus_window`` towards one of them, to get there).
* ``ask_apps``: every action inside them goes through the approval gate,
  whatever the ``ask`` posture.

The kill switch is a file in the runtime directory so ``navin computer stop``
works from any terminal while the gateway is busy; every action checks it.
"""

from __future__ import annotations

import fnmatch
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from navin.computer.base import WindowInfo

DEFAULT_PROTECTED_APPS: tuple[str, ...] = (
    "1Password",
    "Bitwarden",
    "KeePass*",
    "Keychain Access",
    "LastPass",
    "Dashlane",
    "NordPass",
    "Proton Pass",
    "Enpass",
    "Windows Security",
    "Credential Manager",
    "Passwords",
)


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed: bool
    reason: str = ""
    #: Allowed, but must go through approval first.
    ask: bool = False
    #: Which rule / pattern fired, for the audit trail.
    rule: str = ""


def _compile(patterns: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    out: list[str] = []
    for raw in patterns or ():
        text = str(raw or "").strip().lower()
        if text:
            out.append(text)
    return tuple(out)


def _matches(patterns: tuple[str, ...], window: WindowInfo | None) -> str | None:
    if window is None:
        return None
    haystacks = [window.title.lower(), window.app.lower()]
    for pattern in patterns:
        for hay in haystacks:
            if not hay:
                continue
            if any(ch in pattern for ch in "*?["):
                if fnmatch.fnmatchcase(hay, pattern) or fnmatch.fnmatchcase(hay, f"*{pattern}*"):
                    return pattern
            elif pattern in hay:
                return pattern
    return None


class AppPolicy:
    def __init__(
        self,
        *,
        allowed: list[str] | None = None,
        blocked: list[str] | None = None,
        ask: list[str] | None = None,
        protected: list[str] | None = None,
    ) -> None:
        self.allowed = _compile(allowed)
        self.blocked = _compile(blocked)
        self.ask = _compile(ask)
        self.protected = _compile(
            protected if protected is not None else list(DEFAULT_PROTECTED_APPS)
        )

    @classmethod
    def from_config(cls, config: object) -> AppPolicy:
        return cls(
            allowed=list(getattr(config, "allowed_apps", None) or []),
            blocked=list(getattr(config, "blocked_apps", None) or []),
            ask=list(getattr(config, "ask_apps", None) or []),
            protected=list(getattr(config, "protected_apps", None) or []),
        )

    @property
    def needs_window(self) -> bool:
        return bool(self.allowed or self.blocked or self.ask or self.protected)

    def evaluate(
        self,
        action: str,
        *,
        mutating: bool,
        active: WindowInfo | None,
        target: WindowInfo | None = None,
        window_known: bool = True,
    ) -> Verdict:
        """Judge one action given the active window (and the target of focus_window)."""
        hit = _matches(self.protected, active)
        if hit:
            return Verdict(
                False,
                f"a protected application is in front ({active.app or active.title!s}); the agent "
                "may not look at it or act in it. Ask the user to switch away from it first.",
                rule=f"protected:{hit}",
            )
        if not mutating:
            return Verdict(True)
        if action == "focus_window" and target is not None:
            hit = _matches(self.protected, target)
            if hit:
                return Verdict(
                    False, f"{target.title!r} is a protected application", rule=f"protected:{hit}"
                )
            hit = _matches(self.blocked, target)
            if hit:
                return Verdict(
                    False, f"{target.title!r} is a blocked application", rule=f"blocked:{hit}"
                )
            if self.allowed and not _matches(self.allowed, target):
                return Verdict(
                    False,
                    f"{target.title!r} is outside the allowed applications ({', '.join(self.allowed)})",
                    rule="allowed",
                )
            return Verdict(True)
        hit = _matches(self.blocked, active)
        if hit:
            return Verdict(
                False,
                f"acting in {active.app or active.title!s} is blocked by policy",  # type: ignore[union-attr]
                rule=f"blocked:{hit}",
            )
        if self.allowed:
            if active is None:
                return Verdict(
                    False,
                    "an application allow-list is set but the active window is unknown"
                    + ("" if window_known else " on this platform"),
                    rule="allowed",
                )
            if not _matches(self.allowed, active):
                return Verdict(
                    False,
                    f"the active window ({active.app or active.title}) is outside the allowed "
                    f"applications ({', '.join(self.allowed)}); use focus_window to reach one",
                    rule="allowed",
                )
        hit = _matches(self.ask, active)
        if hit:
            return Verdict(
                True,
                f"policy asks before acting in {active.app or active.title}",
                ask=True,
                rule=f"ask:{hit}",
            )  # type: ignore[union-attr]
        return Verdict(True)

    def describe(self) -> list[str]:
        lines: list[str] = []
        if self.protected:
            lines.append("protected: " + ", ".join(self.protected))
        if self.blocked:
            lines.append("blocked: " + ", ".join(self.blocked))
        if self.allowed:
            lines.append("allowed only: " + ", ".join(self.allowed))
        if self.ask:
            lines.append("ask in: " + ", ".join(self.ask))
        return lines


# ------------------------------------------------------------------ kill switch


def stop_file() -> Path:
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("computer") / "STOP"


def engage_stop(reason: str = "") -> Path:
    """Halt every desktop session until :func:`release_stop`."""
    path = stop_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"reason": reason or "stopped by the user", "at": time.time(), "pid": os.getpid()}
        ),
        encoding="utf-8",
    )
    return path


def release_stop() -> bool:
    path = stop_file()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def stop_reason() -> str | None:
    """Why computer use is halted, or None when it may run."""
    path = stop_file()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
        reason = str(data.get("reason") or "stopped by the user")
        at = float(data.get("at") or 0)
    except (json.JSONDecodeError, TypeError, ValueError):
        return "stopped by the user"
    when = time.strftime("%H:%M", time.localtime(at)) if at else ""
    return f"{reason}" + (f" (at {when})" if when else "")
