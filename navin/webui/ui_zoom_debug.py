# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Localhost debug route for the Navin desktop webview.

The route exposes ``GET``/``POST`` ``/api/debug/ui-zoom`` so an agent (or a
human with ``curl``) can drive CSS zoom and read layout/menu numbers from the
live ``WebKitGTK`` view without a token.

Commands are queued via query string because the gateway HTTP layer does not
have request bodies. Snapshot uploads from the frontend use the standard
``X-Navin-File-Body-*`` chunked base64 headers (see ``file_body_from_headers``
in :mod:`navin.webui.file_preview`).

The state is a tiny process-local store guarded by a lock. Each new command
bumps ``seq``; ``GET`` without command fields returns the current state and
does NOT bump ``seq``.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Mapping
from typing import Any

# Allowed values for ``action``. Anything else is rejected with HTTP 400.
ALLOWED_ACTIONS = frozenset({"in", "out", "reset", "open", "click", "escape", "dump", "outside"})

# Allowed values for ``target``. Same enforcement as ``action``.
ALLOWED_TARGETS = frozenset({"session", "effort", "model"})

# Zoom is clamped to a sensible WebKitGTK range. The bounds match what the
# frontend zoom controls allow; values outside the band are rejected.
_ZOOM_MIN = 0.25
_ZOOM_MAX = 5.0

QueryDict = Mapping[str, list[str]]


_store_lock = threading.Lock()
_state: dict[str, Any] = {
    "seq": 0,
    "command": None,
    "snapshot": None,
    "updatedAt": 0.0,
}


def _reset_for_tests() -> None:
    """Reset the module-level store. Intended for unit tests only."""
    with _store_lock:
        _state["seq"] = 0
        _state["command"] = None
        _state["snapshot"] = None
        _state["updatedAt"] = 0.0


def _snapshot_response() -> dict[str, Any]:
    return {
        "ok": True,
        "seq": _state["seq"],
        "command": _state["command"],
        "snapshot": _state["snapshot"],
        "updatedAt": _state["updatedAt"],
    }


def _set_command(command: dict[str, Any]) -> None:
    with _store_lock:
        _state["seq"] += 1
        _state["command"] = {
            "seq": _state["seq"],
            "zoom": command.get("zoom"),
            "action": command.get("action"),
            "target": command.get("target"),
            "selector": command.get("selector"),
            "hud": bool(command.get("hud")),
            "issuedAt": time.time(),
        }
        _state["updatedAt"] = time.time()


def _set_snapshot(snapshot: dict[str, Any]) -> None:
    with _store_lock:
        _state["snapshot"] = snapshot
        _state["updatedAt"] = time.time()


def _current_response() -> dict[str, Any]:
    with _store_lock:
        return _snapshot_response()


def _first(values: list[str] | None) -> str | None:
    if not values:
        return None
    return values[0]


def _coerce_zoom(raw: str | None) -> tuple[float | None, str | None]:
    if raw is None or raw == "":
        return None, None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"zoom must be a number, got {raw!r}"
    if not math.isfinite(value):
        return None, f"zoom must be finite, got {raw!r}"
    if value < _ZOOM_MIN or value > _ZOOM_MAX:
        return None, f"zoom must be between {_ZOOM_MIN} and {_ZOOM_MAX}, got {raw!r}"
    return value, None


def _coerce_hud(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _validate_optional_choice(
    raw: str | None,
    allowed: frozenset[str],
    field: str,
) -> tuple[str | None, str | None]:
    if raw is None or raw == "":
        return None, None
    if raw not in allowed:
        return None, f"{field} must be one of {sorted(allowed)}, got {raw!r}"
    return raw, None


def _build_command_from_query(query: QueryDict) -> tuple[dict[str, Any] | None, str | None]:
    """Translate the parsed query dict into a command payload.

    Returns ``(command, error)``. ``command`` is ``None`` when the request had
    no command fields at all (a plain read); ``error`` is set when any field
    was invalid.
    """
    zoom_raw = _first(list(query.get("zoom") or []))
    action_raw = _first(list(query.get("action") or []))
    target_raw = _first(list(query.get("target") or []))
    selector_raw = _first(list(query.get("selector") or []))
    hud_raw = _first(list(query.get("hud") or []))

    has_any = any(
        value is not None and value != ""
        for value in (zoom_raw, action_raw, target_raw, selector_raw, hud_raw)
    )
    if not has_any:
        return None, None

    command: dict[str, Any] = {}

    zoom, err = _coerce_zoom(zoom_raw)
    if err is not None:
        return None, err
    if zoom is not None:
        command["zoom"] = zoom

    action, err = _validate_optional_choice(action_raw, ALLOWED_ACTIONS, "action")
    if err is not None:
        return None, err
    if action is not None:
        command["action"] = action

    target, err = _validate_optional_choice(target_raw, ALLOWED_TARGETS, "target")
    if err is not None:
        return None, err
    if target is not None:
        command["target"] = target

    if selector_raw:
        command["selector"] = selector_raw

    command["hud"] = _coerce_hud(hud_raw)

    return command, None


def _build_command_from_body(body: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    has_any = any(
        key in body and body[key] not in (None, "")
        for key in ("zoom", "action", "target", "selector", "hud")
    )
    if not has_any:
        return None, None

    command: dict[str, Any] = {}

    if body.get("zoom") is not None:
        zoom, err = _coerce_zoom(str(body["zoom"]))
        if err is not None:
            return None, err
        command["zoom"] = zoom

    if body.get("action") is not None:
        action, err = _validate_optional_choice(
            str(body["action"]), ALLOWED_ACTIONS, "action"
        )
        if err is not None:
            return None, err
        command["action"] = action

    if body.get("target") is not None:
        target, err = _validate_optional_choice(
            str(body["target"]), ALLOWED_TARGETS, "target"
        )
        if err is not None:
            return None, err
        command["target"] = target

    selector = body.get("selector")
    if selector:
        command["selector"] = str(selector)

    if "hud" in body:
        command["hud"] = bool(body["hud"])

    return command, None


def _coerce_snapshot(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    return None


def handle_ui_zoom_debug(request: Any) -> Any:
    """Dispatch a ``/api/debug/ui-zoom`` request.

    Returns a ``Response`` built with :mod:`navin.webui.http_utils`. The
    helper imports are deferred so test harnesses can import this module
    without dragging the heavy ``navin.utils`` import chain.
    """
    # Deferred imports keep ``import navin.webui.ui_zoom_debug`` cheap for
    # unit tests that only touch the in-memory store.
    from navin.webui.file_preview import WebUIFilePreviewError, file_body_from_headers
    from navin.webui.http_utils import (
        http_error,
        http_json_response,
        parse_query,
    )

    # The websockets handshake hook rejects POST before routing, so
    # snapshot uploads travel as GET plus X-Navin-File-Body-* headers,
    # same as file-save. Always try the headers; missing ones are normal.
    path = getattr(request, "path", "") or ""
    query = parse_query(path)

    body: dict[str, Any] = {}
    headers = getattr(request, "headers", None)
    raw_body = ""
    if headers is not None:
        try:
            raw_body = file_body_from_headers(headers) or ""
        except WebUIFilePreviewError:
            raw_body = ""
    if raw_body.strip():
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            return http_error(400, "invalid JSON body")
        if not isinstance(parsed, dict):
            return http_error(400, "invalid JSON body")
        body = parsed
    snapshot = _coerce_snapshot(body.get("snapshot")) if body else None
    command, err = _build_command_from_query(query)
    if err is not None:
        return http_error(400, err)
    if command is None:
        command, err = _build_command_from_body(body)
        if err is not None:
            return http_error(400, err)

    if snapshot is not None:
        _set_snapshot(snapshot)
    if command is not None:
        _set_command(command)

    return http_json_response(_current_response())


__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_TARGETS",
    "handle_ui_zoom_debug",
    "_reset_for_tests",
    "_build_command_from_query",
    "_build_command_from_body",
]
