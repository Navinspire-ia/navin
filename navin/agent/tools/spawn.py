# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Spawn tool for creating background subagents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.context import current_request_context
from navin.agent.tools.schema import (
    BooleanSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.security.workspace_access import current_workspace_scope

if TYPE_CHECKING:
    from navin.agent.subagent import SubagentManager


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "'start' (default) launches a subagent and needs 'task'. "
            "'results' lists how recently finished subagents ended, for when a "
            "result did not reach you.",
            enum=["start", "results"],
        ),
        task=StringSchema("The task for the subagent to complete. Required to start one."),
        label=StringSchema("Optional short label for the task (for display)"),
        agent=StringSchema(
            "Optional name of a project-defined subagent (listed under "
            "'Project Subagents' in your context, from .claude/agents, "
            ".navin/agents, .opencode/agents, ...). Its instructions are "
            "applied on top of the standard subagent prompt."
        ),
        isolate=BooleanSchema(
            description="Run the subagent in its own git checkout of this repository instead "
            "of the shared working tree. The checkout starts from the last "
            "commit (HEAD): uncommitted changes in the working tree are "
            "invisible there, so isolation is for work that starts from "
            "committed state, not for fixing something just written and not "
            "yet committed. Use it when several subagents may edit the same "
            "files, or when you want competing attempts at one problem to "
            "compare. Its edits then land outside the user's tree and have to "
            "be merged deliberately, so leave it off for research, for reading "
            "tasks, and whenever the user expects to see the change appear."
        ),
        scope_paths=StringSchema(
            "Optional comma-separated project-relative paths (files or "
            "directories) this subagent should focus on, e.g. "
            "'navin/agent, tests/test_runner.py'. The subagent may still read "
            "other files when a finding needs cross-checking, but its mission "
            "and report stay within this scope. Use one scoped subagent per "
            "independent chapter of a large audit/review/build to keep each "
            "context small."
        ),
        temperature=NumberSchema(
            description=(
                "Optional sampling temperature for the subagent "
                "(0.0 = deterministic, higher = more creative). "
                "Defaults to the provider's configured temperature."
            ),
            minimum=0.0,
            maximum=2.0,
        ),
        model_preset=StringSchema(
            "Optional name of a configured model preset to run this subagent "
            "on, instead of inheriting the parent's model. Lets one turn mix "
            "roles: e.g. the architect thinks on a deep model while "
            "implementer subagents run on a fast one. Unknown preset names "
            "are refused with the available options."
        ),
    )
)
class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(
        self,
        manager: "SubagentManager",
        resolve_model_preset: Any | None = None,
    ):
        self._manager = manager
        self._resolve_model_preset = resolve_model_preset

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            manager=ctx.subagent_manager,
            resolve_model_preset=getattr(ctx, "resolve_model_preset", None),
        )

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background, or review how "
            "finished ones ended. Several can run at once, so start independent "
            "tasks together rather than one at a time. Each reports back on its "
            "own when done. They share one working tree unless you set "
            "isolate=true, so give overlapping edits their own checkout. For "
            "deliverables or existing projects, inspect the workspace first and "
            "use a dedicated subdirectory when helpful. In Plan mode only "
            "action='results' is available: starting a subagent is deferred "
            "until the composer switches to Agent."
        )

    def call_read_only(self, arguments: Any) -> bool:
        """'results' only reports what already finished; 'start' launches work.

        A subagent runs on its own spec and does not inherit the parent's plan
        read-only policy, so starting one in Plan mode would let the turn mutate
        by proxy. Reading how earlier subagents ended has no such effect, and a
        planning turn needs it to fold their research into the plan.
        """
        if not isinstance(arguments, dict):
            return False
        return arguments.get("action") == "results"

    async def execute(
        self,
        task: str | None = None,
        label: str | None = None,
        agent: str | None = None,
        temperature: float | None = None,
        action: str = "start",
        isolate: bool = False,
        scope_paths: str | None = None,
        model_preset: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent, or report what became of finished ones."""
        request_ctx = current_request_context()
        if request_ctx is None or request_ctx.runtime is None:
            return ToolResult.error("Error: spawn requires an active model runtime")
        origin_channel = request_ctx.channel
        origin_chat_id = request_ctx.chat_id
        session_key = request_ctx.session_key or f"{origin_channel}:{origin_chat_id}"

        if action == "results":
            return self._render_outcomes(session_key)
        if not task or not task.strip():
            return ToolResult.error("Error: spawn needs a task to start a subagent")

        # The limit lives in the manager, which queues past it instead of
        # refusing. Two copies of the check meant two answers to the same
        # question, and this one turned a full conversation into a dead end.
        scopes = [
            p.strip()
            for p in (scope_paths or "").split(",")
            if p.strip()
        ]
        # P2-3: role-specialised subagents. A named preset resolves to its own
        # runtime; everything else about the spawn (policy, scope, context)
        # still inherits from the parent.
        runtime = request_ctx.runtime
        preset_name = (model_preset or "").strip()
        if preset_name:
            if self._resolve_model_preset is None:
                return ToolResult.error(
                    "Error: model_preset is not available in this runtime "
                    "(no preset resolver configured); spawn without it to "
                    "inherit the parent's model."
                )
            try:
                runtime = self._resolve_model_preset(preset_name)
            except (KeyError, ValueError) as exc:
                return ToolResult.error(
                    f"Error: model_preset {preset_name!r} could not be "
                    f"resolved: {exc}"
                )
        # The parent turn's metadata (open files, file mentions) seeds the
        # subagent's context pack, so it inherits the parent's focus paths.
        metadata = getattr(request_ctx, "metadata", None)
        return await self._manager.spawn(
            task=task,
            runtime=runtime,
            label=label,
            agent=agent,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            session_key=session_key,
            origin_message_id=request_ctx.message_id,
            temperature=temperature,
            workspace_scope=current_workspace_scope(),
            isolate=bool(isolate),
            scope_paths=scopes or None,
            parent_metadata=dict(metadata) if isinstance(metadata, dict) else None,
        )

    def _render_outcomes(self, session_key: str) -> str:
        """Summarise finished subagents for this conversation."""
        outcomes = self._manager.recent_outcomes(session_key=session_key)
        running = self._manager.get_running_count_by_session(session_key)
        if not outcomes:
            if running:
                return f"No subagent has finished yet; {running} still running."
            return "No subagent has run in this conversation."

        lines = [f"{len(outcomes)} finished subagent(s), most recent first:"]
        for outcome in outcomes:
            mark = "ok" if outcome.status == "ok" else "FAILED"
            lines.append(f"- [{outcome.task_id}] {outcome.label} - {mark}: {outcome.summary}")
        if running:
            lines.append(f"{running} still running.")
        return "\n".join(lines)
