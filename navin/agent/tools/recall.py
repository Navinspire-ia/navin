# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""``recall``: search this project's episodic journal and MEMORY.md.

Registered only when the project opted in through ``.navin/cognition.json``
(see ``navin.cognition``). Read-only, bounded, and silent on the heartbeat.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.context import current_request_context, is_heartbeat_turn
from navin.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from navin.cognition.episodes import format_episode, search_episodes, tokenize
from navin.cognition.notes import search_memory_notes
from navin.cognition.settings import cognition_enabled

DEFAULT_LIMIT = 5
MAX_LIMIT = 10
MAX_NOTES = 3

_RECALL_PARAMETERS = tool_parameters_schema(
    query=StringSchema(
        "Words to look for in past turns: a request, a file, a tool, a buyer, "
        "a decision, an error message. Plain keywords work best.",
        min_length=2,
        max_length=400,
    ),
    limit=IntegerSchema(
        DEFAULT_LIMIT,
        description=f"Max past turns to return (1-{MAX_LIMIT}).",
        minimum=1,
        maximum=MAX_LIMIT,
    ),
    required=["query"],
)


@tool_parameters(_RECALL_PARAMETERS)
class RecallTool(Tool):
    """Look up what already happened in this project before redoing it."""

    _scopes = {"core"}

    def __init__(self, *, workspace: str | Path) -> None:
        self._workspace = Path(workspace).expanduser()

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return cognition_enabled(getattr(ctx, "workspace", None), "recall")

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace)

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search this project's past turns (episodic journal) and MEMORY.md for "
            "something Navin already handled: a request, a decision, a file, a tool "
            "result, an error. Use it when the user says 'like last time', 'again', "
            "'the one we did', or before redoing work. Read-only; returns the most "
            "relevant past turns with date, channel, request, answer and tools used."
        )

    @property
    def read_only(self) -> bool:
        return True

    def _project(self) -> Path:
        ctx = current_request_context()
        if ctx is not None and ctx.workspace is not None:
            return Path(ctx.workspace)
        return self._workspace

    async def execute(self, query: str, limit: int = DEFAULT_LIMIT, **kwargs: Any) -> Any:
        if is_heartbeat_turn():
            return ToolResult.error("recall is not available on the heartbeat.")
        text = (query or "").strip()
        if len(text) < 2:
            return ToolResult.error("Error: query must contain at least two characters.")
        project = self._project()
        if not cognition_enabled(project, "recall"):
            return ToolResult(
                "recall is off for this project. Enable it with "
                '{"enabled": true} in .navin/cognition.json.'
            )
        try:
            count = int(limit)
        except (TypeError, ValueError):
            count = DEFAULT_LIMIT
        count = max(1, min(MAX_LIMIT, count))
        hits, notes = await asyncio.to_thread(self._search, project, text, count)
        if not hits and not notes:
            return (
                f"No past turn or memory note matches '{text}'. The journal only "
                "covers turns since cognition was enabled for this project."
            )
        sections: list[str] = []
        if hits:
            sections.append(f"Past turns ({len(hits)}):")
            for index, (_score, record) in enumerate(hits, start=1):
                sections.append(f"[{index}] " + format_episode(record))
        if notes:
            sections.append("MEMORY.md:")
            for _score, snippet in notes:
                sections.append(f"- {snippet}")
        return "\n\n".join(sections)

    @staticmethod
    def _search(project: Path, query: str, limit: int) -> tuple[list, list]:
        hits = search_episodes(project, query, limit=limit)
        notes = search_memory_notes(project, tokenize(query), limit=MAX_NOTES)
        return hits, notes
