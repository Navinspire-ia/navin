# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Local data stays readable across updates and rollbacks.

An update replaces the application, never ``~/.navin``: config, sessions,
memories, skills and vaults stay where they are. What can still go wrong is
the format. ``DATA_VERSION`` is the layout this build writes; it is stamped
in ``~/.navin/data-version.json`` and only ever goes up. A rollback to a build
that reads an older layout is refused, and ``config.json`` is copied aside
before any install so a bad release can be undone by hand.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

# Bump when a release changes the on-disk layout in a way older builds cannot read.
DATA_VERSION = 1
_BACKUPS_KEPT = 5


def navin_home() -> Path:
    return Path.home() / ".navin"


def _stamp_path() -> Path:
    return navin_home() / "data-version.json"


def local_data_version() -> int:
    """The layout version on disk; a machine never stamped is at version 1."""
    try:
        raw = json.loads(_stamp_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Never stamped (first start of 2.4.3) or unreadable: the base layout.
        return 1
    try:
        return max(1, int(raw.get("dataVersion", 1))) if isinstance(raw, dict) else 1
    except (TypeError, ValueError):
        return 1


def stamp_data_version(version: int = DATA_VERSION) -> int:
    """Record the layout this build writes. Never lowers the stamp."""
    current = local_data_version()
    target = max(current, int(version))
    if target != current or not _stamp_path().is_file():
        _stamp_path().parent.mkdir(parents=True, exist_ok=True)
        _stamp_path().write_text(
            json.dumps({"dataVersion": target, "at": time.time()}) + "\n", encoding="utf-8",
        )
    return target


def compatible(target_data_version: Any) -> bool:
    """Whether a build that expects ``target_data_version`` can read this disk."""
    try:
        target = int(target_data_version if target_data_version is not None else 1)
    except (TypeError, ValueError):
        return False
    return target >= local_data_version()


def backups_dir() -> Path:
    return navin_home() / "updates" / "backups"


def backup_before_install(from_version: str, to_version: str) -> Path | None:
    """Copy config.json aside before the installer runs; keep the last five.

    Sessions and memories are not copied: no installer writes there, and they
    can be gigabytes. The config is the file a broken release could rewrite
    at its first start.
    """
    config = navin_home() / "config.json"
    if not config.is_file():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backups_dir() / f"{stamp}-{from_version or 'unknown'}-to-{to_version or 'unknown'}"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config, target / "config.json")
    (target / "data-version.json").write_text(
        json.dumps({"dataVersion": local_data_version()}) + "\n", encoding="utf-8",
    )
    folders = sorted(
        (path for path in backups_dir().iterdir() if path.is_dir()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    for old in folders[:-_BACKUPS_KEPT]:
        shutil.rmtree(old)
    return target
