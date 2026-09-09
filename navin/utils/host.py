# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Host OS classification for installers and local tooling."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Literal

from navin.utils.wsl import is_wsl_guest, windows_path_to_wsl

HostPlatform = Literal["macos", "linux", "wsl", "windows"]


def host_platform() -> HostPlatform:
    """Classify the host for install guidance (not just ``sys.platform``)."""
    system = platform.system().lower()
    if system == "darwin" or sys.platform == "darwin":
        return "macos"
    if system == "windows" or sys.platform == "win32" or os.name == "nt":
        return "windows"
    if is_wsl_guest():
        return "wsl"
    release = platform.release().lower()
    if "microsoft" in release or "wsl" in release:
        return "wsl"
    # Some WSL setups omit the usual /proc markers but still mount the Windows
    # drive; treat that as WSL so installers pick the Linux path.
    if Path("/mnt/c/Windows").is_dir() or Path("/mnt/c/Users").is_dir():
        return "wsl"
    return "linux"


def normalize_host_path(raw: str) -> Path:
    """Resolve a user-supplied path for the OS this process actually runs on.

    Windows keeps ``C:\\Users\\me``. WSL translates that (and
    ``\\\\wsl.localhost\\...``) to a POSIX path the guest can open. Linux and
    macOS keep POSIX form and accept either slash.
    """
    text = (raw or "").strip().strip('"')
    if not text:
        raise ValueError("empty path")
    plat = host_platform()
    if plat == "windows":
        return Path(text).expanduser().resolve(strict=False)
    if plat == "wsl":
        translated = windows_path_to_wsl(text)
        if translated:
            text = translated
        else:
            text = text.replace("\\", "/")
        return Path(text).expanduser().resolve(strict=False)
    return Path(text.replace("\\", "/")).expanduser().resolve(strict=False)
