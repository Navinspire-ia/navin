# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Which board tasks the current run is working on.

The board is project-scoped and long-lived: after a few sessions it holds work
from conversations nobody is watching any more. A plan panel that showed all of
it would be a second kanban rather than "what is happening right now", so the
chat needs the narrower question - of everything on the board, which tasks did
*this* run touch, and in what order.

Focus is kept out of ``.navin/board/`` so session identifiers do not travel with
the repository. It is persisted under the WebUI runtime dir
(``~/.navin/webui/session-focus.json``) so a gateway restart mid-plan can still
rebuild the chat checklist from the same task ids.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

from filelock import FileLock
from loguru import logger

# A plan longer than this is not a plan the reader can hold, and the panel would
# scroll past the answer. The board itself keeps everything.
_MAX_TASKS_PER_SESSION = 40
# Enough for every conversation a user has open at once; the oldest is evicted
# rather than letting a long-lived gateway grow without bound.
_MAX_SESSIONS = 64

_focus: OrderedDict[str, list[str]] = OrderedDict()
_guard = threading.Lock()
_loaded = False
# Multi-process CLI and gateway share the same file; the file's signature
# detects a different process replacing a session's plan.
_loaded_signature: tuple[int, int, int, int] | None = None


def _store_path() -> Path:
    from navin.config.paths import get_webui_dir

    return get_webui_dir() / "session-focus.json"


def _signature(path: Path) -> tuple[int, int, int, int] | None:
    """Identity and version of the on-disk snapshot, even across atomic writes."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size


def _ensure_loaded(*, _reread: bool = True) -> None:
    global _loaded, _loaded_signature
    path = _store_path()
    signature = _signature(path)
    if _loaded and signature == _loaded_signature:
        return
    snapshot: OrderedDict[str, list[str]] = OrderedDict()
    if signature is not None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("session focus must be a JSON object")
            for key, value in raw.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                ids = _as_ids(value)
                if ids:
                    snapshot[key.strip()] = ids[-_MAX_TASKS_PER_SESSION:]
            while len(snapshot) > _MAX_SESSIONS:
                snapshot.popitem(last=False)
        except ValueError as exc:
            # A corrupt snapshot is set aside, never saved over: that would
            # drop every other chat's plan, and raising would keep the CLI
            # from showing any plan at all. Under the update lock, and only if
            # the file is still the one read: another process may have
            # written a valid one meanwhile, which must not be moved aside.
            with _update_lock():
                if _signature(path) != signature:
                    _loaded = False
                    if _reread:
                        _ensure_loaded(_reread=False)
                        return
                    raise OSError(f"session focus {path} keeps changing while it is read") from exc
                kept = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
                os.replace(path, kept)
                logger.error("Corrupt session focus {} moved to {}: {}", path, kept, exc)
            snapshot = OrderedDict()
            signature = None
        except OSError as exc:
            logger.error("Failed to read session focus from {}: {}", path, exc)
            raise
    _focus.clear()
    _focus.update(snapshot)
    _loaded_signature = signature
    _loaded = True


def _persist_unlocked() -> None:
    global _loaded_signature
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: list(ids) for key, ids in _focus.items()}
    # A unique temporary file prevents two CLI processes trampling each
    # other's write, even when they replace the same session in one second.
    handle, name = tempfile.mkstemp(prefix="session-focus-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        _replace_retrying(name, path)
        _loaded_signature = _signature(path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _replace_retrying(source: str, target: Path) -> None:
    """``os.replace`` that waits out a reader holding ``target`` open on Windows."""
    for attempt in range(5):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * (attempt + 1))


# One lock object per file: it is reentrant, so the reload under ``remember``
# can take it again. Two objects on one file in one process block each other.
_file_locks: dict[str, FileLock] = {}


def _update_lock() -> FileLock:
    """Serialize reload-modify-save across CLI and gateway processes."""
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    name = str(path) + ".lock"
    lock = _file_locks.get(name)
    if lock is None:
        lock = _file_locks[name] = FileLock(name, timeout=10)
    return lock


def remember(session_key: str | None, task_ids: object) -> None:
    """Record that ``session_key`` touched these tasks, keeping first-touch order.

    Order matters: the panel reads top to bottom like the plan it stands for, so
    a task that comes back for a second update keeps its original position
    instead of jumping to the end.
    """

    key = (session_key or "").strip()
    if not key:
        return
    wanted = [tid for tid in _as_ids(task_ids) if tid]
    if not wanted:
        return

    with _guard, _update_lock():
        _ensure_loaded()
        current = _focus.pop(key, [])
        for task_id in wanted:
            if task_id not in current:
                current.append(task_id)
        _focus[key] = current[-_MAX_TASKS_PER_SESSION:]
        while len(_focus) > _MAX_SESSIONS:
            _focus.popitem(last=False)
        _persist_unlocked()


def touched(session_key: str | None) -> list[str]:
    """Task ids this session touched, oldest first."""
    key = (session_key or "").strip()
    if not key:
        return []
    with _guard:
        _ensure_loaded()
        return list(_focus.get(key, ()))


def forget(session_key: str | None) -> None:
    """Drop one session's focus, e.g. when its chat is deleted."""
    key = (session_key or "").strip()
    if not key:
        return
    with _guard, _update_lock():
        _ensure_loaded()
        if key in _focus:
            _focus.pop(key, None)
            _persist_unlocked()


def clear() -> None:
    """Drop every session's focus. For tests."""
    with _guard, _update_lock():
        _ensure_loaded()
        _focus.clear()
        _persist_unlocked()


def _as_ids(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, (list, tuple, set)):
        return [item.strip() for item in value if isinstance(item, str)]
    return []


def replace(session_key: str | None, task_ids: object) -> None:
    """Start a new plan for this chat, discarding its previous plan's task ids."""
    key = (session_key or "").strip()
    if not key:
        return
    ids = list(dict.fromkeys(_as_ids(task_ids)))[-_MAX_TASKS_PER_SESSION:]
    with _guard, _update_lock():
        _ensure_loaded()
        _focus.pop(key, None)
        if ids:
            _focus[key] = ids
        _persist_unlocked()
