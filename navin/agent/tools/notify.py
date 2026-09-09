# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Notify tool: how an agent asks a human to look up from what they are doing.

Everything an agent says already reaches the user through the transcript. This
is for the case the transcript cannot cover: the user is not reading it. They
switched to another chat, another module, another window, and work is now
blocked on a decision only they can make.

That makes restraint the whole design. A notification the user did not need
costs more than the one it was supposed to help with, because it teaches them to
ignore the next one. The tool description says so plainly, and the tool itself
refuses the two shapes of noise that are easy to detect: an empty title and a
level that overstates what happened.
"""

from __future__ import annotations

from typing import Any

from navin.agent.tools.base import Tool, ToolResult
from navin.bus.notify import LEVELS, publish_notification

_MAX_TITLE = 120
_MAX_DETAIL = 500


class NotifyTool(Tool):
    """Raise a notification in the user's WebUI notification centre."""

    _scopes = {"core", "subagent"}

    def __init__(self, bus: Any = None) -> None:
        self._bus = bus

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(bus=ctx.bus)

    @property
    def name(self) -> str:
        return "notify"

    @property
    def description(self) -> str:
        return (
            "Ask for the user's attention when they are not watching this chat. "
            "The notification appears under the bell in their app chrome, from "
            "anywhere in the product. "
            "Use it when work is blocked on them and progress has stopped: a "
            "decision only they can make, a credential you cannot obtain, a "
            "destructive step awaiting approval, or a long job that has finished "
            "and they asked to be told. "
            "Do NOT use it to report progress, to announce that a step "
            "succeeded, to summarise what you did, or to repeat something you "
            "already wrote in your reply - that is what the reply is for, and "
            "notifications the user did not need make them ignore the ones they "
            "do. If in doubt, do not send one. "
            "Say what you need in the title; keep it to one sentence a person "
            "can act on."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "One actionable sentence, e.g. 'Deploy needs your "
                        "approval' or 'Missing STRIPE_API_KEY to continue'"
                    ),
                },
                "detail": {
                    "type": "string",
                    "description": "Optional context: what you tried, what you need next",
                },
                "level": {
                    "type": "string",
                    "enum": list(LEVELS),
                    "description": (
                        "'warning' when you are blocked, 'error' when something "
                        "failed and needs a human, 'success' only for a finished "
                        "job they asked to be told about, 'info' otherwise"
                    ),
                },
                "key": {
                    "type": "string",
                    "description": (
                        "Optional identity for a recurring condition. Reusing a "
                        "key folds repeats into one entry with a counter instead "
                        "of stacking them."
                    ),
                },
            },
            "required": ["title"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        title = str(kwargs.get("title") or "").strip()
        if not title:
            return ToolResult.error("title is required")
        if len(title) > _MAX_TITLE:
            title = f"{title[: _MAX_TITLE - 1].rstrip()}…"

        detail = str(kwargs.get("detail") or "").strip()
        if len(detail) > _MAX_DETAIL:
            detail = f"{detail[: _MAX_DETAIL - 1].rstrip()}…"

        level = str(kwargs.get("level") or "info").strip().lower()
        if level not in LEVELS:
            return ToolResult.error(f"level must be one of: {', '.join(LEVELS)}")

        delivered = publish_notification(
            self._bus,
            title=title,
            detail=detail or None,
            level=level,
            source="agent",
            key=str(kwargs.get("key") or "").strip() or None,
        )
        if not delivered:
            # Worth saying: the agent may be waiting on a human who will never
            # be told, and should fall back to asking in its reply.
            return ToolResult.error(
                "notification could not be delivered; ask in your reply instead"
            )
        return ToolResult(f"Notified the user: {title}")
