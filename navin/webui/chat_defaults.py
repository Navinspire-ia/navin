# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What a new chat starts with: the model and the team the user picked last.

Kept under the instance data dir (``~/.navin/webui/chat-defaults.json``)
rather than only in the page's localStorage. The desktop page origin carries
the local port, and each engine (WebView2, WKWebView, WebKitGTK) keeps its own
storage, so a new port, another window or a cleared webview used to start
every chat on the account default again. The account default itself
(``agents.defaults.model_preset``, ``collab.default_team``) is not touched:
writing it from a chat would switch the chats that are already running.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from navin.config.paths import get_webui_dir

_MAX_STATE_FILE_BYTES = 8 * 1024
_MAX_VALUE_CHARS = 80
_FIELDS = ("model_preset", "team")
# One writer at a time: the model and the team are often saved together.
_WRITE_LOCK = threading.Lock()


class ChatDefaultsError(ValueError):
    """A value the store refuses (not a string, too long)."""


def chat_defaults_path() -> Path:
    return get_webui_dir() / "chat-defaults.json"


def default_chat_defaults() -> dict[str, Any]:
    return {"model_preset": None, "team": None}


def _clean_value(field: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ChatDefaultsError(f"{field} must be a string or null")
    value = value.strip()
    if len(value) > _MAX_VALUE_CHARS:
        raise ChatDefaultsError(f"{field} is longer than {_MAX_VALUE_CHARS} characters")
    return value or None


def read_chat_defaults() -> dict[str, Any]:
    path = chat_defaults_path()
    if not path.exists():
        return default_chat_defaults()
    try:
        raw_bytes = path.read_bytes()
        if len(raw_bytes) > _MAX_STATE_FILE_BYTES:
            logger.warning("chat defaults file too large, using the account defaults: {}", path)
            return default_chat_defaults()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("chat defaults unreadable, using the account defaults {}: {}", path, exc)
        return default_chat_defaults()
    state = default_chat_defaults()
    if not isinstance(raw, dict):
        logger.warning("chat defaults file is not an object, using the account defaults: {}", path)
        return state
    for field in _FIELDS:
        try:
            state[field] = _clean_value(field, raw.get(field))
        except ChatDefaultsError as exc:
            logger.warning("chat defaults: dropping {} from {}: {}", field, path, exc)
    return state


def update_chat_defaults(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge *patch* into the stored defaults; a field set to null is cleared.

    Unknown fields are refused so a typo cannot silently do nothing.
    """
    unknown = sorted(set(patch) - set(_FIELDS))
    if unknown:
        raise ChatDefaultsError(f"unknown chat default: {', '.join(unknown)}")
    cleaned = {field: _clean_value(field, value) for field, value in patch.items()}
    with _WRITE_LOCK:
        state = read_chat_defaults()
        state.update(cleaned)
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        encoded = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        path = chat_defaults_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".chat-defaults.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
    return state


__all__ = [
    "ChatDefaultsError",
    "chat_defaults_path",
    "read_chat_defaults",
    "update_chat_defaults",
]
