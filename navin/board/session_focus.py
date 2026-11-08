# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Which board tasks the current run is working on, and the plans of each chat.

The board is project-scoped and long-lived: after a few sessions it holds work
from conversations nobody is watching any more. A plan panel that showed all of
it would be a second kanban rather than "what is happening right now", so the
chat needs the narrower question - of everything on the board, which tasks did
*this* run touch, and in what order.

Focus is kept out of ``.navin/board/`` so session identifiers do not travel with
the repository. It is persisted under the WebUI runtime dir
(``~/.navin/webui/session-focus.json``) so a gateway restart mid-plan can still
rebuild the chat checklist from the same task ids.

The focus is the chat's *current* plan, in the format every Navin version reads
and writes. What older versions do not know lives in a sibling file they never
open (``session-plans.json``): which user request filed the current plan,
whether the user hid it, and the earlier plans it replaced. A new request that
files work gets its own plan instead of growing the previous one; the previous
plan stays listed in "My plans", where a relaunch brings it back.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from filelock import FileLock
from loguru import logger

# A plan longer than this is not a plan the reader can hold, and the panel would
# scroll past the answer. The board itself keeps everything.
_MAX_TASKS_PER_SESSION = 40
# Enough for every conversation a user has open at once; the oldest is evicted
# rather than letting a long-lived gateway grow without bound.
_MAX_SESSIONS = 64
# Earlier plans kept per chat, oldest first. "My plans" lists them.
_MAX_EARLIER_PLANS = 12

# The user request a turn serves. Set on the user's own message and carried by
# its continuation slices and replays: the board tells the next slice of the
# request that filed the current plan from a new request with its own plan.
# No leading underscore, so a replay after a restart keeps it.
PLAN_REQUEST_META = "plan_request_id"

_focus: OrderedDict[str, list[str]] = OrderedDict()
_guard = threading.Lock()
_loaded = False
# Multi-process CLI and gateway share the same file; the file's signature
# detects a different process replacing a session's plan.
_loaded_signature: tuple[int, int, int, int] | None = None

# Per chat: {"ids": the current plan as last written here, "owner": request id,
# "hidden": the user hid it, "earlier": [{"ids": [...], "at": epoch}]}.
_plans: OrderedDict[str, dict[str, Any]] = OrderedDict()
_plans_loaded = False
_plans_signature: tuple[int, int, int, int] | None = None


def _store_path() -> Path:
    from navin.config.paths import get_webui_dir

    return get_webui_dir() / "session-focus.json"


def _plans_path() -> Path:
    return _store_path().with_name("session-plans.json")


def _signature(path: Path) -> tuple[int, int, int, int] | None:
    """Identity and version of the on-disk snapshot, even across atomic writes."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size


def _set_aside(path: Path, signature: tuple[int, int, int, int] | None, exc: Exception) -> bool:
    """Move a corrupt snapshot away; False when another process rewrote it meanwhile.

    A corrupt snapshot is set aside, never saved over: that would drop every
    other chat's data. Under the update lock, and only if the file is still
    the one read: a valid one written meanwhile must not be moved aside.
    """
    with _update_lock():
        if _signature(path) != signature:
            return False
        kept = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
        os.replace(path, kept)
        logger.error("Corrupt {} moved to {}: {}", path.name, kept, exc)
        return True


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
            # Raising would keep the CLI from showing any plan at all.
            if not _set_aside(path, signature, exc):
                _loaded = False
                if _reread:
                    _ensure_loaded(_reread=False)
                    return
                raise OSError(f"session focus {path} keeps changing while it is read") from exc
            snapshot = OrderedDict()
            signature = None
        except OSError as exc:
            logger.error("Failed to read session focus from {}: {}", path, exc)
            raise
    _focus.clear()
    _focus.update(snapshot)
    _loaded_signature = signature
    _loaded = True


def _ensure_plans_loaded(*, _reread: bool = True) -> None:
    global _plans_loaded, _plans_signature
    path = _plans_path()
    signature = _signature(path)
    if _plans_loaded and signature == _plans_signature:
        return
    snapshot: OrderedDict[str, dict[str, Any]] = OrderedDict()
    if signature is not None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("session plans must be a JSON object")
            for key, value in raw.items():
                entry = _as_plan_entry(value)
                if isinstance(key, str) and key.strip() and entry is not None:
                    snapshot[key.strip()] = entry
            while len(snapshot) > _MAX_SESSIONS:
                snapshot.popitem(last=False)
        except ValueError as exc:
            if not _set_aside(path, signature, exc):
                _plans_loaded = False
                if _reread:
                    _ensure_plans_loaded(_reread=False)
                    return
                raise OSError(f"session plans {path} keep changing while they are read") from exc
            snapshot = OrderedDict()
            signature = None
        except OSError as exc:
            logger.error("Failed to read session plans from {}: {}", path, exc)
            raise
    _plans.clear()
    _plans.update(snapshot)
    _plans_signature = signature
    _plans_loaded = True


def _write_json(path: Path, payload: Any) -> tuple[int, int, int, int] | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique temporary file prevents two CLI processes trampling each
    # other's write, even when they replace the same session in one second.
    handle, name = tempfile.mkstemp(prefix=f"{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        _replace_retrying(name, path)
        return _signature(path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _persist_unlocked() -> None:
    global _loaded_signature
    _loaded_signature = _write_json(_store_path(), {key: list(ids) for key, ids in _focus.items()})


def _persist_plans_unlocked() -> None:
    global _plans_signature
    _plans_signature = _write_json(_plans_path(), {key: entry for key, entry in _plans.items()})


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
    """Serialize reload-modify-save across CLI and gateway processes (both files)."""
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    name = str(path) + ".lock"
    lock = _file_locks.get(name)
    if lock is None:
        lock = _file_locks[name] = FileLock(name, timeout=10)
    return lock


def _evict_unlocked() -> None:
    while len(_focus) > _MAX_SESSIONS:
        _focus.popitem(last=False)
    while len(_plans) > _MAX_SESSIONS:
        _plans.popitem(last=False)


def remember(session_key: str | None, task_ids: object, *, owner: str | None = None) -> None:
    """Record that ``session_key`` touched these tasks, keeping first-touch order.

    Order matters: the panel reads top to bottom like the plan it stands for, so
    a task that comes back for a second update keeps its original position
    instead of jumping to the end. ``owner`` makes that request the one the
    current plan belongs to (it files or works on it).
    """

    key = (session_key or "").strip()
    if not key:
        return
    wanted = [tid for tid in _as_ids(task_ids) if tid]
    if not wanted:
        return

    with _guard, _update_lock():
        _ensure_loaded()
        _ensure_plans_loaded()
        current = _focus.pop(key, [])
        before = list(current)
        for task_id in wanted:
            if task_id not in current:
                current.append(task_id)
        _focus[key] = current[-_MAX_TASKS_PER_SESSION:]
        _evict_unlocked()
        _persist_unlocked()
        entry = _plans.get(key)
        tracked = entry is not None and _describes_focus(entry, before)
        if owner is not None or (tracked and _focus.get(key) != before):
            _set_current_unlocked(key, owner=owner, keep_view=tracked)
            _persist_plans_unlocked()


def touched(session_key: str | None) -> list[str]:
    """Task ids this session touched, oldest first."""
    key = (session_key or "").strip()
    if not key:
        return []
    with _guard:
        _ensure_loaded()
        return list(_focus.get(key, ()))


def forget(session_key: str | None) -> None:
    """Drop one session's focus and plans, e.g. when its chat is deleted."""
    key = (session_key or "").strip()
    if not key:
        return
    with _guard, _update_lock():
        _ensure_loaded()
        _ensure_plans_loaded()
        if key in _focus:
            _focus.pop(key, None)
            _persist_unlocked()
        if key in _plans:
            _plans.pop(key, None)
            _persist_plans_unlocked()


def clear() -> None:
    """Drop every session's focus and plans. For tests."""
    with _guard, _update_lock():
        _ensure_loaded()
        _ensure_plans_loaded()
        _focus.clear()
        _plans.clear()
        _persist_unlocked()
        _persist_plans_unlocked()


def _as_ids(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, (list, tuple, set)):
        return [item.strip() for item in value if isinstance(item, str)]
    return []


def _unique_ids(value: object) -> list[str]:
    return [tid for tid in dict.fromkeys(_as_ids(value)) if tid]


def replace(session_key: str | None, task_ids: object, *, owner: str | None = None) -> None:
    """Start a new plan for this chat; the previous one joins its earlier plans."""
    start_plan(session_key, task_ids, owner=owner)


# -- Plans of a chat ------------------------------------------------------------


def _as_plan_entry(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    earlier: list[dict[str, Any]] = []
    raw_earlier = value.get("earlier")
    for plan in raw_earlier if isinstance(raw_earlier, list) else []:
        if not isinstance(plan, dict):
            continue
        ids = _unique_ids(plan.get("ids"))
        if not ids:
            continue
        at = plan.get("at")
        stamp = float(at) if isinstance(at, int | float) and not isinstance(at, bool) else 0.0
        earlier.append({"ids": ids[-_MAX_TASKS_PER_SESSION:], "at": stamp, "owner": _owner(plan.get("owner"))})
    entry = {
        "ids": _unique_ids(value.get("ids"))[-_MAX_TASKS_PER_SESSION:],
        "owner": _owner(value.get("owner")),
        "hidden": value.get("hidden") is True,
        "earlier": earlier[-_MAX_EARLIER_PLANS:],
    }
    return entry if entry["ids"] or entry["earlier"] else None


def _owner(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _describes_focus(entry: Mapping[str, Any], focus: list[str]) -> bool:
    """Whether ``entry``'s current part is about this focus.

    A version that does not know this file can replace the focus with a new
    plan; the owner and the hidden flag written for the old plan must not stick
    to the new one. A plan shares at least one task with what was last written.
    """
    return bool(focus) and bool(set(entry.get("ids") or ()) & set(focus))


def _entry_unlocked(key: str) -> dict[str, Any]:
    entry = _plans.pop(key, None) or {"ids": [], "owner": None, "hidden": False, "earlier": []}
    _plans[key] = entry
    return entry


def _set_current_unlocked(
    key: str, *, owner: str | None = None, keep_view: bool = True, hidden: bool | None = None,
) -> None:
    """Describe the current focus: its owner and whether the user hid it."""
    entry = _entry_unlocked(key)
    focus = list(_focus.get(key) or [])
    if not keep_view or not _describes_focus(entry, focus):
        entry["owner"] = None
        entry["hidden"] = False
    entry["ids"] = focus
    if owner is not None:
        entry["owner"] = owner
    if hidden is not None:
        entry["hidden"] = hidden
    if not entry["ids"] and not entry["earlier"]:
        _plans.pop(key, None)
    _evict_unlocked()


def _archive_unlocked(key: str, ids: list[str], previous: list[str]) -> None:
    """Keep a replaced plan in the chat's earlier plans (newest last), with its request."""
    if not ids:
        return
    entry = _entry_unlocked(key)
    owner = entry.get("owner") if _describes_focus(entry, previous) else None
    replaced = set(ids)
    # The same tasks archived again (a plan brought back, then replaced) are
    # one plan, not two entries.
    earlier = [plan for plan in entry["earlier"] if not replaced & set(plan["ids"])]
    earlier.append({"ids": list(ids)[-_MAX_TASKS_PER_SESSION:], "at": time.time(), "owner": owner})
    entry["earlier"] = earlier[-_MAX_EARLIER_PLANS:]


def start_plan(
    session_key: str | None,
    task_ids: object,
    *,
    owner: str | None = None,
    keep: object = (),
) -> None:
    """Make ``task_ids`` the chat's current plan; the previous one is kept as an earlier plan.

    ``keep`` are ids of the current plan that stay in place: they belong to the
    plan another project runs under the same session key (``cli:direct``).
    """
    key = (session_key or "").strip()
    if not key:
        return
    ids = _unique_ids(task_ids)
    kept = [tid for tid in _unique_ids(keep) if tid not in ids]
    with _guard, _update_lock():
        _ensure_loaded()
        _ensure_plans_loaded()
        previous = _focus.pop(key, [])
        _archive_unlocked(key, [tid for tid in previous if tid not in kept and tid not in ids], previous)
        focus = (kept + ids)[-_MAX_TASKS_PER_SESSION:]
        if focus:
            _focus[key] = focus
        _evict_unlocked()
        _persist_unlocked()
        _set_current_unlocked(key, owner=owner, keep_view=False)
        _persist_plans_unlocked()


def resume_earlier_plan(
    session_key: str | None,
    task_ids: object,
    *,
    owner: str | None = None,
    keep: object = (),
) -> bool:
    """Bring back the newest earlier plan holding one of ``task_ids``; False when none does.

    A relaunch from "My plans" asks the agent to continue a task of an earlier
    plan: that plan becomes the current one again, the current one is kept as
    an earlier plan, and the ids not in the earlier plan are added to it.
    """
    key = (session_key or "").strip()
    wanted = _unique_ids(task_ids)
    if not key or not wanted:
        return False
    kept = [tid for tid in _unique_ids(keep) if tid not in wanted]
    with _guard, _update_lock():
        _ensure_loaded()
        _ensure_plans_loaded()
        entry = _plans.get(key)
        earlier = list(entry["earlier"]) if entry else []
        index = next(
            (i for i in range(len(earlier) - 1, -1, -1) if set(earlier[i]["ids"]) & set(wanted)),
            None,
        )
        if entry is None or index is None:
            return False
        plan = earlier.pop(index)
        entry["earlier"] = earlier
        ids = list(plan["ids"]) + [tid for tid in wanted if tid not in plan["ids"]]
        kept = [tid for tid in kept if tid not in ids]
        previous = _focus.pop(key, [])
        _archive_unlocked(key, [tid for tid in previous if tid not in kept and tid not in ids], previous)
        _focus[key] = (kept + ids)[-_MAX_TASKS_PER_SESSION:]
        _evict_unlocked()
        _persist_unlocked()
        _set_current_unlocked(key, owner=owner, keep_view=False)
        _persist_plans_unlocked()
        return True


def plan_owner(session_key: str | None) -> str | None:
    """The request the chat's current plan belongs to; None when unknown."""
    key = (session_key or "").strip()
    if not key:
        return None
    with _guard:
        _ensure_loaded()
        _ensure_plans_loaded()
        entry = _plans.get(key)
        if entry is None or not _describes_focus(entry, _focus.get(key) or []):
            return None
        return entry.get("owner")


def plan_hidden(session_key: str | None) -> bool:
    """True when the user hid the chat's current plan."""
    key = (session_key or "").strip()
    if not key:
        return False
    with _guard:
        _ensure_loaded()
        _ensure_plans_loaded()
        entry = _plans.get(key)
        return bool(
            entry is not None
            and entry.get("hidden")
            and _describes_focus(entry, _focus.get(key) or [])
        )


def set_plan_hidden(session_key: str | None, hidden: bool, *, plan_task: str | None = None) -> bool:
    """Hide or show the chat's current plan; False when it has none.

    Hiding is a view choice: the tasks, their statuses and the run that works
    on them are untouched. ``plan_task`` is a task of the plan the user looks
    at; when the chat has moved on to another plan meanwhile, nothing changes.
    The next plan of the chat shows again.
    """
    key = (session_key or "").strip()
    if not key:
        return False
    with _guard, _update_lock():
        _ensure_loaded()
        _ensure_plans_loaded()
        focus = _focus.get(key) or []
        if not focus or (plan_task is not None and plan_task not in focus):
            return False
        _set_current_unlocked(key, hidden=hidden)
        _persist_plans_unlocked()
        return True


def earlier_plans(session_key: str | None) -> list[list[str]]:
    """The chat's earlier plans, newest first, as task id lists."""
    key = (session_key or "").strip()
    if not key:
        return []
    with _guard:
        _ensure_plans_loaded()
        entry = _plans.get(key)
        return [list(plan["ids"]) for plan in reversed(entry["earlier"])] if entry else []


def earlier_task_ids(session_key: str | None) -> set[str]:
    """Every task id of the chat's earlier plans."""
    return {tid for ids in earlier_plans(session_key) for tid in ids}


def remember_earlier(session_key: str | None, owner: str | None, task_ids: object) -> bool:
    """Add tasks to the earlier plan ``owner`` filed; False when it filed none.

    An older request can still be at work after a newer one filed its own plan
    (a late slice, its subagent): its tasks go to its own plan, and the chat
    keeps showing the newest one instead of switching back and forth.
    """
    key = (session_key or "").strip()
    wanted = _unique_ids(task_ids)
    if not key or not owner or not wanted:
        return False
    with _guard, _update_lock():
        _ensure_loaded()
        _ensure_plans_loaded()
        entry = _plans.get(key)
        plan = next(
            (plan for plan in reversed(entry["earlier"]) if plan.get("owner") == owner),
            None,
        ) if entry else None
        if plan is None:
            return False
        current = set(_focus.get(key) or ())
        added = [tid for tid in wanted if tid not in plan["ids"] and tid not in current]
        if added:
            plan["ids"] = (plan["ids"] + added)[-_MAX_TASKS_PER_SESSION:]
            _persist_plans_unlocked()
        return True


def plan_request(metadata: Mapping[str, Any] | None) -> str | None:
    """The user request id a turn carries, or None (background turn, older version)."""
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get(PLAN_REQUEST_META)
    return value.strip() if isinstance(value, str) and value.strip() else None


def begin_plan_request(metadata: MutableMapping[str, Any]) -> str:
    """Give the user's own message the request id its whole run carries."""
    request = uuid.uuid4().hex
    metadata[PLAN_REQUEST_META] = request
    return request
