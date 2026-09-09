# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Remember which build installed the on-demand toolchains under ``~/.navin``.

Installing Navin replaces the whole product: the Windows installers, the macOS
bundle and the Linux packages all swap every file they own, so a reinstall (even
of the same version) always runs the new code. The toolchains fetched on demand
are the exception. Montage/HyperFrames under ``~/.navin/montage`` and the Mobile
SDK under ``~/.navin`` are user data: no installer touches them, and their setup
is idempotent, so it answers "already ready" forever. A fix that needs a newer
toolchain would never reach a machine that already had one.

A build stamp next to each toolchain closes that gap: the setup that installed
it records this build, and a later build sees the mismatch instead of assuming
the toolchain it finds is the one it expects.
"""

from __future__ import annotations

import json
from pathlib import Path

from navin import __version__

STAMP_NAME = ".navin-build.json"


def stamp_path(root: Path) -> Path:
    return Path(root).expanduser() / STAMP_NAME


def installed_build(root: Path) -> str | None:
    """The Navin version that last installed this toolchain, if recorded."""
    try:
        raw = stamp_path(root).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    build = str((data or {}).get("build") or "").strip()
    return build or None


def record_build(root: Path) -> None:
    """Mark this toolchain as installed by the running build. Never raises."""
    try:
        directory = Path(root).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        stamp_path(directory).write_text(
            json.dumps({"build": __version__}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        # A read-only home is not a reason to fail an install that worked.
        pass


def is_stale(root: Path, *, installed: bool = True) -> bool:
    """Whether the toolchain in *root* was installed by a different build.

    *installed* is what the caller already knows about the toolchain being
    present: nothing to refresh when there is nothing there, and the normal
    install path covers that case anyway.
    """
    if not installed:
        return False
    return installed_build(root) != __version__
