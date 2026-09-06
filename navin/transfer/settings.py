"""S5.0 - the flag of the transfer protocol (``<project>/.navin/transfer.json``).

S5 is not a feature; it is the threshold before anyone may even discuss a
strong claim about Navin. The flag therefore opens nothing in the chat: it
only allows the secret campaign job and the safety dossier to run for this
project. Off (the default, and the state of every project today) means no
secret suite is drawn, no dossier is written, no claim is computed, no
autonomy is widened; there is not even a per-turn hook to answer ``None``.

The flag refuses to go on while the hard prerequisites are not met (S2
finished, S3.3 up, S4.3 up): a transfer campaign without them is a demo.

Fields:

* ``enabled`` - master switch, off by default;
* ``suites_dir`` - where the secret suites live. Must be outside the project
  and outside any git repository; default ``~/.navin/transfer/suites``.
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

SETTINGS_NAME = "transfer.json"
SCHEMA_VERSION = 1

_MAX_SETTINGS_BYTES = 16 * 1024
_MAX_PATH_CHARS = 1024


@dataclass(frozen=True, slots=True)
class TransferSettings:
    enabled: bool = False
    suites_dir: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_OFF = TransferSettings()

_CACHE: dict[str, tuple[int, int, TransferSettings]] = {}
_CACHE_LOCK = threading.Lock()
_WARNED: set[str] = set()


def settings_path(workspace: Path | str) -> Path:
    return navin_dir(workspace) / SETTINGS_NAME


def _normalize(raw: Any) -> TransferSettings:
    if not isinstance(raw, dict):
        return _OFF
    enabled = raw.get("enabled")
    suites_dir = raw.get("suites_dir")
    if not isinstance(suites_dir, str) or not suites_dir.strip() or len(suites_dir) > _MAX_PATH_CHARS:
        suites_dir = None
    return TransferSettings(enabled=isinstance(enabled, bool) and enabled, suites_dir=suites_dir)


def _parse(path: Path) -> TransferSettings:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        key = str(path)
        if key not in _WARNED:
            _WARNED.add(key)
            logger.warning("transfer settings ignored {}: {}", path, exc)
        return _OFF
    return _normalize(raw)


def read_settings(workspace: Path | str | None) -> TransferSettings:
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


def transfer_enabled(workspace: Path | str | None) -> bool:
    return read_settings(workspace).enabled


def write_settings(workspace: Path | str, settings: TransferSettings) -> Path:
    """Write the flag file atomically. The only writer that creates ``.navin``."""
    path = settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        **settings.as_dict(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    fd, tmp_name = tempfile.mkstemp(prefix=".transfer-", suffix=".json", dir=str(path.parent))
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


BOOL_FIELDS = ("enabled",)
STR_FIELDS = ("suites_dir",)
FIELDS = (*BOOL_FIELDS, *STR_FIELDS)


def validate_fields(fields: dict[str, Any]) -> None:
    """Raise ``ValueError`` on an unknown key or a wrong type."""
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty object")
    for key, value in fields.items():
        if key in BOOL_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif key in STR_FIELDS:
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > _MAX_PATH_CHARS):
                raise ValueError(f"{key} must be a non-empty path or null")
        else:
            raise ValueError(f"unknown transfer field: {key}")


def update_settings(workspace: Path | str, fields: dict[str, Any]) -> TransferSettings:
    """Merge ``fields`` into the flag file. The raw writer: the panel and the
    CLI go through ``navin.transfer.state.transfer_update``, which refuses
    ``enabled: true`` while the prerequisites are not met."""
    validate_fields(fields)
    current = read_settings(workspace) if settings_path(workspace).exists() else _OFF
    merged = replace(current, **fields)
    write_settings(workspace, merged)
    return read_settings(workspace)


def clear_settings_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _WARNED.clear()
