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
import threading
from collections import OrderedDict
from pathlib import Path

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


def _store_path() -> Path:
    from navin.config.paths import get_webui_dir

    return get_webui_dir() / "session-focus.json"


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    path = _store_path()
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if not isinstance(key, str) or not key.strip():
                        continue
                    ids = _as_ids(value)
                    if ids:
                        _focus[key.strip()] = ids[-_MAX_TASKS_PER_SESSION:]
                while len(_focus) > _MAX_SESSIONS:
                    _focus.popitem(last=False)
    except Exception:
        logger.exception("Failed to load session focus from {}", path)
    _loaded = True


def _persist_unlocked() -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: list(ids) for key, ids in _focus.items()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception:
        logger.exception("Failed to persist session focus to {}", path)


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

    with _guard:
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
    with _guard:
        _ensure_loaded()
        if key in _focus:
            _focus.pop(key, None)
            _persist_unlocked()


def clear() -> None:
    """Drop every session's focus. For tests."""
    with _guard:
        _ensure_loaded()
        _focus.clear()
        _persist_unlocked()


def _as_ids(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, (list, tuple, set)):
        return [item.strip() for item in value if isinstance(item, str)]
    return []
