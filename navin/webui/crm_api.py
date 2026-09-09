# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP payloads for the CRM workbench."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.crm.insights import insights as compute_insights
from navin.crm.insights import timeline as compute_timeline
from navin.crm.outreach import channel_status, outreach
from navin.crm.schema import KINDS
from navin.crm.store import (
    CrmError,
    accept_invite,
    calendar_activities,
    convert_lead,
    create_record,
    dashboard,
    delete_record,
    due_followups,
    ensure_owner,
    get_record,
    get_settings,
    invite_member,
    kick_member,
    list_audit,
    list_lines,
    list_members,
    list_records,
    role_of,
    search_records,
    set_member_role,
    sync_status,
    update_record,
    update_settings,
)
from navin.security.workspace_access import WorkspaceScope


def _project(scope: WorkspaceScope) -> Path:
    raw = getattr(scope, "project_path", None)
    text = str(raw or "").strip()
    if not text:
        raise CrmError("no project folder for this session", status=400)
    path = Path(text).expanduser()
    if not path.is_dir():
        raise CrmError("project root not found", status=404)
    return path.resolve()


def _kind(raw: str) -> str:
    kind = (raw or "").strip()
    extra = {
        "products",
        "opportunity_lines",
        "files",
    }
    if kind not in KINDS and kind not in extra:
        raise CrmError("unknown CRM kind", status=400)
    return kind


def _actor(body: dict[str, Any] | None, fallback: str = "") -> str:
    payload = body or {}
    return str(payload.get("actor") or payload.get("owner") or fallback or "").strip()


def list_payload(scope: WorkspaceScope, kind: str) -> dict[str, Any]:
    return {"kind": _kind(kind), "records": list_records(_project(scope), _kind(kind))}


def get_payload(scope: WorkspaceScope, kind: str, record_id: str) -> dict[str, Any]:
    return {"record": get_record(_project(scope), _kind(kind), record_id)}


def create_payload(scope: WorkspaceScope, kind: str, body: dict[str, Any]) -> dict[str, Any]:
    actor = _actor(body)
    if actor:
        ensure_owner(_project(scope), actor)
    return {"record": create_record(_project(scope), _kind(kind), body, actor=actor)}


def update_payload(
    scope: WorkspaceScope, kind: str, record_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    return {"record": update_record(_project(scope), _kind(kind), record_id, body, actor=_actor(body))}


def delete_payload(scope: WorkspaceScope, kind: str, record_id: str, actor: str = "") -> dict[str, Any]:
    return delete_record(_project(scope), _kind(kind), record_id, actor=actor)


def convert_payload(
    scope: WorkspaceScope,
    lead_id: str,
    owner: str = "",
    actor: str = "",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    who = actor or owner
    payload = dict(body or {})
    if who:
        ensure_owner(_project(scope), who)
    if owner and not payload.get("owner"):
        payload["owner"] = owner
    if actor and not payload.get("actor"):
        payload["actor"] = actor
    return convert_lead(_project(scope), lead_id, owner=owner, actor=who, fields=payload)


def dashboard_payload(scope: WorkspaceScope) -> dict[str, Any]:
    return dashboard(_project(scope))


def settings_payload(scope: WorkspaceScope, actor: str = "") -> dict[str, Any]:
    root = _project(scope)
    settings = get_settings(root)
    role = role_of(root, actor) if actor else None
    return {
        **settings,
        "role": role,
        "canWrite": role != "viewer",
    }


def update_settings_payload(scope: WorkspaceScope, body: dict[str, Any]) -> dict[str, Any]:
    actor = _actor(body)
    if actor:
        ensure_owner(_project(scope), actor)
    return {
        "settings": {
            **update_settings(_project(scope), body, actor=actor),
            "role": role_of(_project(scope), actor) if actor else None,
            "canWrite": True,
        }
    }


def insights_payload(scope: WorkspaceScope, kind: str, record_id: str) -> dict[str, Any]:
    return compute_insights(_project(scope), _kind(kind), record_id)


def timeline_payload(scope: WorkspaceScope, kind: str, record_id: str) -> dict[str, Any]:
    return {"records": compute_timeline(_project(scope), _kind(kind), record_id)}


def search_payload(scope: WorkspaceScope, query: str) -> dict[str, Any]:
    return {"query": query, "results": search_records(_project(scope), query)}


def audit_payload(scope: WorkspaceScope, kind: str = "", record_id: str = "") -> dict[str, Any]:
    return {"records": list_audit(_project(scope), kind=kind, record_id=record_id)}


def members_payload(scope: WorkspaceScope, actor: str = "") -> dict[str, Any]:
    root = _project(scope)
    if actor:
        ensure_owner(root, actor)
    return list_members(root)


def invite_payload(scope: WorkspaceScope, body: dict[str, Any]) -> dict[str, Any]:
    actor = _actor(body)
    if actor:
        ensure_owner(_project(scope), actor)
    return invite_member(
        _project(scope),
        identity=str(body.get("identity") or body.get("email") or body.get("handle") or ""),
        role=str(body.get("role") or "member"),
        actor=actor,
        email=str(body.get("email") or ""),
        handle=str(body.get("handle") or ""),
        user_id=str(body.get("userId") or body.get("user_id") or ""),
    )


def accept_payload(scope: WorkspaceScope, body: dict[str, Any]) -> dict[str, Any]:
    return accept_invite(
        _project(scope),
        actor=_actor(body),
        accept=body.get("accept", True) is not False,
        invite_id=str(body.get("id") or body.get("inviteId") or ""),
    )


def role_payload(scope: WorkspaceScope, body: dict[str, Any]) -> dict[str, Any]:
    return {"member": set_member_role(
        _project(scope),
        str(body.get("id") or ""),
        str(body.get("role") or "member"),
        actor=_actor(body),
    )}


def kick_payload(scope: WorkspaceScope, body: dict[str, Any]) -> dict[str, Any]:
    return kick_member(_project(scope), str(body.get("id") or ""), actor=_actor(body))


def products_payload(scope: WorkspaceScope) -> dict[str, Any]:
    return {"records": list_records(_project(scope), "products")}


def lines_payload(scope: WorkspaceScope, opportunity_id: str) -> dict[str, Any]:
    return {"records": list_lines(_project(scope), opportunity_id)}


def followups_payload(
    scope: WorkspaceScope,
    *,
    days: int = 7,
    create: bool = False,
    actor: str = "",
) -> dict[str, Any]:
    return due_followups(_project(scope), days=days, create=create, actor=actor)


def sync_payload(scope: WorkspaceScope) -> dict[str, Any]:
    return sync_status(_project(scope))


def calendar_payload(scope: WorkspaceScope, start: int, end: int) -> dict[str, Any]:
    return {"records": calendar_activities(_project(scope), start=start, end=end)}


def outreach_payload(scope: WorkspaceScope, body: dict[str, Any]) -> dict[str, Any]:
    return outreach(
        _project(scope),
        channel=str(body.get("channel") or "email"),
        to=str(body.get("to") or ""),
        subject=str(body.get("subject") or ""),
        body=str(body.get("body") or ""),
        send=bool(body.get("send")),
        contact_id=str(body.get("contactId") or ""),
        company_id=str(body.get("companyId") or ""),
        opportunity_id=str(body.get("opportunityId") or ""),
        lead_id=str(body.get("leadId") or ""),
        actor=_actor(body),
        log_anyway=bool(body.get("logAnyway")),
    )


def channels_payload() -> dict[str, Any]:
    return {
        "email": channel_status("email"),
        "whatsapp": channel_status("whatsapp"),
        "teams": channel_status("teams"),
    }
