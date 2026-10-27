# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Spawn tool for creating background subagents."""

from __future__ import annotations

import asyncio
from pathlib import Path
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

# Levels of delegation below the user's turn: 1 = the turn's own subagents,
# 2 = those may spawn helpers, 3 = helpers of helpers. Beyond, spawn refuses.
MAX_SPAWN_DEPTH = 3


def _split(raw: str | None, sep: str) -> list[str]:
    return [p.strip() for p in (raw or "").split(sep) if p.strip()]


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "'start' (default) launches a subagent and needs 'task'. "
            "'results' lists how recently finished subagents ended, for when a "
            "result did not reach you. "
            "'worktrees' lists the isolated subagents still active: id, label, "
            "checkout path, base commit, modified files and workspace policy.",
            enum=["start", "results", "worktrees"],
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
        allowed_paths=StringSchema(
            "Optional comma-separated globs (e.g. 'navin/agent/**, tests/test_x.py') "
            "the subagent may modify. Requires isolate=true: without it the spawn is "
            "refused, since the policy is enforced only before merging an isolated "
            "checkout. A file changed outside these globs blocks the merge and the "
            "checkout is kept for review."
        ),
        allowed_commands=StringSchema(
            "Optional comma-separated command prefixes the subagent's exec tool may "
            "run (e.g. 'pytest, git status'); any other shell command is refused. "
            "Requires isolate=true."
        ),
        test_commands=StringSchema(
            "Optional commands separated by ';;' run in the isolated checkout before "
            "merging (e.g. 'python -m pytest -q tests/test_x.py'). A failure blocks "
            "the merge. Requires isolate=true."
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
            "Ignored: subagents always run on the session's model."
        ),
    )
)
class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    # Subagents may delegate too, up to MAX_SPAWN_DEPTH levels below the
    # parent turn. Every level shares the same admission queue and limits.
    _scopes = {"core", "subagent"}

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
            "finished ones ended. Several can run at once: as soon as a request has "
            "two or more independent tasks (audit of several modules, backend and "
            "frontend, a list of fixes), start one subagent per task in the same "
            "reply instead of doing them in sequence; stay sequential only when "
            "tasks depend on each other or are trivial. A queued reply means the task is waiting for a "
            "slot: report running and queued counts separately. Each reports back on its "
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
        allowed_paths: str | None = None,
        allowed_commands: str | None = None,
        test_commands: str | None = None,
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
            # Reading history can wait for a durable write by another
            # subagent. Keep that disk/lock wait away from terminal input.
            return await asyncio.to_thread(self._render_outcomes, session_key)
        if action == "worktrees":
            return await asyncio.to_thread(self._render_worktrees)
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
        policy_spec = {
            "allowed_paths": _split(allowed_paths, ","),
            "allowed_commands": _split(allowed_commands, ","),
            "test_commands": _split(test_commands, ";;"),
        }
        if not any(policy_spec.values()):
            policy_spec = None
        elif not isolate:
            return ToolResult.error(
                "Error: allowed_paths, allowed_commands and test_commands need "
                "isolate=true: the workspace policy is checked before merging an "
                "isolated checkout. Pass isolate=true or drop these parameters."
            )
        # P2-3: role-specialised subagents. A named preset resolves to its own
        # runtime; everything else about the spawn (policy, scope, context)
        # still inherits from the parent.
        runtime = request_ctx.runtime
        # Subagents run on the session's model (the one picked in the desktop
        # composer or the CLI). A preset the model names on its own is
        # ignored: it silently moved delegated work onto another model.
        del model_preset
        preset_name = ""
        if agent:
            preset_name = self._agent_model_preset(agent)
            if preset_name:
                try:
                    runtime = self._resolve_model_preset(preset_name)  # type: ignore[misc]
                except (KeyError, ValueError, TypeError):
                    # A definition written for another harness may name a model
                    # Navin has no preset for: keep the parent's model.
                    runtime = request_ctx.runtime
                preset_name = ""
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
        parent_task_id = (
            metadata.get("subagent_task_id") if isinstance(metadata, dict) else None
        )
        depth_of = getattr(self._manager, "spawn_depth", None)
        depth = (depth_of(parent_task_id) + 1) if parent_task_id and depth_of else 1
        if depth > MAX_SPAWN_DEPTH:
            return ToolResult.error(
                f"Error: delegation depth limit reached ({MAX_SPAWN_DEPTH} levels). "
                "Do this part of the work yourself instead of spawning."
            )
        extra: dict[str, Any] = (
            {"parent_task_id": parent_task_id, "depth": depth} if parent_task_id else {}
        )
        if policy_spec is not None:
            extra["workspace_policy_spec"] = policy_spec
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
            **extra,
        )

    def _agent_model_preset(self, agent: str) -> str:
        """The ``model`` an agent definition asks for, when a resolver exists."""
        workspace = getattr(self._manager, "workspace", None)
        if self._resolve_model_preset is None or workspace is None:
            return ""
        try:
            from navin.agent.project_agents import find_project_agent

            definition = find_project_agent(Path(workspace), agent)
        except Exception:  # noqa: BLE001 - a bad definition never blocks a spawn
            return ""
        model = (definition.model if definition else "").strip()
        return "" if model.lower() in ("", "inherit", "default") else model

    def _render_worktrees(self) -> str:
        """List the isolated subagents still active, with their changes and policy."""
        listing = getattr(self._manager, "active_worktrees", None)
        if listing is None:
            return "This runtime does not track isolated subagents."
        entries = listing()
        if not entries:
            return "No isolated subagent is active."
        lines = [f"{len(entries)} active isolated subagent(s):"]
        for e in entries:
            files = e["changed_files"]
            changed = (
                "unknown (git failed)" if files is None
                else (", ".join(files) if files else "none")
            )
            lines.append(
                f"- [{e['task_id']}] {e['label']} at {e['path']} (base {e['base']})\n"
                f"  modified: {changed}\n  policy: {e['policy'] or 'none'}"
            )
        return "\n".join(lines)

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
