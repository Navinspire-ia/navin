# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI used by the Vite Tools page when the live gateway catalog is stale."""

from __future__ import annotations

import json
import sys
from typing import Any

from navin.webui.mcp_presets_api import (
    McpPresetError,
    custom_mcp_action,
    mcp_presets_action,
    mcp_presets_payload,
)

_QUERY_ACTIONS = frozenset({"enable", "remove", "tools"})
_CUSTOM_ACTIONS = frozenset({"custom", "import", "import-cursor"})


def _as_query(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    query: dict[str, list[str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(value, list):
            query[key] = [str(item) for item in value if str(item).strip()]
        elif value is None:
            continue
        else:
            text = str(value).strip()
            if text:
                query[key] = [text]
    return query


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "list").strip().lower()
    raw = sys.stdin.read()
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print(json.dumps({"error": "invalid MCP presets payload"}))
        return 1
    if not isinstance(body, dict):
        body = {}
    query = _as_query(body.get("query"))
    extra = _as_query(body.get("values"))
    for key, values in extra.items():
        query.setdefault(key, values)
    try:
        if action in {"", "list"}:
            payload = mcp_presets_payload()
        elif action in _CUSTOM_ACTIONS:
            payload = custom_mcp_action(action, query)
        elif action in _QUERY_ACTIONS:
            payload = mcp_presets_action(action, query)
        else:
            print(json.dumps({"error": f"unknown MCP action {action}", "status": 404}))
            return 2
    except McpPresetError as exc:
        print(json.dumps({"error": str(exc), "status": getattr(exc, "status", 400)}))
        return 2 if getattr(exc, "status", 400) < 500 else 1
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
