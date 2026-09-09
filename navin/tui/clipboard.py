# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""System clipboard for the TUI.

Textual's ``App.clipboard`` is in-process only. OSC 52 is ignored by Apple
Terminal. We read and write the OS clipboard so Ctrl+C / Ctrl+V,
copy-on-select and right-click copy behave like a normal terminal.

WSL ``clip.exe`` treats UTF-8 stdin as the OEM code page (accents become
``Probl├┐mes``). Writes use UTF-16 LE; reads force PowerShell UTF-8.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys


def _read_commands() -> list[list[str]]:
    commands: list[list[str]] = []
    if sys.platform == "darwin":
        if shutil.which("pbpaste"):
            commands.append(["pbpaste"])
        return commands
    if shutil.which("wl-paste"):
        commands.append(["wl-paste", "-n"])
    if shutil.which("xclip"):
        commands.append(["xclip", "-selection", "clipboard", "-o"])
    if shutil.which("xsel"):
        commands.append(["xsel", "-b", "-o"])
    if shutil.which("pbpaste"):
        commands.append(["pbpaste"])
    return commands


def _write_commands() -> list[list[str]]:
    commands: list[list[str]] = []
    if sys.platform == "darwin":
        if shutil.which("pbcopy"):
            commands.append(["pbcopy"])
        return commands
    if shutil.which("wl-copy"):
        commands.append(["wl-copy"])
    if shutil.which("xclip"):
        commands.append(["xclip", "-selection", "clipboard"])
    if shutil.which("xsel"):
        commands.append(["xsel", "-b", "-i"])
    if shutil.which("pbcopy"):
        commands.append(["pbcopy"])
    return commands


def _normalize_clip(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    if text.endswith("\n") and "\n" not in text[:-1]:
        text = text[:-1]
    return text


def decode_windows_clipboard_bytes(raw: bytes) -> str:
    """Decode stdout from powershell.exe / clip.exe (UTF-16 or UTF-8)."""
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16-le")
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16-be")
    if len(raw) >= 4 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _write_windows_clipboard(text: str) -> bool:
    """Copy Unicode text to the Windows clipboard (native or WSL)."""
    clip = shutil.which("clip.exe")
    if clip:
        try:
            result = subprocess.run(
                [clip],
                input=text.encode("utf-16-le"),
                capture_output=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            return True
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return False
    # Here-string would break on `'@`. Base64 keeps every Unicode code point.
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    command = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$t = [System.Text.Encoding]::UTF8.GetString("
        f"[System.Convert]::FromBase64String('{b64}')); "
        "Set-Clipboard -Value $t"
    )
    if len(command) > 7000:
        return False
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _read_windows_clipboard() -> str:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return ""
    command = (
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
        "$OutputEncoding = [Console]::OutputEncoding; "
        "Get-Clipboard -Raw"
    )
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0 or not result.stdout:
        return ""
    return _normalize_clip(decode_windows_clipboard_bytes(result.stdout))


def read_clipboard() -> str:
    """Return the OS clipboard text, or ``""`` if it cannot be read."""
    if sys.platform != "darwin":
        text = _read_windows_clipboard()
        if text:
            return text
    for cmd in _read_commands():
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        text = _normalize_clip(result.stdout or "")
        if text:
            return text
    return ""


def pointer_copy_text(selected: str, last_reply: str = "") -> str:
    """Right-click payload: the selection, otherwise the last assistant reply."""
    return selected or last_reply


def write_clipboard(text: str) -> bool:
    """Copy text to the OS clipboard. True if a backend accepted it."""
    payload = (text or "").replace("\r\n", "\n")
    if not payload:
        return False
    if sys.platform != "darwin" and _write_windows_clipboard(payload):
        return True
    for cmd in _write_commands():
        try:
            result = subprocess.run(
                cmd,
                input=payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False
