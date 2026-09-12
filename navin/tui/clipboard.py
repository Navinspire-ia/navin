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
import os
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

# Windows Terminal warns (and often drops OSC 52) above 5 KiB.
OSC52_MAX_BYTES = 4000
_win_temp_cache: str | None = None


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


def pick_paste_text(app_text: str, os_text: str) -> str:
    """Prefer the current OS payload; the in-app copy is only a fallback."""
    app = (app_text or "").replace("\x00", "")
    os_clip = os_text or ""
    return os_clip or app


def osc52_allowed(text: str) -> bool:
    """True when OSC 52 is small enough for Windows Terminal to accept."""
    return len((text or "").encode("utf-8", errors="replace")) < OSC52_MAX_BYTES


def write_os_clipboard(text: str) -> bool:
    """Write to the host clipboard when it will not trip a terminal paste guard.

    Windows Terminal intercepts Ctrl+V above 5 KiB. Large copies therefore stay
    in-app on Windows / WSL so Ctrl+V remains a normal paste. macOS pbcopy has
    no such limit; large copies go to the OS clipboard there.
    """
    payload = text or ""
    if not payload:
        return False
    if osc52_allowed(payload) or sys.platform == "darwin":
        return write_clipboard(payload)
    return False


def _win_temp_dir() -> str:
    global _win_temp_cache
    if _win_temp_cache:
        return _win_temp_cache
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return ""
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", "Write-Output $env:TEMP"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    raw = (result.stdout or b"").decode("utf-8", errors="replace").strip()
    _win_temp_cache = raw.splitlines()[0].strip() if raw else ""
    return _win_temp_cache


def _win_to_wsl(win_path: str) -> str:
    if not win_path:
        return ""
    wslpath = shutil.which("wslpath")
    if wslpath:
        try:
            result = subprocess.run(
                [wslpath, "-u", win_path],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            return (result.stdout or "").strip()
    path = win_path.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        return f"/mnt/{path[0].lower()}{path[2:]}"
    return path


def _write_windows_clipboard_file(text: str) -> bool:
    """Set-Clipboard from a UTF-8 file. Survives the 7k PowerShell -Command cap."""
    powershell = shutil.which("powershell.exe")
    temp_dir = _win_temp_dir()
    if not powershell or not temp_dir:
        return False
    name = f"navin-clip-w-{os.getpid()}.txt"
    win_file = temp_dir.rstrip("\\/") + "\\" + name
    wsl_file = _win_to_wsl(win_file)
    if not wsl_file:
        return False
    try:
        Path(wsl_file).write_text(text, encoding="utf-8")
    except OSError:
        return False
    quoted = win_file.replace("'", "''")
    command = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        f"Set-Clipboard -Value (Get-Content -Raw -Encoding UTF8 '{quoted}')"
    )
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    with suppress(OSError):
        Path(wsl_file).unlink(missing_ok=True)
    return result is not None and result.returncode == 0


def _write_windows_clipboard(text: str) -> bool:
    """Copy Unicode text to the Windows clipboard (native or WSL)."""
    if not osc52_allowed(text) and _write_windows_clipboard_file(text):
        return True
    clip = shutil.which("clip.exe")
    if clip:
        try:
            result = subprocess.run(
                [clip],
                input=text.encode("utf-16-le"),
                capture_output=True,
                timeout=8 if len(text) > 8000 else 3,
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
        return _write_windows_clipboard_file(text)
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
            timeout=20,
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
                timeout=8 if len(payload) > 8000 else 3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False
