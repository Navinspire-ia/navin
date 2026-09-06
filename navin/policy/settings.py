"""Per-project opt-in flag for policy learning (S4).

The flag is a small JSON file, ``<project>/.navin/policy.json``::

    {
      "schema_version": 1,
      "enabled": false,
      "log": true,
      "train": true,
      "steer": false
    }

``enabled`` is the master switch and it is **off by default**. While it is
off the contract is strict: no policy trajectory, no training job, no
steering in any turn, and the per-turn hook factory answers ``None`` after
one ``os.stat``. The three corridors gate on their own sub-switch:

* ``log``: the eval runner appends one line per step of an eval episode
  (state, action, observation, eval reward) after the run, from a
  background thread (S4.1). Nothing is ever logged from a chat turn;
* ``train``: a training job may run when enough turns went by, in a
  separate process, never inside a turn (S4.2);
* ``steer``: the live proposal. It cannot be switched on while the gate is
  closed (adapter N+1 did not beat N without a suite down, or the offline
  A/B showed no gain, S4.4) and it switches itself off when the live A/B
  regresses.

``enabled`` itself is refused while the world model exam (S3.3) is not
``up``: no policy without a radar. That check lives in
``navin.policy.state.policy_update``; this module only stores values.

The advanced knobs (``confidence_threshold``, ``train_every``, ``min_rows``)
live in the same file and only the CLI writes them.

Reading mirrors ``navin.world_model.settings``: one ``os.stat`` per call,
the parsed value reused while ``(mtime_ns, size)`` is unchanged. A missing or
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

SETTINGS_NAME = "policy.json"
SCHEMA_VERSION = 1

_MAX_SETTINGS_BYTES = 16 * 1024

# Corridors a project can switch individually once the master is on.
STAGES = ("log", "train", "steer")

DEFAULT_CONFIDENCE_THRESHOLD = 0.7
# Chat turns between two automatic training jobs.
DEFAULT_TRAIN_EVERY = 40
# Successful eval steps needed before a checkpoint may be fitted.
DEFAULT_MIN_ROWS = 20


@dataclass(frozen=True, slots=True)
class PolicySettings:
    enabled: bool = False
    log: bool = True
    train: bool = True
    steer: bool = False
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    train_every: int = DEFAULT_TRAIN_EVERY
    min_rows: int = DEFAULT_MIN_ROWS

    def feature(self, name: str | None) -> bool:
        """True when the master is on and, if given, the corridor is on too."""
        if not self.enabled:
            return False
        if name is None:
            return True
        return getattr(self, name, False) is True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_OFF = PolicySettings()

_CACHE: dict[str, tuple[int, int, PolicySettings]] = {}
_CACHE_LOCK = threading.Lock()
_WARNED: set[str] = set()


def settings_path(workspace: Path | str) -> Path:
    return navin_dir(workspace) / SETTINGS_NAME


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, float(value)))


def _normalize(raw: Any) -> PolicySettings:
    """Stored values, master included. ``feature()`` is what gates on it."""
    if not isinstance(raw, dict):
        return _OFF
    enabled = raw.get("enabled")
    values: dict[str, Any] = {}
    for name in STAGES:
        value = raw.get(name, getattr(_OFF, name))
        values[name] = value if isinstance(value, bool) else getattr(_OFF, name)
    values["confidence_threshold"] = _clamp_float(
        raw.get("confidence_threshold"), DEFAULT_CONFIDENCE_THRESHOLD, 0.5, 0.99
    )
    values["train_every"] = _clamp_int(raw.get("train_every"), DEFAULT_TRAIN_EVERY, 1, 5000)
    values["min_rows"] = _clamp_int(raw.get("min_rows"), DEFAULT_MIN_ROWS, 4, 5000)
    return PolicySettings(enabled=isinstance(enabled, bool) and enabled, **values)


def _parse(path: Path) -> PolicySettings:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        key = str(path)
        if key not in _WARNED:
            _WARNED.add(key)
            logger.warning("policy settings ignored {}: {}", path, exc)
        return _OFF
    return _normalize(raw)


def read_settings(workspace: Path | str | None) -> PolicySettings:
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


def policy_enabled(workspace: Path | str | None, stage: str | None = None) -> bool:
    """True when the project opted in (and, if given, the corridor is on)."""
    return read_settings(workspace).feature(stage)


def write_settings(workspace: Path | str, settings: PolicySettings) -> Path:
    """Write the flag file atomically. The only writer that creates ``.navin``."""
    path = settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        **settings.as_dict(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    fd, tmp_name = tempfile.mkstemp(prefix=".policy-", suffix=".json", dir=str(path.parent))
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


BOOL_FIELDS = ("enabled", *STAGES)
INT_FIELDS = {"train_every": (1, 5000), "min_rows": (4, 5000)}
FLOAT_FIELDS = {"confidence_threshold": (0.5, 0.99)}
FIELDS = (*BOOL_FIELDS, *INT_FIELDS, *FLOAT_FIELDS)


def validate_fields(fields: dict[str, Any]) -> None:
    """Raise ``ValueError`` on an unknown key or a wrong type / range."""
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty object")
    for key, value in fields.items():
        if key in BOOL_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif key in INT_FIELDS:
            low, high = INT_FIELDS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{key} must be an integer between {low} and {high}")
        elif key in FLOAT_FIELDS:
            low, high = FLOAT_FIELDS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f"{key} must be a number between {low} and {high}")
        else:
            raise ValueError(f"unknown policy field: {key}")


def update_settings(workspace: Path | str, fields: dict[str, Any]) -> PolicySettings:
    """Merge ``fields`` into the flag file and return the new settings.

    This is the raw writer: the AGI panel and the CLI go through
    ``navin.policy.state.policy_update``, which also refuses ``enabled: true``
    while the world model radar is not up and ``steer: true`` while the gate
    is closed.
    """
    validate_fields(fields)
    current = read_settings(workspace) if settings_path(workspace).exists() else _OFF
    merged = replace(current, **fields)
    write_settings(workspace, merged)
    return read_settings(workspace)


def clear_settings_cache() -> None:
    """Forget parsed flags (tests that rewrite files within the same mtime tick)."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _WARNED.clear()
