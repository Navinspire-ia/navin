"""Terminal capability hints applied before Textual is imported.

Rich (and therefore Textual) only trusts ``COLORTERM=truecolor`` or a
``*-256color`` ``TERM``. WSL sessions, ssh hops and some Windows consoles ship
``TERM=xterm`` with no ``COLORTERM``, so every theme color collapses to the 16
ANSI colors: black background, gray user bubbles, light-gray footer. Every
terminal we care about (Windows Terminal, conhost, VS Code, iTerm2, kitty,
WezTerm, Alacritty, GNOME/Konsole, tmux) renders 24-bit color, so we opt in
unless the environment clearly says otherwise.

The user keeps the last word: ``TEXTUAL_COLOR_SYSTEM`` and ``NO_COLOR`` are
never overridden.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

_TRUECOLOR_MARKERS = (
    "WT_SESSION",  # Windows Terminal (also visible inside WSL)
    "WSL_DISTRO_NAME",  # any WSL console: conhost >= Win10 1703 is 24-bit
    "KONSOLE_VERSION",
    "VTE_VERSION",  # GNOME Terminal, Tilix, Terminator...
    "KITTY_WINDOW_ID",
    "ALACRITTY_WINDOW_ID",
    "WEZTERM_EXECUTABLE",
    "ITERM_SESSION_ID",
    "GHOSTTY_RESOURCES_DIR",
)


def detect_color_system(env: Mapping[str, str] | None = None) -> str | None:
    """Return the ``TEXTUAL_COLOR_SYSTEM`` value to force, or ``None`` to leave auto."""
    env = os.environ if env is None else env
    if env.get("TEXTUAL_COLOR_SYSTEM") or env.get("NO_COLOR"):
        return None
    term = env.get("TERM", "").strip().lower()
    if term in {"dumb", "linux", "vt100", "vt102", "vt220"} or term.endswith("-16color"):
        return None
    colorterm = env.get("COLORTERM", "").strip().lower()
    if colorterm in {"truecolor", "24bit"}:
        return "truecolor"
    if env.get("TERM_PROGRAM", "").strip().lower() == "apple_terminal":
        return "256"
    if any(marker in env for marker in _TRUECOLOR_MARKERS):
        return "truecolor"
    if "256color" in term or term.startswith(("xterm", "screen", "tmux", "rxvt", "st-", "foot", "wezterm", "alacritty", "kitty")):
        return "truecolor"
    return None


def prepare_terminal_env() -> None:
    """Export the color-system hint. Must run before ``import textual``."""
    forced = detect_color_system()
    if forced:
        os.environ["TEXTUAL_COLOR_SYSTEM"] = forced
