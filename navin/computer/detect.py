"""Pick the backend that fits this machine, and say why when none does."""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.computer.base import ComputerBackend, NotSupportedError

BackendName = str  # "windows" | "macos" | "x11" | "wayland" | "none"


@dataclass(frozen=True, slots=True)
class BackendChoice:
    name: BackendName
    reason: str
    #: Extra facts worth showing in ``doctor`` (WSL, display names, ...).
    notes: tuple[str, ...] = ()


def is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        release = platform.uname().release.lower()
    except Exception:  # noqa: BLE001
        release = ""
    if "microsoft" in release or "wsl" in release:
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def detect_platform(*, preferred: str = "auto", display: str | None = None) -> BackendChoice:
    """Decide which backend to use without importing any of them."""
    wanted = (preferred or "auto").strip().lower()
    notes: list[str] = []

    if wanted not in {"auto", "windows", "macos", "x11", "wayland", "none"}:
        wanted = "auto"

    if sys.platform == "win32":
        if wanted in {"auto", "windows"}:
            return BackendChoice("windows", "Windows desktop (SendInput + GDI capture)")
        return BackendChoice("none", f"backend '{wanted}' is not available on Windows")

    if sys.platform == "darwin":
        if wanted in {"auto", "macos"}:
            return BackendChoice("macos", "macOS desktop (Quartz events + screencapture)")
        return BackendChoice("none", f"backend '{wanted}' is not available on macOS")

    # Linux / BSD: X11 or Wayland, or nothing at all on a server.
    if wanted in {"windows", "macos"}:
        return BackendChoice("none", f"backend '{wanted}' is not available on {sys.platform}")
    if is_wsl():
        notes.append(
            "WSL: this drives Linux GUI apps shown through WSLg only. To control "
            "the Windows desktop itself, run Navin on Windows."
        )
    x_display = display or os.environ.get("DISPLAY", "")
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()

    if wanted == "x11":
        if x_display:
            return BackendChoice("x11", f"X11 display {x_display}", tuple(notes))
        return BackendChoice("none", "backend 'x11' requested but DISPLAY is not set", tuple(notes))
    if wanted == "wayland":
        if wayland_display:
            return BackendChoice("wayland", f"Wayland display {wayland_display}", tuple(notes))
        return BackendChoice(
            "none", "backend 'wayland' requested but WAYLAND_DISPLAY is not set", tuple(notes)
        )
    if wanted == "none":
        return BackendChoice("none", "disabled by configuration", tuple(notes))

    # auto: a real Wayland session (GNOME, KDE, sway) gets the portal backend
    # because XTest under XWayland cannot reach native Wayland windows. When a
    # DISPLAY override is given (dedicated Xvfb), X11 wins regardless.
    if display:
        return BackendChoice("x11", f"X11 display {display} (configured)", tuple(notes))
    if wayland_display and session_type == "wayland" and not is_wsl():
        return BackendChoice("wayland", f"Wayland display {wayland_display}", tuple(notes))
    if x_display:
        return BackendChoice("x11", f"X11 display {x_display}", tuple(notes))
    if wayland_display:
        return BackendChoice("wayland", f"Wayland display {wayland_display}", tuple(notes))
    return BackendChoice(
        "none",
        "no display: neither DISPLAY nor WAYLAND_DISPLAY is set (headless server?)",
        tuple(notes),
    )


def create_backend(config: Any) -> ComputerBackend:
    """Instantiate the backend for *config* (a ``ComputerToolConfig``-like object).

    Raises :class:`NotSupportedError` with an actionable message when the machine
    has no controllable display.
    """
    preferred = str(getattr(config, "backend", "auto") or "auto")
    display = getattr(config, "display", None) or None
    choice = detect_platform(preferred=preferred, display=display)
    if choice.name == "windows":
        from navin.computer.windows import WindowsBackend

        return WindowsBackend()
    if choice.name == "macos":
        from navin.computer.macos import MacOSBackend

        return MacOSBackend()
    if choice.name == "x11":
        from navin.computer.x11 import X11Backend

        return X11Backend(display=display or os.environ.get("DISPLAY") or ":0")
    if choice.name == "wayland":
        from navin.computer.wayland import WaylandBackend

        return WaylandBackend()
    hint = " ".join(choice.notes)
    raise NotSupportedError(
        f"no desktop to control: {choice.reason}."
        + (f" {hint}" if hint else "")
        + " Set tools.computer.display to an X server (e.g. a dedicated Xvfb :99) "
        "or run Navin where a desktop session is open."
    )
