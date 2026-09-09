# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Switch the WebUI composer turn mode (Plan / Agent / Review / Security / Debug / Montage).

Investigate modes tint the composer shell like Plan does. Agents call this
when they autonomously enter a review, security audit, debug, or montage posture
so the user sees the matching mode and color without switching manually.
"""

from __future__ import annotations

from typing import Any

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import current_request_context
from navin.bus.outbound_events import (
    ComposerModeRequestedEvent,
    outbound_message_for_event,
)

_ALLOWED_MODES = frozenset(
    {"ask", "plan", "agent", "review", "security", "debug", "montage"}
)

_MODE_HELP = {
    "ask": "Ask - read-only Q&A: explore and explain, no edits",
    "plan": "Plan - design only, no code until Build/Agent",
    "agent": "Agent - full build: implement, test, iterate",
    "review": "Review - evidence-only code review + report + fix plan",
    "security": "Security - evidence-only audit + scanners + hardening plan",
    "debug": "Debug - reproduce with real evidence + root-cause report",
    "montage": "Montage - project marketing kit, calendar, images/videos, HyperFrames",
}


class SetComposerModeTool(Tool):
    """Ask the WebUI to switch composer turn mode and accent color."""

    _scopes = {"core", "subagent"}

    def __init__(self, bus: Any = None) -> None:
        self._bus = bus

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(bus=ctx.bus)

    @property
    def name(self) -> str:
        return "set_composer_mode"

    @property
    def description(self) -> str:
        return (
            "Switch the Navin composer turn mode in the editor UI "
            "(ask, plan, agent, review, security, debug, montage) and update its "
            "accent color. The editor already shows the mode a turn started in; "
            "call this only to CHANGE mode: when you autonomously enter review/"
            "security/debug/montage, or when the user asks for work the current "
            "mode does not allow (hand off Ask/Plan to Agent). The switch applies "
            "to this turn immediately: leaving Ask or Plan for Agent unlocks the "
            "build tools right away, switching into Plan or Ask tightens to "
            "read-only at once."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": sorted(_ALLOWED_MODES),
                    "description": (
                        "Composer mode to show: "
                        + "; ".join(
                            f"{name} = {_MODE_HELP[name]}"
                            for name in (
                                "ask",
                                "agent",
                                "plan",
                                "review",
                                "security",
                                "debug",
                                "montage",
                            )
                        )
                    ),
                },
            },
            "required": ["mode"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        mode = str(kwargs.get("mode") or "").strip().lower()
        if mode not in _ALLOWED_MODES:
            return ToolResult.error(
                "Error: mode must be one of: "
                + ", ".join(sorted(_ALLOWED_MODES))
                + "."
            )

        ctx = current_request_context()
        if ctx is None or ctx.channel != "websocket":
            return ToolResult.error(
                "Error: composer mode only exists in the Navin editor "
                "(WebUI/desktop)."
            )
        if self._bus is None:
            return ToolResult.error(
                "Error: no message bus available to reach the editor UI."
            )

        try:
            self._bus.outbound.put_nowait(
                outbound_message_for_event(
                    channel="websocket",
                    chat_id=ctx.chat_id,
                    event=ComposerModeRequestedEvent(mode=mode),
                )
            )
        except Exception as exc:
            return ToolResult.error(
                f"Error: could not reach the editor UI: {exc}"
            )

        return f"Composer mode set to {_MODE_HELP[mode]}."
