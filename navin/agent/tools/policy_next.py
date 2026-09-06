"""``policy_next``: what this project's successful eval runs did next.

Registered only while the project's ``.navin/policy.json`` says ``steer``
**and** every gate is open (adapter N+1 beat N with no suite down, offline
A/B ``gain``); see ``navin.policy``. Read-only and advisory: it names the
action successful runs took in a state like this one and the actions only
failed runs took. It never runs, blocks or replaces a call, and a write,
delete, mail or payment keeps its usual approval.

It also owns the per-turn runtime-context block: one suggestion for the
request as a whole, before the agent picks its first tool. With ``steer``
off the tool is absent and the prompt is exactly what it was.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.context import current_request_context, is_heartbeat_turn
from navin.agent.tools.schema import StringSchema, tool_parameters_schema

_PARAMETERS = tool_parameters_schema(
    goal=StringSchema(
        "The request you are working on, in a few words (used to derive its intent; not stored).",
        max_length=2000,
    ),
    last_tools=StringSchema(
        "The tools you already called this turn with their outcome, oldest first, "
        "as 'tool:class' separated by commas (e.g. 'read_file:ok, edit_file:changed').",
        max_length=400,
    ),
    required=["goal"],
)


@tool_parameters(_PARAMETERS)
class PolicyNextTool(Tool):
    """Ask which action this project's successful runs took next in a state like this."""

    _scopes = {"core"}

    def __init__(self, *, workspace: str | Path) -> None:
        self._workspace = Path(workspace).expanduser()

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        from navin.policy.steerer import steer_open

        return steer_open(getattr(ctx, "workspace", None))

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace)

    @property
    def name(self) -> str:
        return "policy_next"

    @property
    def description(self) -> str:
        return (
            "Ask which tool this project's successful eval runs called next in a situation like "
            "yours (same kind of request, same last calls), with a confidence, and which actions "
            "only failed runs took. Advisory only: it never runs anything, and a write, delete, "
            "mail or payment call still needs its usual approval."
        )

    @property
    def read_only(self) -> bool:
        return True

    def runtime_context_provider(self):  # type: ignore[no-untyped-def]
        async def provide(request: Any):  # type: ignore[no-untyped-def]
            from navin.policy.steerer import steer_block_text
            from navin.runtime_context import RuntimeContextBlock

            workspace = getattr(request, "workspace", None) or self._workspace
            if (getattr(request, "session_key", "") or "").strip().lower() == "heartbeat":
                return None
            user_text = getattr(request, "original_user_text", None)
            text = await asyncio.to_thread(steer_block_text, workspace, user_text=user_text)
            return RuntimeContextBlock(source="policy", content=text) if text else None

        return provide

    def _project(self) -> Path:
        ctx = current_request_context()
        if ctx is not None and ctx.workspace is not None:
            return Path(ctx.workspace)
        return self._workspace

    async def execute(self, goal: str, last_tools: str | None = None, **kwargs: Any) -> Any:
        if is_heartbeat_turn():
            return ToolResult.error("policy_next is not available on the heartbeat.")
        if not (goal or "").strip():
            return ToolResult.error("Error: goal is required.")
        from navin.policy.steerer import steer_open

        project = self._project()
        if not steer_open(project):
            return "policy_next is not active for this project (steer off or gate closed). Decide by yourself."
        return await asyncio.to_thread(self._suggest, project, goal, last_tools or "")

    @staticmethod
    def _suggest(project: Path, goal: str, last_tools: str) -> str:
        from navin.policy.steerer import suggest_next
        from navin.policy.trajectory import intent_of
        from navin.world_model.trajectory import HISTORY

        prev = tuple(part.strip() for part in last_tools.split(",") if part.strip())[-HISTORY:]
        intent = intent_of(goal)
        suggestion = suggest_next(project, intent=intent, prev=prev)
        if suggestion is None:
            return f"No confident pattern for this state ({intent}; after {' > '.join(prev) or 'nothing yet'}). Decide by yourself."
        return "Successful runs of this project: " + suggestion.text + " Advisory only; you decide, approvals unchanged."
