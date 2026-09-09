# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Detect montage toolchain pieces on the host (never fatal)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from navin.montage import MONTAGE_BIN, MONTAGE_HOME
from navin.utils.proc import no_window_kwargs

_CMD_TIMEOUT_S = 12


def ffmpeg_user_bin() -> Path:
    """User-local ffmpeg installed by Montage (no root / sudo required)."""
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    return MONTAGE_HOME.expanduser() / "bin" / name


def _bundled_bin(name: str) -> str | None:
    """A static binary a packaged build ships under ``tools/``, if any.

    Installers can strip the exec bit off data files, so it is restored here
    rather than trusting the archive.
    """
    from navin.python_runtime import bundled_tool

    found = bundled_tool(name)
    if not found:
        return None
    path = Path(found)
    if sys.platform != "win32" and not os.access(path, os.X_OK):
        try:
            path.chmod(path.stat().st_mode | 0o755)
        except OSError:
            return None
    return found


def ffmpeg_bundled_bin() -> str | None:
    """The static ffmpeg a packaged build ships under ``tools/``, if any."""
    return _bundled_bin("ffmpeg")


def ffprobe_bundled_bin() -> str | None:
    """The static ffprobe shipped next to the bundled ffmpeg, if any."""
    return _bundled_bin("ffprobe")


def find_ffmpeg() -> str | None:
    """Resolve ffmpeg from PATH, then the bundled copy, then ~/.navin/montage/bin.

    PATH wins so an operator can pin a specific build (hardware encoders, a
    newer release) without repackaging the app.
    """
    which = shutil.which("ffmpeg")
    if which:
        return which
    bundled = ffmpeg_bundled_bin()
    if bundled:
        return bundled
    local = ffmpeg_user_bin()
    if not local.is_file():
        return None
    if sys.platform != "win32" and not os.access(local, os.X_OK):
        return None
    return str(local)


_CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
    "msedge",
)


@dataclass(slots=True)
class ToolchainDetect:
    node: str | None = None
    npm: str | None = None
    npx: str | None = None
    ffmpeg: str | None = None
    chrome: str | None = None
    hyperframes: str | None = None
    montage_home: str = ""
    node_version: str = ""
    hyperframes_version: str = ""
    free_disk_gb: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        lines = [
            "Montage toolchain:",
            f"  home: {self.montage_home or MONTAGE_HOME}",
            f"  node: {self.node or 'missing'}"
            + (f" ({self.node_version})" if self.node_version else ""),
            f"  npm: {self.npm or 'missing'}",
            f"  npx: {self.npx or 'missing'}",
            f"  ffmpeg: {self.ffmpeg or 'missing'}",
            f"  chrome: {self.chrome or 'missing'}",
            f"  hyperframes: {self.hyperframes or 'missing'}"
            + (f" ({self.hyperframes_version})" if self.hyperframes_version else ""),
        ]
        if self.free_disk_gb is not None:
            lines.append(f"  free disk (scratch): {self.free_disk_gb:.1f} GB")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def montage_home() -> Path:
    return MONTAGE_HOME.expanduser()


def hyperframes_bin() -> Path | None:
    """Resolve hyperframes CLI from local montage install or PATH."""
    home = montage_home()
    for name in ("hyperframes", "hyperframes.cmd", "hyperframes.ps1"):
        candidate = MONTAGE_BIN / name
        if candidate.is_file():
            return candidate
    # Windows npm bin shims
    win = home / "node_modules" / "hyperframes" / "bin" / "hyperframes.js"
    if win.is_file():
        return win
    which = shutil.which("hyperframes")
    return Path(which) if which else None


def find_chrome() -> str | None:
    """Locate Chrome/Chromium for HyperFrames (Puppeteer) and Montage doctor.

    Order: explicit env → system Chrome/Edge on PATH → common install paths →
    Playwright's cached Chromium (same browser the ``browser`` tool uses).
    """
    env = (
        os.environ.get("PUPPETEER_EXECUTABLE_PATH")
        or os.environ.get("CHROME_PATH")
        or os.environ.get("GOOGLE_CHROME_BIN")
        or os.environ.get("NAVIN_CHROMIUM")
        or os.environ.get("NAVIN_CHROME_PATH")
        or ""
    ).strip()
    if env and Path(env).is_file():
        return env
    for name in _CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    # Common absolute paths
    extras = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in extras:
        if Path(path).is_file():
            return path
    # Playwright installs its own Chromium under ~/.cache/ms-playwright (or
    # LOCALAPPDATA on Windows). Browser demos already use that binary - Montage
    # doctor must not report "missing" when Playwright Chromium is present.
    try:
        from navin.documents._chromium import find_chromium

        playwright_chrome = find_chromium()
        if playwright_chrome and Path(playwright_chrome).is_file():
            return playwright_chrome
    except Exception:
        pass
    return None


def _version(cmd: list[str]) -> str:
    try:
        completed = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CMD_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    out = (completed.stdout or completed.stderr or "").strip().splitlines()
    return out[0].strip() if out else ""


def _free_disk_gb(path: Path) -> float | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
        return usage.free / (1024**3)
    except OSError:
        return None


def detect_toolchain() -> ToolchainDetect:
    """Inspect host tools used by Montage / HyperFrames."""
    home = montage_home()
    node = shutil.which("node")
    npm = shutil.which("npm")
    npx = shutil.which("npx")
    ffmpeg = find_ffmpeg()
    chrome = find_chrome()
    hf = hyperframes_bin()

    notes: list[str] = []
    if chrome:
        if "ms-playwright" in chrome.replace("\\", "/"):
            notes.append(
                "Reusing Playwright Chromium cache (same browser as the browser tool)."
            )
        else:
            notes.append(
                "Reusing system Chrome/Chromium (no Chromium download required)."
            )
    else:
        notes.append(
            "No Chrome/Playwright Chromium found; HyperFrames/Puppeteer may "
            "download Chromium on first render (or run: playwright install chromium)."
        )

    node_version = _version([node, "--version"]) if node else ""
    hf_version = ""
    if hf is not None:
        if hf.suffix == ".js":
            if node:
                hf_version = _version([node, str(hf), "--version"]) or "installed"
        else:
            hf_version = _version([str(hf), "--version"]) or "installed"

    return ToolchainDetect(
        node=node,
        npm=npm,
        npx=npx,
        ffmpeg=ffmpeg,
        chrome=chrome,
        hyperframes=str(hf) if hf else None,
        montage_home=str(home),
        node_version=node_version,
        hyperframes_version=hf_version,
        free_disk_gb=_free_disk_gb(home),
        notes=notes,
    )


def detect_json() -> str:
    return json.dumps(detect_toolchain().to_dict(), indent=2)
