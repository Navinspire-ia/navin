# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Short-lived in-process cache for web_search / web_fetch.

Identical queries inside one agent turn (and across nearby turns) used to
hit the network twice. A small TTL cache, keyed by the normalised tool
arguments, collapses that without changing tool contracts: callers still
receive the same string / ToolResult shapes, and failures are never stored
so a transient 429 cannot poison the next attempt.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from loguru import logger

_TTL_S = 120.0
_MAX_ENTRIES = 256
_cache: dict[str, tuple[float, Any]] = {}


def _key(kind: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{kind}:{digest}"


def get(kind: str, payload: dict[str, Any]) -> Any | None:
    """Return a cached success value, or None on miss / expiry."""
    key = _key(kind, payload)
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts >= _TTL_S:
        _cache.pop(key, None)
        return None
    logger.debug("web cache hit for {}", kind)
    return value


def put(kind: str, payload: dict[str, Any], value: Any) -> None:
    """Store a successful tool result. Evicts the oldest entries past the cap."""
    if _is_error(value):
        return
    if len(_cache) >= _MAX_ENTRIES:
        # Drop the oldest half; cheap and good enough for a turn-scoped cache.
        ordered = sorted(_cache.items(), key=lambda item: item[1][0])
        for stale_key, _ in ordered[: max(1, _MAX_ENTRIES // 2)]:
            _cache.pop(stale_key, None)
    _cache[_key(kind, payload)] = (time.monotonic(), value)


def clear() -> None:
    """Drop every entry. For tests."""
    _cache.clear()


def _is_error(value: Any) -> bool:
    if value is None:
        return True
    if getattr(value, "is_error", False):
        return True
    if isinstance(value, str) and value.startswith("Error:"):
        return True
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith("{") and '"error"' in stripped[:80]:
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return False
            return isinstance(parsed, dict) and "error" in parsed
    return False
