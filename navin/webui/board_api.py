"""HTTP payload builders for the project task board (Dev workbench).

Human edits arrive through these payloads; agents mutate the same store via
the ``board`` tool. Both paths append to the activity timeline, which feeds
the Evolutions view.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.board import session_focus
from navin.board.ledger import MissionLedgerStore
from navin.board.plan import session_plan
from navin.board.store import BoardError, ProjectBoardStore
from navin.security.workspace_access import WorkspaceScope

_DEFAULT_HUMAN_ACTOR = "user"


def _store_for_scope(scope: WorkspaceScope) -> ProjectBoardStore:
    project = Path(scope.project_path).expanduser()
    if not project.is_dir():
        raise BoardError(f"project not found: {project}", status=404)
    try:
        from navin.utils.helpers import ensure_project_scaffold

        ensure_project_scaffold(project, silent=True)
    except Exception:
        pass
    return ProjectBoardStore(project)


def _mission_for_store(store: ProjectBoardStore) -> dict[str, Any] | None:
    try:
        ledger_store = MissionLedgerStore(store.project_path)
        if not ledger_store.exists():
            return None
        return ledger_store.load()
    except Exception:
        return None


def _with_session_plan(
    payload: dict[str, Any],
    session_key: str | None,
    *,
    store: ProjectBoardStore | None = None,
) -> dict[str, Any]:
    """Attach the narrow plan for one conversation, or None when it has none.

    Sessions are not a board concept, which is why this is computed here rather
    than in the store: the store answers for the project, the caller knows which
    conversation is asking.
    """

    mission = _mission_for_store(store) if store is not None else None
    if mission is not None:
        payload["mission"] = mission
    payload["session_plan"] = session_plan(
        payload.get("tasks") or [],
        session_focus.touched(session_key),
        mission=mission,
    )
    return payload


def board_payload(
    scope: WorkspaceScope,
    session_key: str | None = None,
) -> dict[str, Any]:
    store = _store_for_scope(scope)
    payload = _with_session_plan(store.payload(), session_key, store=store)
    try:
        from navin.board.autonomy import autonomy_state

        payload["autonomy"] = autonomy_state(store.project_path)
    except Exception:
        payload["autonomy"] = None
    return payload


def _project_for_issues(
    scope: WorkspaceScope,
    project_path: str | Path | None,
) -> Path:
    raw = project_path if project_path is not None else scope.project_path
    project = Path(raw).expanduser().resolve(strict=False)
    if not project.is_dir():
        raise BoardError(f"project not found: {project}", status=404)
    return project


def github_issues_payload(
    scope: WorkspaceScope,
    *,
    state: str = "open",
    project_path: str | Path | None = None,
) -> dict[str, Any]:
    """Forge issues of the bound project, for the Issues panel.

    ``project_path`` (when set) overrides the session workspace so the Issues
    tab follows the Project Home selector instead of a chat bound to a
    different folder (e.g. a parent dir with no GitHub remote).

    ``repo`` in the payload is the configured tracker (``owner/name``) when the
    project reads issues from another repository, and None when they come from
    its own remote.
    """
    from navin.board.github_sync import list_github_issues

    project = _project_for_issues(scope, project_path)
    result = list_github_issues(project, state=state)
    payload = {
        "ok": bool(result.get("ok")),
        "project_path": str(project),
        "state": state,
        "repo": result.get("repo") or None,
        "issues": result.get("issues") or [],
        "detail": str(result.get("detail") or ""),
        # Which forge answered: the panel says "GitLab issues" on GitLab and
        # offers a token, not a `gh` install, when the host is not github.com.
        "forge": result.get("forge") or "unknown",
        "forge_label": result.get("forge_label") or "",
        "host": result.get("host") or "",
        "remote_repo": result.get("remote_repo"),
    }
    if result.get("install"):
        payload["install"] = result["install"]
    if result.get("token_setup"):
        payload["token_setup"] = result["token_setup"]
    return payload


def github_issues_repo_payload(
    scope: WorkspaceScope,
    repo: str | None,
    *,
    project_path: str | Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Point this project's Issues panel at *repo*, or clear it when empty.

    Persisted next to the board (``.navin/board/settings.json``) so the choice
    survives restarts and is picked up by the agent and the import alike.
    """
    from navin.board.autonomy import write_autonomy

    project = _project_for_issues(scope, project_path)
    clean_actor = (actor or _DEFAULT_HUMAN_ACTOR).strip() or _DEFAULT_HUMAN_ACTOR
    try:
        settings = write_autonomy(project, {"issues_repo": repo}, actor=clean_actor)
    except ValueError as e:
        raise BoardError(str(e)) from e
    except OSError as e:
        raise BoardError(f"could not persist the issues repository: {e}", status=500) from e
    slug = settings.get("issues_repo")
    try:
        ProjectBoardStore(project).log_activity(
            kind="issues_repo_updated",
            actor=clean_actor,
            actor_type="human",
            detail=f"issues repository set to {slug}" if slug else "issues repository cleared",
        )
    except Exception:
        # The setting is what matters; a board that cannot log must not fail it.
        pass
    return {"ok": True, "project_path": str(project), "repo": slug}


def github_pr_sync_payload(scope: WorkspaceScope) -> dict[str, Any]:
    """Suggest board updates when linked task PRs have merged (no auto-close)."""
    from navin.board.pr_sync import pr_sync_payload

    store = _store_for_scope(scope)
    result = pr_sync_payload(store.project_path, store)
    return {
        "ok": bool(result.get("ok")),
        "available": bool(result.get("available")),
        "project_path": str(store.project_path),
        "suggestions": result.get("suggestions") or [],
        "detail": str(result.get("detail") or ""),
    }


def board_autonomy_update_payload(
    scope: WorkspaceScope,
    fields: dict[str, Any],
    *,
    actor: str | None = None,
) -> dict[str, Any]:
    """Persist the project autonomy consent/toggles and return the new state."""
    from navin.board.autonomy import autonomy_state, write_autonomy

    store = _store_for_scope(scope)
    clean_actor = (actor or _DEFAULT_HUMAN_ACTOR).strip() or _DEFAULT_HUMAN_ACTOR
    try:
        settings = write_autonomy(store.project_path, fields, actor=clean_actor)
    except ValueError as e:
        raise BoardError(str(e)) from e
    except OSError as e:
        raise BoardError(f"could not persist autonomy settings: {e}", status=500) from e
    store.log_activity(
        kind="autonomy_updated",
        actor=clean_actor,
        actor_type="human",
        detail=", ".join(
            f"{k}={'on' if v else 'off'}" for k, v in sorted(fields.items())
        )
        or ("enabled" if settings.get("enabled") else "disabled"),
    )
    return autonomy_state(store.project_path)


def board_update_payload(
    scope: WorkspaceScope,
    op: dict[str, Any],
    session_key: str | None = None,
) -> dict[str, Any]:
    """Apply one board mutation and return the refreshed board payload.

    ``op`` shape: ``{"action": ..., "actor": ..., ...action fields}``.
    The web API is the human surface: actor_type is always "human" here so
    UI edits cannot impersonate agents in the timeline.

    A human edit refreshes the statuses shown in the chat's plan panel but never
    adds a task to it - the panel is what the agent set out to do, and dragging a
    card in the kanban is not that.
    """
    store = _store_for_scope(scope)
    action = op.get("action")
    actor = op.get("actor") if isinstance(op.get("actor"), str) else None
    actor = (actor or _DEFAULT_HUMAN_ACTOR).strip() or _DEFAULT_HUMAN_ACTOR
    common = {"actor": actor, "actor_type": "human"}

    if action == "create_task":
        task = op.get("task")
        if not isinstance(task, dict):
            raise BoardError("missing task object")
        store.create_task(
            title=task.get("title") or "",
            description=task.get("description"),
            status=task.get("status"),
            priority=task.get("priority"),
            labels=task.get("labels") if isinstance(task.get("labels"), list) else None,
            assignee=task.get("assignee") if isinstance(task.get("assignee"), dict) else None,
            milestone_id=task.get("milestone_id"),
            depends_on=task.get("depends_on") if isinstance(task.get("depends_on"), list) else None,
            **common,
        )
    elif action == "update_task":
        task_id = op.get("task_id")
        fields = op.get("fields")
        if not isinstance(task_id, str) or not isinstance(fields, dict):
            raise BoardError("update_task requires task_id and fields")
        store.update_task(task_id, fields=fields, **common)
    elif action == "comment_task":
        task_id = op.get("task_id")
        text = op.get("text")
        if not isinstance(task_id, str) or not isinstance(text, str):
            raise BoardError("comment_task requires task_id and text")
        store.comment_task(task_id, text=text, **common)
    elif action == "delete_task":
        task_id = op.get("task_id")
        if not isinstance(task_id, str):
            raise BoardError("delete_task requires task_id")
        store.delete_task(task_id, **common)
    elif action == "create_milestone":
        milestone = op.get("milestone")
        if not isinstance(milestone, dict):
            raise BoardError("missing milestone object")
        store.create_milestone(
            title=milestone.get("title") or "",
            description=milestone.get("description"),
            target_date=milestone.get("target_date"),
            status=milestone.get("status"),
            **common,
        )
    elif action == "update_milestone":
        milestone_id = op.get("milestone_id")
        fields = op.get("fields")
        if not isinstance(milestone_id, str) or not isinstance(fields, dict):
            raise BoardError("update_milestone requires milestone_id and fields")
        store.update_milestone(milestone_id, fields=fields, **common)
    elif action == "delete_milestone":
        milestone_id = op.get("milestone_id")
        if not isinstance(milestone_id, str):
            raise BoardError("delete_milestone requires milestone_id")
        store.delete_milestone(milestone_id, **common)
    elif action == "pause_mission":
        ledger_store = MissionLedgerStore(store.project_path)
        ledger = ledger_store.load()
        if not ledger:
            raise BoardError("no mission ledger", status=404)
        reason = op.get("reason") if isinstance(op.get("reason"), str) else "human"
        ledger = ledger_store.pause(
            ledger, reason=reason or "human", actor="human"
        )
        ledger_store.save(ledger)
        store.log_activity(
            kind="mission_paused",
            actor=actor,
            actor_type="human",
            detail=str(ledger.get("pause_reason") or reason or "human"),
        )
    elif action == "resume_mission":
        ledger_store = MissionLedgerStore(store.project_path)
        ledger = ledger_store.load()
        if not ledger:
            raise BoardError("no mission ledger", status=404)
        ledger = ledger_store.resume(ledger, actor="human")
        ledger_store.save(ledger)
        store.log_activity(
            kind="mission_resumed",
            actor=actor,
            actor_type="human",
            detail=f"v{ledger.get('version')}",
        )
    elif action == "update_mission":
        fields = op.get("fields")
        if not isinstance(fields, dict):
            raise BoardError("update_mission requires fields")
        ledger_store = MissionLedgerStore(store.project_path)
        ledger = ledger_store.load()
        if not ledger:
            raise BoardError("no mission ledger", status=404)
        ledger = ledger_store.apply_manual_edit(ledger, fields, actor="human")
        ledger_store.save(ledger)
        store.log_activity(
            kind="mission_updated",
            actor=actor,
            actor_type="human",
            detail=", ".join(sorted(fields.keys())) or "edit",
        )
    elif action == "sync_github":
        # Import open forge issues as board tasks (idempotent by issue URL), so
        # a human can pull the repo's issues without going through the agent.
        # ``repo`` lets the panel import exactly the tracker it is displaying,
        # on GitHub, GitLab or Forgejo.
        from navin.board.github_sync import import_issues_to_board

        repo = op.get("repo") if isinstance(op.get("repo"), str) else None
        result = import_issues_to_board(store.project_path, store, actor=actor, repo=repo)
        if not result.get("ok"):
            raise BoardError(f"issue sync failed: {result.get('detail')}", status=502)
        store.log_activity(
            kind="github_sync",
            actor=actor,
            actor_type="human",
            detail=str(result.get("detail") or ""),
        )
    else:
        raise BoardError(f"unknown board action: {action}")

    return _with_session_plan(store.payload(), session_key, store=store)
