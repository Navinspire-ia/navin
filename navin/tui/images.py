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

import base64
import contextlib
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
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


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _is_png(path: Path) -> bool:
    """Whether ``path`` starts like a PNG: 8 bytes read, not the whole image."""
    try:
        with path.open("rb") as handle:
            return handle.read(8) == _PNG_MAGIC
    except OSError:
        return False


# Reading the Windows clipboard started a PowerShell and loaded Windows Forms
# on every paste (1.2 s measured on the user's machine before one pixel
# moved), then had Windows write the PNG into the WSL home through the 9P
# bridge. One PowerShell now stays up after the first paste with Forms loaded
# and hands the PNG back on its stdout: the next pastes cost the copy alone,
# and no file crosses the bridge.
_HELPER_SCRIPT = "\n".join((
    "$ErrorActionPreference = 'Stop'",
    "Add-Type -AssemblyName System.Windows.Forms",
    "Add-Type -AssemblyName System.Drawing",
    "[Console]::Out.WriteLine('READY'); [Console]::Out.Flush()",
    "while ($true) {",
    "  $line = [Console]::In.ReadLine()",
    "  if ($null -eq $line) { break }",
    "  try {",
    "    $png = $null",
    # A screenshot or a browser copy already holds a PNG: sent as is, no re-encoding.
    "    try {",
    "      $data = [System.Windows.Forms.Clipboard]::GetDataObject()",
    "      if ($null -ne $data) {",
    "        $raw = $data.GetData('PNG')",
    "        if ($raw -is [System.IO.MemoryStream]) { $png = $raw.ToArray() }",
    "      }",
    "    } catch { $png = $null }",
    "    if ($null -eq $png) {",
    "      $img = [System.Windows.Forms.Clipboard]::GetImage()",
    "      if ($null -ne $img) {",
    "        $ms = New-Object System.IO.MemoryStream",
    "        $img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)",
    "        $img.Dispose()",
    "        $png = $ms.ToArray()",
    "        $ms.Dispose()",
    "      }",
    "    }",
    "    if ($null -eq $png) { [Console]::Out.WriteLine('NONE') }",
    "    else { [Console]::Out.WriteLine('PNG ' + [Convert]::ToBase64String($png)) }",
    "  } catch { [Console]::Out.WriteLine('ERR ' + $_.Exception.Message) }",
    "  [Console]::Out.Flush()",
    "}",
))

# One PowerShell for one paste, when the kept helper cannot start. The PNG
# comes back base64 on stdout as well: no wslpath round trip and no write
# through the \\wsl$ (9P) share, Python writes the file.
_ONCE_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
    "$d = [System.Windows.Forms.Clipboard]::GetDataObject(); "
    "if ($null -eq $d) { exit 3 }; "
    "$s = $d.GetData('PNG'); "
    "if ($s -is [System.IO.MemoryStream]) { [Console]::Out.Write([Convert]::ToBase64String($s.ToArray())); exit 0 }; "
    "$img = [System.Windows.Forms.Clipboard]::GetImage(); "
    "if ($null -eq $img) { exit 3 }; "
    "$m = New-Object System.IO.MemoryStream; "
    "$img.Save($m, [System.Drawing.Imaging.ImageFormat]::Png); "
    "[Console]::Out.Write([Convert]::ToBase64String($m.ToArray()))"
)


def _powershell_argv(powershell: str) -> list[str]:
    encoded = base64.b64encode(_HELPER_SCRIPT.encode("utf-16-le")).decode("ascii")
    return [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded]


def _pump_lines(proc: subprocess.Popen[bytes], lines: queue.Queue[bytes | None]) -> None:
    """Every line the helper prints, then None once it is gone."""
    stdout = proc.stdout
    if stdout is not None:
        for raw in iter(stdout.readline, b""):
            lines.put(raw.rstrip(b"\r\n"))
    lines.put(None)


class ClipboardHelper:
    """One PowerShell kept up with Windows Forms loaded, asked one line per paste.

    It ends by itself when this process does: its stdin closes and its read
    loop stops, so quitting the CLI never waits on it.
    """

    start_timeout_s = 20.0
    read_timeout_s = 15.0

    def __init__(self, argv_for: Callable[[str], list[str]] = _powershell_argv) -> None:
        self._argv_for = argv_for
        self._proc: subprocess.Popen[bytes] | None = None
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._lock = threading.Lock()

    def png(self, powershell: str) -> tuple[bool, bytes | None]:
        """(the helper answered, the clipboard PNG or None when it holds no image)."""
        with self._lock:
            if not self._alive() and not self._start(powershell):
                return False, None
            proc = self._proc
            if proc is None or proc.stdin is None:
                return False, None
            try:
                proc.stdin.write(b"png\n")
                proc.stdin.flush()
            except OSError:
                self._stop()
                return False, None
            line = self._next_line(self.read_timeout_s)
            if line == b"NONE":
                return True, None
            if line is not None and line.startswith(b"PNG "):
                try:
                    data = base64.b64decode(line[4:], validate=True)
                except ValueError:
                    data = b""
                if data.startswith(_PNG_MAGIC):
                    return True, data
            # No answer in time, a broken frame or an error: a fresh helper next time.
            self._stop()
            return False, None

    def close(self) -> None:
        with self._lock:
            self._stop()

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _start(self, powershell: str) -> bool:
        self._stop()
        try:
            proc = subprocess.Popen(
                self._argv_for(powershell),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                **no_window_kwargs(),
            )
        except OSError:
            return False
        self._proc = proc
        self._lines = lines = queue.Queue()
        threading.Thread(target=_pump_lines, args=(proc, lines), name="navin-clipboard", daemon=True).start()
        if self._next_line(self.start_timeout_s) == b"READY":
            return True
        self._stop()
        return False

    def _next_line(self, timeout: float) -> bytes | None:
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            proc.wait(timeout=2)


_HELPER = ClipboardHelper()


def _save_windows_clipboard_image(target: Path) -> bool:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return False
    answered, png = _HELPER.png(powershell)
    if answered:
        if png is None:
            return False
        target.write_bytes(png)
        return _is_png(target)
    # The helper could not start (an execution policy, a broken PowerShell):
    # the one-shot way still works, at its old price.
    return _save_windows_clipboard_image_once(target, powershell)


def _save_windows_clipboard_image_once(target: Path, powershell: str) -> bool:
    result = _run([powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-Command", _ONCE_SCRIPT], timeout=15)
    if result is None or result.returncode != 0:
        return False
    try:
        data = base64.b64decode(b"".join(result.stdout.split()), validate=True)
    except ValueError:
        return False
    if not data.startswith(_PNG_MAGIC):
        return False
    target.write_bytes(data)
    return True


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
