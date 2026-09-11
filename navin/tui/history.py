# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Visible chat turns for the TUI transcript (not the raw session window)."""

from __future__ import annotations

import json
from typing import Any

from navin.cognition.episodes import strip_runtime_context
from navin.runtime_context import public_history_message
from navin.session.history_visibility import is_hidden_history_message

# Safety cap when the caller asks for "everything". A huge render freezes WT.
DEFAULT_VISIBLE_CAP = 2000


def _message_text(content: Any) -> str:
    """Plain text of a chat message (string or OpenAI / Anthropic parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:  # noqa: BLE001
            return {"value": raw}
        if isinstance(parsed, dict):
            return parsed
    return {}


def tools_from_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Tool calls persisted on an assistant message (OpenAI or Anthropic shape)."""
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tc in message.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        name = str(fn.get("name") or tc.get("name") or "")
        call_id = str(tc.get("id") or name or f"tool:{len(tools)}")
        if call_id in seen:
            continue
        seen.add(call_id)
        tools.append(
            {
                "id": call_id,
                "name": name,
                "arguments": _parse_args(fn.get("arguments", tc.get("arguments"))),
                "result": None,
            }
        )
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_use":
                continue
            name = str(part.get("name") or "")
            call_id = str(part.get("id") or name or f"tool:{len(tools)}")
            if call_id in seen:
                continue
            seen.add(call_id)
            raw_input = part.get("input")
            tools.append(
                {
                    "id": call_id,
                    "name": name,
                    "arguments": raw_input if isinstance(raw_input, dict) else {},
                    "result": None,
                }
            )
    return tools


def _attach_tool_result(rows: list[dict[str, Any]], message: dict[str, Any]) -> None:
    if not rows or rows[-1].get("role") != "assistant":
        return
    call_id = str(message.get("tool_call_id") or "")
    text = _message_text(message.get("content"))
    if not call_id:
        return
    for tool in rows[-1].get("tools") or []:
        if tool.get("id") == call_id:
            tool["result"] = text
            return


def visible_chat_rows(
    messages: list[Any] | None,
    *,
    limit: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """User/assistant turns the TUI should paint, then the last ``limit`` of those.

    ``session.messages[-200:]`` is the wrong window: a tool-heavy tail eats the
    budget and older visible turns vanish. Walk everything, drop hidden rows,
    then slice the *visible* list.

    Returns ``(rows, older_count)``. ``older_count`` is how many visible turns
    sit before the returned window (0 when nothing was dropped).
    """
    rows: list[dict[str, Any]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if message.get("_command") or is_hidden_history_message(message):
            continue
        if message.get("injected_event"):
            continue
        role = message.get("role")
        if role == "tool":
            _attach_tool_result(rows, message)
            continue
        if role not in {"user", "assistant"}:
            continue
        shown = public_history_message(message)
        content = _message_text(shown.get("content"))
        if role == "user":
            content = strip_runtime_context(content)
        tools = tools_from_message(message) if role == "assistant" else []
        if not content.strip() and not tools:
            continue
        rows.append(
            {
                "role": role,
                "content": content.rstrip(),
                "metadata": message,
                "tools": tools,
            }
        )
    cap = limit if limit > 0 else DEFAULT_VISIBLE_CAP
    if len(rows) > cap:
        older = len(rows) - cap
        return rows[-cap:], older
    return rows, 0
