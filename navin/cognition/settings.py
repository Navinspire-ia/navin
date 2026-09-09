# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-project opt-in flag for the cognition sidecar.

The flag is a small JSON file, ``<project>/.navin/cognition.json``::

    {"schema_version": 1, "enabled": true, "episodes": true, "recall": true}

``enabled`` is the master switch. ``episodes`` (journal at the end of a
turn) and ``recall`` (the search tool) default to the master value, so the
one-line form ``{"enabled": true}`` turns both on.

Reading is cheap on purpose: one ``os.stat`` per call, and the parsed value
is reused while ``(mtime_ns, size)`` is unchanged. A missing or malformed
file means *off*. Nothing here creates directories or raises.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from navin.workspace_layout import navin_dir

SETTINGS_NAME = "cognition.json"
SCHEMA_VERSION = 1

# A flag file is a handful of bytes. Anything larger is not ours to parse on
# the turn path.
_MAX_SETTINGS_BYTES = 16 * 1024

_FEATURES = ("episodes", "recall")


@dataclass(frozen=True, slots=True)
class CognitionSettings:
    enabled: bool = False
    episodes: bool = False
    recall: bool = False

    def feature(self, name: str | None) -> bool:
        if not self.enabled:
            return False
        if name is None:
            return True
        return bool(getattr(self, name, False))


_OFF = CognitionSettings()

# path -> (mtime_ns, size, settings). Module level so every caller in the
# process (hook factory, tool, tests) shares the same parse.
_CACHE: dict[str, tuple[int, int, CognitionSettings]] = {}
_CACHE_LOCK = threading.Lock()
_WARNED: set[str] = set()


def settings_path(workspace: Path | str) -> Path:
    return navin_dir(workspace) / SETTINGS_NAME


def _normalize(raw: Any) -> CognitionSettings:
    """Stored values, master included. ``feature()`` is what gates on it.

    The sub-switches keep their stored value while the master is off, so a
    user who turned recall off, then paused memory, gets the same choices
    back when resuming. A missing sub-key means on.
    """
    if not isinstance(raw, dict):
        return _OFF
    enabled = raw.get("enabled")
    values: dict[str, bool] = {}
    for name in _FEATURES:
        value = raw.get(name, True)
        values[name] = value if isinstance(value, bool) else True
    return CognitionSettings(enabled=isinstance(enabled, bool) and enabled, **values)


def _parse(path: Path) -> CognitionSettings:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        key = str(path)
        if key not in _WARNED:
            _WARNED.add(key)
            logger.warning("cognition settings ignored {}: {}", path, exc)
        return _OFF
    return _normalize(raw)


def read_settings(workspace: Path | str | None) -> CognitionSettings:
    """Settings for one project. Off when the file is missing or unreadable."""
    if workspace is None:
        return _OFF
    path = settings_path(workspace)
    try:
        stat = os.stat(path)
    except OSError:
        return _OFF
    if stat.st_size > _MAX_SETTINGS_BYTES:
        return _OFF
    key = str(path)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    settings = _parse(path)
    with _CACHE_LOCK:
        _CACHE[key] = (stat.st_mtime_ns, stat.st_size, settings)
    return settings


def cognition_enabled(workspace: Path | str | None, feature: str | None = None) -> bool:
    """True when the project opted in (and, if given, the feature is on)."""
    return read_settings(workspace).feature(feature)


def write_settings(
    workspace: Path | str,
    *,
    enabled: bool,
    episodes: bool | None = None,
    recall: bool | None = None,
) -> Path:
    """Write the flag file atomically. The only writer that creates ``.navin``."""
    path = settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "enabled": bool(enabled),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if episodes is not None:
        payload["episodes"] = bool(episodes)
    if recall is not None:
        payload["recall"] = bool(recall)
    fd, tmp_name = tempfile.mkstemp(prefix=".cognition-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    with _CACHE_LOCK:
        _CACHE.pop(str(path), None)
    return path


_FIELDS = ("enabled", *_FEATURES)


def update_settings(workspace: Path | str, fields: dict[str, Any]) -> CognitionSettings:
    """Merge ``fields`` into the flag file and return the new settings.

    Only ``enabled``, ``episodes`` and ``recall`` are accepted, booleans only:
    this is what the Guardrails panel writes, one switch at a time.
    """
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty object")
    for key, value in fields.items():
        if key not in _FIELDS:
            raise ValueError(f"unknown cognition field: {key}")
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
    # A project that never opted in has every feature on once enabled, so the
    # first write of one sub-switch must not silently disable the other one.
    # With a file present, the stored choices are the base, master on or off.
    current = read_settings(workspace) if settings_path(workspace).exists() else CognitionSettings(
        enabled=False, episodes=True, recall=True
    )
    merged = {
        "enabled": fields.get("enabled", current.enabled),
        "episodes": fields.get("episodes", current.episodes),
        "recall": fields.get("recall", current.recall),
    }
    write_settings(workspace, **merged)
    settings = read_settings(workspace)
    # Machine-level index so a gateway booted later knows which projects
    # want the recall tool without walking every session.
    from navin.cognition.registration import remember_project

    remember_project(workspace, settings.feature("recall"))
    return settings


def cognition_state(
    workspace: Path | str,
    *,
    recall_registered: bool | None = None,
) -> dict[str, Any]:
    """Settings plus what exists on disk, for the Guardrails panel.

    ``recall_registered`` is what the serving agent's tool registry says right
    now (``None`` when the caller cannot see it). The journal hook is decided
    per turn, so it never needs a restart; the tool does unless the gateway
    synced it at runtime.
    """
    from navin.cognition.episodes import episodes_path

    settings = read_settings(workspace)
    journal = episodes_path(workspace)
    try:
        journal_bytes = os.stat(journal).st_size
    except OSError:
        journal_bytes = 0
    wants_recall = settings.enabled and settings.recall
    return {
        "enabled": settings.enabled,
        "episodes": settings.episodes,
        "recall": settings.recall,
        "journal_bytes": journal_bytes,
        "settings_file": ".navin/" + SETTINGS_NAME,
        "recall_registered": recall_registered,
        "recall_requires_restart": wants_recall and recall_registered is not True,
    }


def clear_settings_cache() -> None:
    """Forget parsed flags (tests that rewrite files within the same mtime tick)."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _WARNED.clear()
