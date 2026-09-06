"""System clipboard for the TUI.

Textual's ``App.clipboard`` is in-process only. On WSL / Windows the text the
user copied in the browser or another app lives in the OS clipboard: we read
that so Ctrl+V behaves like a normal terminal.
"""

from __future__ import annotations

import shutil
import subprocess


def read_clipboard() -> str:
    """Return the OS clipboard text, or ``""`` if it cannot be read."""
    commands: list[list[str]] = []
    if shutil.which("powershell.exe"):
        commands.append(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-Clipboard -Raw",
            ]
        )
    if shutil.which("wl-paste"):
        commands.append(["wl-paste", "-n"])
    if shutil.which("xclip"):
        commands.append(["xclip", "-selection", "clipboard", "-o"])
    if shutil.which("xsel"):
        commands.append(["xsel", "-b", "-o"])
    if shutil.which("pbpaste"):
        commands.append(["pbpaste"])
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        text = (result.stdout or "").replace("\r\n", "\n").replace("\r", "\n")
        if text.endswith("\n") and "\n" not in text[:-1]:
            text = text[:-1]
        if text:
            return text
    return ""
