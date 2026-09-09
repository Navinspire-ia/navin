# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-project opt-in flag for skills evolution (S2).

The flag is a small JSON file, ``<project>/.navin/skills-evolve.json``::

    {
      "schema_version": 1,
      "enabled": false,
      "draft": true,
      "promote_project": true,
      "publish_harness": false
    }

``enabled`` is the master switch and it is **off by default**. While it is
off the contract is strict: no draft folder is created, no exam runs, no
new skill is loaded, and the per-turn hook factory answers ``None`` after
one ``os.stat``. The three sub-switches gate the corridor stages:

* ``draft``: the agent may write skill drafts on its own (S2.1);
* ``promote_project``: an eligible draft may enter ``.navin/skills`` without
  a click (S2.4);
* ``publish_harness``: the **human** "publish for every project" button is
  offered. The publish action itself still requires a human actor (S2.5).

The advanced knobs (``failure_threshold``, ``max_attempts``, ``exam_model``,
``author``) are stored in the same file and only the CLI writes them.

Reading mirrors ``navin.cognition.settings``: one ``os.stat`` per call, the
parsed value reused while ``(mtime_ns, size)`` is unchanged. A missing or
malformed file means *off*. Nothing here creates directories or raises.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from loguru import logger

from navin.workspace_layout import navin_dir

SETTINGS_NAME = "skills-evolve.json"
SCHEMA_VERSION = 1

_MAX_SETTINGS_BYTES = 16 * 1024

# Corridor stages a project can switch individually once the master is on.
STAGES = ("draft", "promote_project", "publish_harness")

EXAM_MODELS = ("lexical", "llm")
AUTHORS = ("auto", "template", "llm")

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class SkillsEvolveSettings:
    enabled: bool = False
    draft: bool = True
    promote_project: bool = True
    publish_harness: bool = False
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    exam_model: str = "lexical"
    author: str = "auto"

    def feature(self, name: str | None) -> bool:
        """True when the master is on and, if given, the stage is on too."""
        if not self.enabled:
            return False
        if name is None:
            return True
        value = getattr(self, name, False)
        return value is True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_OFF = SkillsEvolveSettings()

_CACHE: dict[str, tuple[int, int, SkillsEvolveSettings]] = {}
_CACHE_LOCK = threading.Lock()
_WARNED: set[str] = set()


def settings_path(workspace: Path | str) -> Path:
    return navin_dir(workspace) / SETTINGS_NAME


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def _normalize(raw: Any) -> SkillsEvolveSettings:
    """Stored values, master included. ``feature()`` is what gates on it.

    Sub-switches keep their stored value while the master is off, so a
    project that paused evolution gets the same choices back when resuming.
    A missing stage key means the default of that stage.
    """
    if not isinstance(raw, dict):
        return _OFF
    enabled = raw.get("enabled")
    values: dict[str, Any] = {}
    for name in STAGES:
        value = raw.get(name, getattr(_OFF, name))
        values[name] = value if isinstance(value, bool) else getattr(_OFF, name)
    values["failure_threshold"] = _clamp_int(
        raw.get("failure_threshold"), DEFAULT_FAILURE_THRESHOLD, 1, 20
    )
    values["max_attempts"] = _clamp_int(raw.get("max_attempts"), DEFAULT_MAX_ATTEMPTS, 1, 5)
    exam_model = raw.get("exam_model")
    values["exam_model"] = exam_model if exam_model in EXAM_MODELS else "lexical"
    author = raw.get("author")
    values["author"] = author if author in AUTHORS else "auto"
    return SkillsEvolveSettings(enabled=isinstance(enabled, bool) and enabled, **values)


def _parse(path: Path) -> SkillsEvolveSettings:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        key = str(path)
        if key not in _WARNED:
            _WARNED.add(key)
            logger.warning("skills-evolve settings ignored {}: {}", path, exc)
        return _OFF
    return _normalize(raw)


def read_settings(workspace: Path | str | None) -> SkillsEvolveSettings:
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


def evolve_enabled(workspace: Path | str | None, stage: str | None = None) -> bool:
    """True when the project opted in (and, if given, the stage is on)."""
    return read_settings(workspace).feature(stage)


def write_settings(workspace: Path | str, settings: SkillsEvolveSettings) -> Path:
    """Write the flag file atomically. The only writer that creates ``.navin``."""
    path = settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        **settings.as_dict(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    fd, tmp_name = tempfile.mkstemp(
        prefix=".skills-evolve-", suffix=".json", dir=str(path.parent)
    )
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


_BOOL_FIELDS = ("enabled", *STAGES)
_INT_FIELDS = {"failure_threshold": (1, 20), "max_attempts": (1, 5)}
_CHOICE_FIELDS = {"exam_model": EXAM_MODELS, "author": AUTHORS}
FIELDS = (*_BOOL_FIELDS, *_INT_FIELDS, *_CHOICE_FIELDS)


def update_settings(workspace: Path | str, fields: dict[str, Any]) -> SkillsEvolveSettings:
    """Merge ``fields`` into the flag file and return the new settings.

    The AGI panel writes one boolean switch at a time; the CLI may also set
    the advanced knobs. Unknown keys and wrong types raise ``ValueError``.
    """
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty object")
    for key, value in fields.items():
        if key in _BOOL_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif key in _INT_FIELDS:
            low, high = _INT_FIELDS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{key} must be an integer between {low} and {high}")
        elif key in _CHOICE_FIELDS:
            if value not in _CHOICE_FIELDS[key]:
                raise ValueError(f"{key} must be one of {', '.join(_CHOICE_FIELDS[key])}")
        else:
            raise ValueError(f"unknown skills-evolve field: {key}")
    current = read_settings(workspace) if settings_path(workspace).exists() else _OFF
    merged = replace(current, **fields)
    write_settings(workspace, merged)
    return read_settings(workspace)


def clear_settings_cache() -> None:
    """Forget parsed flags (tests that rewrite files within the same mtime tick)."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _WARNED.clear()
