# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Collaboration Mode: the orchestrator works with its named team."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.context import current_request_context
from navin.agent.tools.schema import BooleanSchema, StringSchema, tool_parameters_schema
from navin.security.workspace_access import current_workspace_scope


def _load_collab() -> Any:
    from navin.collab.runtime import current_collab

    return current_collab()


@tool_parameters(tool_parameters_schema(
    action=StringSchema(
        "'roster' lists the team and who is busy; 'assign' gives a teammate a task "
        "(member + task); 'message' briefs or interrupts running teammates "
        "(member = a name or 'all', content); 'feed' returns the recent team exchanges; "
        "'email' sends the human manager an email (subject + content), e.g. a mission "
        "report or a decision that needs them.",
        enum=["roster", "assign", "message", "feed", "email"],
    ),
    subject=StringSchema("Email subject, for email."),
    to=StringSchema("Email recipients: contact names or addresses, comma-separated. Empty = the manager."),
    member=StringSchema("Teammate name (e.g. 'Mark'), or 'all' for message."),
    task=StringSchema("What the teammate must do, with context, files and acceptance criteria. For assign."),
    content=StringSchema("Message for message: a briefing, a correction, an answer."),
    mission=StringSchema(
        "Short name of the current mission (e.g. 'auth-module'). Round limits count "
        "per teammate per mission; use a new name for a new user request."
    ),
    isolate=BooleanSchema(
        description="Run the teammate in its own git checkout (parallel competing "
        "solutions). Defaults to the teammate's sandbox setting."
    ),
))
class CollabTool(Tool):
    """Assign work to named teammates and talk with them."""

    _scopes = {"core"}

    def __init__(
        self,
        manager: Any,
        resolve_model_preset: Any | None = None,
        sessions: Any | None = None,
    ) -> None:
        self._manager = manager
        self._resolve_model_preset = resolve_model_preset
        self._sessions = sessions

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "subagent_manager", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            ctx.subagent_manager,
            getattr(ctx, "resolve_model_preset", None),
            getattr(ctx, "sessions", None),
        )

    def runtime_context_provider(self):  # type: ignore[no-untyped-def]
        """The session's team, named in the turn, when Collaboration Mode is on.

        Per turn rather than in the system prompt: each chat works with its own
        team (or none), so the roster changes from one session to the next.
        """
        async def provide(request: Any):  # type: ignore[no-untyped-def]
            from navin.collab.runtime import session_team, session_team_id
            from navin.collab.team import roster_prompt
            from navin.runtime_context import RuntimeContextBlock

            metadata = getattr(request, "metadata", None) or {}
            if metadata.get("subagent"):
                return None
            collab = await asyncio.to_thread(_load_collab)
            if not collab.enabled:
                return None
            team_id = await asyncio.to_thread(
                session_team_id, self._sessions, getattr(request, "session_key", None)
            )
            team, members = session_team(collab, team_id)
            if team is None or not members:
                return None
            text = roster_prompt(collab, members, team.name)
            return RuntimeContextBlock(source="collab-team", content=text) if text else None

        return provide

    def _team_id(self, session_key: str) -> str | None:
        from navin.collab.runtime import session_team_id

        return session_team_id(self._sessions, session_key)

    @property
    def name(self) -> str:
        return "collab"

    @property
    def description(self) -> str:
        return (
            "Collaboration Mode team (Settings > Team): named teammates, each with a job, "
            "model, effort, sandbox, prompt, tools and skills. Assign work by name, "
            "several at once when independent; they talk to each other and to you, live "
            "in the chat, and each reports back on its own when done. Use message to "
            "brief or interrupt a running teammate. Only when Collaboration Mode is on."
        )

    def call_read_only(self, arguments: Any) -> bool:
        # assign from a Plan turn runs the teammate read-only (see runtime).
        return isinstance(arguments, dict) and arguments.get("action", "roster") in {"roster", "feed"}

    async def execute(
        self,
        action: str = "roster",
        member: str = "",
        task: str = "",
        content: str = "",
        mission: str = "",
        isolate: bool | None = None,
        subject: str = "",
        to: str = "",
        **kwargs: Any,
    ) -> str:
        from navin.collab.runtime import CollabError, assign_member, roster_rows, session_team

        ctx = current_request_context()
        if ctx is None:
            return ToolResult.error("Error: collab requires an active conversation")
        session = ctx.session_key or f"{ctx.channel}:{ctx.chat_id}"
        collab = await asyncio.to_thread(_load_collab)
        if not collab.enabled:
            return ToolResult.error(
                "Error: Collaboration Mode is off. The user can turn it on in Settings > Team."
            )
        metadata = ctx.metadata if isinstance(ctx.metadata, dict) else {}
        team_id = await asyncio.to_thread(self._team_id, session)
        try:
            if action == "roster":
                team, members = session_team(collab, team_id)
                if team is None:
                    return "Team mode is off in this chat: the user picks a team in the composer."
                rows = roster_rows(collab, self._manager.team_members(session), members)
                return json.dumps({"team_name": team.name, "pattern": collab.pattern,
                                   "max_rounds": collab.max_rounds, "team": rows}, ensure_ascii=False)
            if action == "feed":
                feed = self._manager.team_feed(session)[-40:]
                return json.dumps([
                    {"from": e["sender"]["name"], "to": (e.get("target") or {}).get("name"),
                     "kind": e["kind"], "content": e["content"][:600]}
                    for e in feed
                ], ensure_ascii=False) if feed else "No team exchange yet in this conversation."
            if action == "assign":
                if ctx.runtime is None:
                    return ToolResult.error("Error: collab requires an active model runtime")
                if metadata.get("subagent"):
                    return ToolResult.error(
                        "Error: only the lead assigns teammates; use team(action='send') instead."
                    )
                # The user request this work belongs to. A report coming back is
                # injected with the original message id, so follow-up rounds of
                # one request count together, and a new request starts fresh.
                origin_id = ctx.message_id or metadata.get("origin_message_id") or ""
                return await assign_member(
                    manager=self._manager,
                    collab=collab,
                    member_name=member,
                    task=task,
                    session_key=session,
                    origin_channel=ctx.channel,
                    origin_chat_id=ctx.chat_id,
                    parent_runtime=ctx.runtime,
                    resolve_preset=self._resolve_model_preset,
                    workspace_scope=current_workspace_scope(),
                    origin_message_id=origin_id or None,
                    parent_metadata=dict(metadata),
                    mission=mission.strip() or str(origin_id),
                    isolate=isolate,
                    composer_mode=metadata.get("composer_mode"),
                    team_id=team_id,
                )
            if action == "email":
                from navin.agent.tools.team import _recipients_identity
                from navin.collab.mail import CollabMailError, send_team_email

                if metadata.get("subagent"):
                    return ToolResult.error(
                        "Error: collab email signs as the lead; teammates use team(action='email')."
                    )
                lead = {"id": "lead", "name": "Navin", "title": "Orchestrator"}
                try:
                    recipients = await asyncio.to_thread(
                        send_team_email, collab, sender=lead, subject=subject, body=content,
                        to=to, session_key=session,
                    )
                except CollabMailError as exc:
                    return ToolResult.error(f"Error: {exc}")
                self._manager.post_team_feed(
                    session, "email", "parent", f"{subject.strip()}\n\n{content.strip()}",
                    target_identity=_recipients_identity(recipients), status="sent", force=True,
                )
                return "Email sent to " + ", ".join(f"{r['name']} <{r['email']}>" for r in recipients) + "."
            if action == "message":
                if not content.strip():
                    return ToolResult.error("Error: message needs content")
                targets = (
                    [r["task_id"] for r in self._manager.team_members(session)
                     if r.get("name") and r.get("phase") not in {"done", "error"}]
                    if member.strip().lower() in {"all", "team", "everyone"}
                    else [member]
                )
                if not targets:
                    return "No teammate is running right now; assign one first."
                results = []
                for t in targets:
                    # One full or stopped inbox must not hide the sends that
                    # already went out to the rest of the team.
                    try:
                        results.append(self._manager.send_team_message(session, "parent", t, content))
                    except ValueError as exc:
                        if len(targets) == 1:
                            raise
                        results.append(f"Not delivered to {t}: {exc}")
                await self._manager.flush_journal()
                return "\n".join(results)
            return ToolResult.error(f"Error: unknown collab action {action!r}")
        except (CollabError, ValueError) as exc:
            return ToolResult.error(f"Error: {exc}")
