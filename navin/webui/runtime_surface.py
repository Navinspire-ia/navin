"""WebUI runtime surface: browser tab vs native desktop sidecar."""

from __future__ import annotations

import os
from typing import Literal

RuntimeSurface = Literal["browser", "native"]


def normalize_surface(surface: str | None) -> RuntimeSurface:
    return "native" if surface in {"native", "desktop"} else "browser"


def desktop_sidecar_surface() -> RuntimeSurface:
    """Native when this process is the desktop window's sidecar.

    Electron and Tauri set ``NAVIN_DESKTOP_PID`` / ``NAVIN_DESKTOP_APP`` on
    ``navin webui``. Without this, bootstrap reports ``browser`` and the UI
    hides host chrome, folder pickers, and in-app updates.
    """
    if os.environ.get("NAVIN_DESKTOP_PID", "").strip():
        return "native"
    if os.environ.get("NAVIN_DESKTOP_APP", "").strip():
        return "native"
    return "browser"
