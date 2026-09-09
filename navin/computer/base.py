# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Backend contract shared by every windowing system.

Coordinates are **screen pixels**, the same unit as the PNG a backend returns
from :meth:`ComputerBackend.screenshot`. A backend that talks to an input API
in another unit (macOS points, Wayland logical pixels) converts internally, so
the tool layer can map "pixel (412, 310) on the screenshot I showed the model"
onto "where to click" with one ratio and no platform knowledge.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from navin.computer.keys import KeyCombo

MouseButton = str  # "left" | "right" | "middle"
MOUSE_BUTTONS: tuple[str, ...] = ("left", "right", "middle")


class ComputerError(RuntimeError):
    """An action could not be carried out. The message is shown to the model."""


class NotSupportedError(ComputerError):
    """The backend cannot do this at all (not a transient failure)."""


class PermissionMissingError(ComputerError):
    """The OS refused: Screen Recording / Accessibility / uinput access."""

    def __init__(self, message: str, *, permission: str | None = None) -> None:
        super().__init__(message)
        self.permission = permission


@dataclass(frozen=True, slots=True)
class DisplayInfo:
    index: int
    left: int
    top: int
    width: int
    height: int
    primary: bool = False
    name: str = ""
    scale: float = 1.0


@dataclass(frozen=True, slots=True)
class ScreenInfo:
    """The virtual screen: the bounding box of every display, in pixels."""

    width: int
    height: int
    left: int = 0
    top: int = 0
    displays: tuple[DisplayInfo, ...] = ()

    def clamp(self, x: float, y: float) -> tuple[int, int]:
        cx = min(max(int(round(x)), self.left), self.left + self.width - 1)
        cy = min(max(int(round(y)), self.top), self.top + self.height - 1)
        return cx, cy


@dataclass(frozen=True, slots=True)
class Screenshot:
    """PNG bytes plus the screen rectangle they cover, in screen pixels."""

    png: bytes
    width: int
    height: int
    left: int = 0
    top: int = 0
    taken_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class WindowInfo:
    id: str
    title: str
    left: int
    top: int
    width: int
    height: int
    app: str = ""
    active: bool = False
    minimized: bool = False

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "x": self.left,
            "y": self.top,
            "w": self.width,
            "h": self.height,
        }
        if self.app:
            out["app"] = self.app
        if self.active:
            out["active"] = True
        if self.minimized:
            out["minimized"] = True
        return out


@dataclass(frozen=True, slots=True)
class UIElement:
    """One accessibility node worth clicking, with its screen rectangle."""

    ref: int
    role: str
    name: str
    left: int
    top: int
    width: int
    height: int
    value: str = ""
    enabled: bool = True
    focused: bool = False

    @property
    def center(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2


@dataclass(frozen=True, slots=True)
class Check:
    """One line of ``navin computer doctor``."""

    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


class ComputerBackend(ABC):
    """What the tool needs from a windowing system. Blocking calls; the tool
    runs them in a worker thread under the session lock."""

    #: Short identifier shown in status / doctor: "windows", "x11", ...
    name: str = "abstract"
    #: Human description of what this backend drives ("Windows desktop").
    label: str = ""

    # -- screen ------------------------------------------------------------

    @abstractmethod
    def screen(self) -> ScreenInfo: ...

    @abstractmethod
    def screenshot(self) -> Screenshot: ...

    @abstractmethod
    def cursor_position(self) -> tuple[int, int]: ...

    # -- pointer -----------------------------------------------------------

    @abstractmethod
    def move(self, x: int, y: int) -> None: ...

    @abstractmethod
    def button(self, x: int, y: int, button: MouseButton, *, down: bool) -> None:
        """Press (down=True) or release one button at ``(x, y)``."""

    def click(self, x: int, y: int, button: MouseButton = "left", count: int = 1) -> None:
        self.move(x, y)
        for i in range(max(1, count)):
            self.button(x, y, button, down=True)
            self.button(x, y, button, down=False)
            if i + 1 < count:
                time.sleep(0.06)

    def drag(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        button: MouseButton = "left",
        *,
        steps: int = 12,
    ) -> None:
        self.move(x0, y0)
        self.button(x0, y0, button, down=True)
        x, y = x0, y0
        try:
            time.sleep(0.05)
            steps = max(2, steps)
            for i in range(1, steps + 1):
                t = i / steps
                x = int(round(x0 + (x1 - x0) * t))
                y = int(round(y0 + (y1 - y0) * t))
                self.move(x, y)
                time.sleep(0.012)
            time.sleep(0.05)
        finally:
            self.button(x, y, button, down=False)

    @abstractmethod
    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        """Scroll ``dy`` notches (positive = down) and ``dx`` (positive = right)."""

    # -- keyboard ----------------------------------------------------------

    @abstractmethod
    def type_text(self, text: str, *, delay_ms: int = 8) -> None: ...

    @abstractmethod
    def key(self, combo: KeyCombo, *, down: bool | None = None) -> None:
        """Tap a combo (``down=None``) or hold / release it."""

    def hold_key(self, combo: KeyCombo, seconds: float) -> None:
        self.key(combo, down=True)
        try:
            time.sleep(max(0.0, min(seconds, 60.0)))
        finally:
            self.key(combo, down=False)

    # -- windows -----------------------------------------------------------

    def windows(self) -> list[WindowInfo]:
        raise NotSupportedError(f"{self.name}: listing windows is not supported")

    def active_window(self) -> WindowInfo | None:
        for window in self.windows():
            if window.active:
                return window
        return None

    def focus_window(self, window_id: str) -> bool:
        raise NotSupportedError(f"{self.name}: focusing windows is not supported")

    # -- accessibility -----------------------------------------------------

    def snapshot(self, window_id: str | None = None, *, limit: int = 300) -> list[UIElement]:
        raise NotSupportedError(f"{self.name}: accessibility snapshot is not supported")

    # -- lifecycle ---------------------------------------------------------

    def permission_checks(self) -> list[Check]:
        """Inspect native permission state without capturing or opening a dialog."""
        return []

    def request_permissions(self, kind: str = "screen_recording") -> list[Check]:
        raise NotSupportedError(f"{self.name}: no permission dialog for {kind}")

    def doctor(self) -> list[Check]:
        """Permission and dependency checks; never raises."""
        try:
            info = self.screen()
            return [Check("screen", True, f"{info.width}x{info.height}")]
        except Exception as exc:  # noqa: BLE001 - doctor reports, never fails
            return [Check("screen", False, str(exc))]

    def close(self) -> None:
        return None
