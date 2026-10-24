# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Images in CLI prompts.

Ctrl+V (or Alt+V, which Windows Terminal lets through when the clipboard holds
only an image) saves the clipboard image as a PNG and inserts an
``[image: <path>]`` token. Image paths typed, dropped or pasted in a prompt
(``.png``, ``.jpg``, ``.jpeg``, ``.webp``, ``.gif``) are attached to the turn,
so a vision model receives the picture itself, not only its name.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from navin.utils.proc import no_window_kwargs

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MAX_IMAGES = 8
_TOKEN = re.compile(r"\[image:\s*([^\]]+?)\s*\]", re.IGNORECASE)
_PATH = re.compile(
    r"""(?:"([^"]+)"|'([^']+)'|((?:[A-Za-z]:[\\/]|~|/|\.{1,2}[\\/])?[^\s"'\[\]]+))""",
)


def _clean(raw: str) -> str:
    value = raw.strip().strip("'\"")
    if value.startswith("file://"):
        value = value[7:]
    return value


def _resolve(raw: str, cwd: Path | None) -> Path | None:
    value = _clean(raw)
    if not value.lower().endswith(IMAGE_SUFFIXES):
        return None
    if len(value) >= 3 and value[1] == ":" and value[2] in "\\/" and sys.platform != "win32":
        value = f"/mnt/{value[0].lower()}/{value[3:].replace(chr(92), '/')}"
    path = Path(value).expanduser()
    if not path.is_absolute() and cwd is not None:
        path = cwd / path
    try:
        return path.resolve() if path.is_file() else None
    except OSError:
        return None


def extract_image_paths(text: str, cwd: Path | None = None) -> list[str]:
    """Existing image files named in ``text`` (tokens first), deduplicated."""
    found: list[str] = []

    def add(raw: str) -> None:
        path = _resolve(raw, cwd)
        if path is not None and str(path) not in found and len(found) < MAX_IMAGES:
            found.append(str(path))

    for match in _TOKEN.finditer(text or ""):
        add(match.group(1))
    rest = _TOKEN.sub(" ", text or "")
    for match in _PATH.finditer(rest):
        raw = next((g for g in match.groups() if g), "")
        if raw.lower().rstrip(".,;:)").endswith(IMAGE_SUFFIXES):
            add(raw.rstrip(".,;:)"))
    return found


def _run(cmd: list[str], timeout: float = 8) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False, **no_window_kwargs())
    except (OSError, subprocess.TimeoutExpired):
        return None


def _is_png(path: Path) -> bool:
    try:
        return path.is_file() and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def _save_windows_clipboard_image(target: Path) -> bool:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return False
    win_target = str(target)
    if sys.platform != "win32":
        conv = _run(["wslpath", "-w", str(target)], timeout=3) if shutil.which("wslpath") else None
        if conv is None or conv.returncode != 0:
            return False
        win_target = conv.stdout.decode("utf-8", "replace").strip()
    quoted = win_target.replace("'", "''")
    command = (
        "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
        "$img = [System.Windows.Forms.Clipboard]::GetImage(); "
        "if ($img -eq $null) { exit 3 }; "
        f"$img.Save('{quoted}', [System.Drawing.Imaging.ImageFormat]::Png)"
    )
    result = _run([powershell, "-NoProfile", "-NonInteractive", "-STA", "-Command", command], timeout=15)
    return result is not None and result.returncode == 0 and _is_png(target)


def _save_unix_clipboard_image(target: Path) -> bool:
    commands: list[list[str]] = []
    if sys.platform == "darwin":
        if shutil.which("pngpaste"):
            commands.append(["pngpaste", "-"])
        commands.append([
            "osascript", "-e",
            "set f to (open for access POSIX file \"%s\" with write permission)\n"
            "write (the clipboard as «class PNGf») to f\nclose access f" % target,
        ])
    if shutil.which("wl-paste"):
        commands.append(["wl-paste", "--type", "image/png"])
    if shutil.which("xclip"):
        commands.append(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"])
    for cmd in commands:
        result = _run(cmd)
        if result is None or result.returncode != 0:
            continue
        if cmd[0] != "osascript":
            if not result.stdout.startswith(b"\x89PNG"):
                continue
            target.write_bytes(result.stdout)
        if _is_png(target):
            return True
    return False


def save_clipboard_image(directory: Path | None = None) -> Path | None:
    """Save the clipboard image as PNG; ``None`` when it holds no image."""
    if directory is None:
        from navin.config.paths import get_media_dir

        directory = get_media_dir("cli")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"paste-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 100000}.png"
    ok = _save_windows_clipboard_image(target) if (
        sys.platform == "win32" or shutil.which("powershell.exe")
    ) else False
    if not ok:
        ok = _save_unix_clipboard_image(target)
    if ok:
        return target
    target.unlink(missing_ok=True)
    return None


def image_token(path: Path | str) -> str:
    return f"[image: {path}]"
