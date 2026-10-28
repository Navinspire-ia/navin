# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Terminal capability hints applied before Textual is imported, and the tab title.

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


# Terminals that draw a frame atomically between ``CSI ? 2026 h`` and
# ``CSI ? 2026 l`` (synchronized output). Textual only turns the mode on
# when the terminal answers its DECRQM query in time, and the answer often
# does not come back through WSL, ssh or a slow first frame. Without it a
# frame that rewrites the whole chat (every streamed line moves the view up)
# is shown half old, half new: the same lines twice for a few milliseconds.
_SYNC_OUTPUT_MARKERS = (
    "WT_SESSION",  # Windows Terminal (also visible inside WSL)
    "KITTY_WINDOW_ID",
    "ALACRITTY_WINDOW_ID",
    "WEZTERM_PANE",
    "ITERM_SESSION_ID",
    "GHOSTTY_RESOURCES_DIR",
)
SYNC_OUTPUT_ENV = "NAVIN_SYNC_OUTPUT"


def forces_synchronized_output(env: Mapping[str, str] | None = None) -> bool:
    """Whether to send synchronized-output frames without waiting for DECRQM.

    A terminal that does not know mode 2026 ignores it (unknown DEC private
    modes are no-ops), so the only cost of a wrong guess is a few bytes.
    ``NAVIN_SYNC_OUTPUT=1`` / ``=0`` forces the choice either way. Inside tmux
    or screen the multiplexer redraws on its own: leave it to detection.
    """
    env = os.environ if env is None else env
    choice = env.get(SYNC_OUTPUT_ENV, "").strip().lower()
    if choice in {"1", "true", "yes", "on"}:
        return True
    if choice in {"0", "false", "no", "off"}:
        return False
    if env.get("TMUX") or env.get("STY"):
        return False
    return any(env.get(marker) for marker in _SYNC_OUTPUT_MARKERS)


# -- the terminal's tab or window title -------------------------------------

TITLE_PREFIX = "navin-"
# xterm's title stack: pushed once before the first title, popped at exit, so
# the shell's own title comes back on terminals that keep one (xterm, kitty,
# WezTerm, foot, Ghostty). The others ignore both, and their shell prompt
# names the tab again at the next prompt.
TITLE_PUSH = "\x1b[22;0t"
TITLE_POP = "\x1b[23;0t"


def project_title(workspace: str | os.PathLike[str]) -> str:
    """The tab or window title of a navin-cli working in ``workspace``: ``navin-<folder>``."""
    text = str(workspace).replace("\\", "/").rstrip("/")
    name = text.rsplit("/", 1)[-1].strip() or "cli"
    return TITLE_PREFIX + name


def title_sequence(title: str, env: Mapping[str, str] | None = None) -> str:
    """The bytes that name the terminal tab (or window) ``title``.

    OSC 0 sets the icon name and the window title together; Windows Terminal,
    conhost, iTerm2, Terminal.app, kitty, WezTerm, GNOME Terminal and Konsole
    read it. Inside tmux or screen it only names the pane, so the screen-style
    rename follows it: tmux applies that one when ``allow-rename`` is on.
    """
    env = os.environ if env is None else env
    safe = "".join(ch for ch in title if ch.isprintable())
    out = f"\x1b]0;{safe}\x07"
    if env.get("TMUX") or env.get("STY"):
        out += f"\x1bk{safe}\x1b\\"
    return out
