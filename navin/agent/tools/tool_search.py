# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Load deferred tool schemas into the conversation (see agent.tool_deferral)."""

from __future__ import annotations

from typing import Any

from navin.agent import tool_deferral
from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import current_request_context


class ToolSearchTool(Tool):
    """Make deferred tools part of the schemas sent to the model."""

    _scopes = {"core"}

    @property
    def name(self) -> str:
        return tool_deferral.TOOL_SEARCH_NAME

    @property
    def description(self) -> str:
        # Replaced per request with the list of loadable tools.
        return (
            "Load tools that are not in your tool list yet, by exact name "
            "('select:name1,name2') or by keywords."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "'select:board,browser' to load exact names, or keywords "
                        "such as 'schedule reminder' to find matching tools."
                    ),
                    "minLength": 1,
                },
            },
            "required": ["query"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        ctx = current_request_context()
        session_key = ctx.session_key if ctx is not None else None
        found, missing = tool_deferral.search(session_key, str(kwargs.get("query") or ""))
        if not found:
            hint = f" Unknown: {', '.join(missing)}." if missing else ""
            return ToolResult.error(
                "Error: no loadable tool matched." + hint
                + " Use an exact name from the tool_search description."
            )
        tool_deferral.mark_loaded(session_key, found)
        available = tool_deferral.catalog(session_key)
        lines = []
        for name in found:
            schema = available.get(name) or {}
            fn = schema.get("function") if isinstance(schema.get("function"), dict) else schema
            params = (fn or {}).get("parameters") or {}
            props = ", ".join(sorted((params.get("properties") or {}).keys()))
            lines.append(f"- {name}({props})")
        note = f" Not available this turn: {', '.join(missing)}." if missing else ""
        return (
            "Loaded; full schemas are in your tool list from the next step:\n"
            + "\n".join(lines)
            + note
        )
