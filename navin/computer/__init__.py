"""Desktop control ("computer use"): screen capture plus OS-level mouse and keyboard.

The agent already drives a Chromium page through ``browser``. This package is
the same idea for the whole desktop: a backend per windowing system exposes
``screenshot`` / ``click`` / ``type`` / ``key`` on the real display, so any
application the user can see and click, the agent can too.

Backends never import each other's platform libraries at module import time;
:func:`navin.computer.detect.create_backend` picks the one that fits the
machine and every heavy import stays behind it.
"""

from navin.computer.base import (
    Check,
    ComputerBackend,
    ComputerError,
    DisplayInfo,
    NotSupportedError,
    PermissionMissingError,
    ScreenInfo,
    Screenshot,
    UIElement,
    WindowInfo,
)
from navin.computer.detect import BackendChoice, create_backend, detect_platform
from navin.computer.keys import KeyCombo, parse_combo

__all__ = [
    "BackendChoice",
    "Check",
    "ComputerBackend",
    "ComputerError",
    "DisplayInfo",
    "KeyCombo",
    "NotSupportedError",
    "PermissionMissingError",
    "ScreenInfo",
    "Screenshot",
    "UIElement",
    "WindowInfo",
    "create_backend",
    "detect_platform",
    "parse_combo",
]
