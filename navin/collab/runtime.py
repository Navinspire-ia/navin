# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Run Collaboration Mode teammates on the subagent engine.

Shared by the orchestrator's ``collab`` tool and the chat's Team panel (the
user assigning or briefing a teammate directly), so both apply the same
profile, sandbox, round limit and budget.
"""

from __future__ import annotations

from typing import Any, Callable

from navin.collab.team import find_member, member_identity

# Plan designs without mutating: a teammate assigned from a Plan turn reads
# and reports, it never edits or runs commands.
PLAN_DENIED_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file", "apply_patch", "manage_files", "notebook_edit",
    "exec", "write_stdin", "start_app", "open_preview", "git",
})


class CollabError(ValueError):
    """A refusal worth showing as is (teammate unknown, limit reached...)."""


# Session metadata key holding the team a chat works with ("off" = none).
SESSION_TEAM_KEY = "collab_team"
TEAM_OFF = "off"


def enabled_members(collab: Any) -> list[Any]:
    return [m for m in (getattr(collab, "members", None) or []) if getattr(m, "enabled", True)]


def session_team(collab: Any, team_id: str | None) -> tuple[Any | None, list[Any]]:
    """The team a session works with and its enabled teammates.

    ``team_id`` comes from the session ("off" = no team in this chat). Unset or
    unknown falls back to the default team, then to the first team.
    """
    if (team_id or "").strip().lower() == TEAM_OFF:
        return None, []
    teams = list(getattr(collab, "teams", None) or [])
    by_id = {team.id: team for team in teams}
    team = by_id.get((team_id or "").strip()) or by_id.get(getattr(collab, "default_team", "")) or (
        teams[0] if teams else None
    )
    if team is None:
        return None, enabled_members(collab)
    members_by_id = {m.id: m for m in enabled_members(collab)}
    return team, [members_by_id[mid] for mid in team.members if mid in members_by_id]


def resolve_member(collab: Any, name: str, members: list[Any] | None = None) -> Any:
    members = enabled_members(collab) if members is None else members
    member = find_member(members, name)
    if member is None:
        names = ", ".join(m.name for m in members) or "nobody"
        raise CollabError(f"No teammate named {name!r} in this session's team. The team is: {names}.")
    return member


def member_runtime(
    member: Any,
    parent_runtime: Any,
    resolve_preset: Callable[[str], Any] | None,
) -> Any:
    """The teammate's model preset and reasoning effort, over the parent's runtime."""
    runtime = parent_runtime
    preset = (getattr(member, "model_preset", "") or "default").strip()
    if preset and preset != "default" and resolve_preset is not None:
        try:
            runtime = resolve_preset(preset)
        except (KeyError, ValueError) as exc:
            raise CollabError(
                f"{member.name}'s model preset {preset!r} is not available ({exc}). "
                "Pick another model for this teammate in Settings > Team."
            ) from exc
    effort = (getattr(member, "reasoning_effort", None) or "").strip()
    if effort and hasattr(runtime, "with_generation_overrides"):
        runtime = runtime.with_generation_overrides(reasoning_effort=effort)
    return runtime


def member_payload(
    member: Any,
    collab: Any,
    *,
    composer_mode: str | None = None,
    teammates_of: list[Any] | None = None,
) -> dict[str, Any]:
    """Profile dict handed to SubagentManager.spawn (private keys start with _)."""
    payload = member.model_dump()
    teammates = []
    for other in (enabled_members(collab) if teammates_of is None else teammates_of):
        identity = member_identity(other)
        identity["specialty"] = other.specialty
        identity["enabled"] = True
        teammates.append(identity)
    payload["_teammates"] = teammates
    payload["_collab"] = {
        "human_approval_critical": collab.human_approval_critical,
        "manager_name": getattr(collab, "manager_name", ""),
        "manager_email": getattr(collab, "manager_email", ""),
        "email_contacts": [c.get("name") or c.get("email") for c in getattr(collab, "email_contacts", None) or []],
        "email_allow_any": bool(getattr(collab, "email_allow_any", False)),
    }
    if (composer_mode or "").strip().lower() == "plan":
        payload["sandbox"] = "readonly"
        payload["_extra_denied"] = sorted(PLAN_DENIED_TOOLS)
    return payload


async def assign_member(
    *,
    manager: Any,
    collab: Any,
    member_name: str,
    task: str,
    session_key: str,
    origin_channel: str,
    origin_chat_id: str,
    parent_runtime: Any,
    resolve_preset: Callable[[str], Any] | None = None,
    workspace_scope: Any = None,
    origin_message_id: str | None = None,
    parent_metadata: dict[str, Any] | None = None,
    mission: str = "",
    isolate: bool | None = None,
    composer_mode: str | None = None,
    assigned_by: str = "parent",
    team_id: str | None = None,
) -> str:
    """Start (or queue) one teammate on *task*; return the spawn detail."""
    if not getattr(collab, "enabled", False):
        raise CollabError(
            "Collaboration Mode is off. Turn it on in Settings > Team to work with the team."
        )
    if not (task or "").strip():
        raise CollabError("A teammate needs a task.")
    team, members = session_team(collab, team_id)
    if team is None and (team_id or "").strip().lower() == TEAM_OFF:
        raise CollabError("Team mode is off in this chat. Pick a team in the composer to work with one.")
    member = resolve_member(collab, member_name, members)
    rounds = manager.collab_round(session_key, mission, member.id)
    if rounds > collab.max_rounds:
        raise CollabError(
            f"Round limit reached: {member.name} already had {collab.max_rounds} "
            f"assignments in mission {mission or 'default'!r}. Stop, summarise what "
            "is blocking, and ask the user to decide (or start a new mission)."
        )
    runtime = member_runtime(member, parent_runtime, resolve_preset)
    payload = member_payload(member, collab, composer_mode=composer_mode, teammates_of=members)
    history = getattr(manager, "member_history", None)
    if callable(history):
        continuity = history(session_key, member.id)
        if continuity:
            payload["_continuity"] = continuity
    if isolate is None:
        isolate = payload.get("sandbox") == "worktree"
    title = member.title or member.role
    return await manager.spawn(
        task=task.strip(),
        runtime=runtime,
        label=f"{member.name} ({title})",
        origin_channel=origin_channel,
        origin_chat_id=origin_chat_id,
        session_key=session_key,
        origin_message_id=origin_message_id,
        workspace_scope=workspace_scope,
        isolate=bool(isolate),
        parent_metadata=parent_metadata,
        member=payload,
        budget_tokens=int(collab.budget_tokens or 0),
        assigned_by=assigned_by,
    )


def roster_rows(
    collab: Any, running: list[dict[str, Any]], members: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Team listing for the orchestrator: who exists and who is busy."""
    rows = []
    for member in (enabled_members(collab) if members is None else members):
        live = [r for r in running if r.get("name") == member.name and r.get("phase") not in {"done", "error"}]
        rows.append({
            "name": member.name,
            "title": member.title,
            "role": member.role,
            "specialty": member.specialty,
            "model": member.model_preset,
            "effort": member.reasoning_effort or "",
            "sandbox": member.sandbox,
            "busy": [{"task_id": r["task_id"], "phase": r.get("phase"), "task": (r.get("task") or "")[:160]}
                     for r in live],
        })
    return rows


_COLLAB_CACHE: dict[str, Any] = {}


def current_collab() -> Any:
    """The saved Collaboration Mode settings, re-read only when the file changes.

    Read on every turn for the orchestrator prompt, so it parses the one key it
    needs instead of loading (and decrypting) the whole configuration.
    """
    import json

    from navin.config.loader import _file_signature, get_config_path
    from navin.config.schema import CollabConfig

    path = get_config_path()
    # mtime alone missed a switch saved twice within one timestamp tick (coarse
    # on some file systems): the feed then reported the old value until a
    # restart. size, inode and ctime change with every save_config replace.
    stamp = (str(path), _file_signature(path))
    cached = _COLLAB_CACHE.get("value")
    if cached is not None and _COLLAB_CACHE.get("stamp") == stamp:
        return cached
    data: Any = {}
    if stamp[1] is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8")).get("collab") or {}
        except (OSError, ValueError, AttributeError):
            data = {}
    try:
        value = CollabConfig.model_validate(data)
    except ValueError:
        value = CollabConfig()
    _COLLAB_CACHE.update(value=value, stamp=stamp)
    return value


def session_team_id(sessions: Any, session_key: str | None) -> str | None:
    """Team id stored on the session, None when the chat never picked one."""
    if sessions is None or not session_key:
        return None
    try:
        session = sessions.get_or_create(session_key)
    except Exception:  # noqa: BLE001 - no session store, default team
        return None
    value = (getattr(session, "metadata", None) or {}).get(SESSION_TEAM_KEY)
    return str(value) if isinstance(value, str) and value else None


def team_summary(collab: Any, team_id: str | None) -> dict[str, Any]:
    """What the chat shows for the session's team."""
    team, members = session_team(collab, team_id)
    return {
        "enabled": bool(getattr(collab, "enabled", False)),
        "team": team.id if team is not None else TEAM_OFF,
        "team_name": team.name if team is not None else "",
        "members": [member_identity(m) for m in members],
        "teams": [{"id": t.id, "name": t.name} for t in (getattr(collab, "teams", None) or [])],
    }
