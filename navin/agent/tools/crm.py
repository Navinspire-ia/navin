# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""CRM tool: the chat reads and writes the same store as the workbench."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import StringSchema, tool_parameters_schema
from navin.crm.insights import insights as compute_insights
from navin.crm.insights import timeline as compute_timeline
from navin.crm.schema import KINDS
from navin.crm.store import (
    CrmError,
    accept_invite,
    convert_lead,
    create_record,
    dashboard,
    delete_record,
    due_followups,
    get_record,
    get_settings,
    invite_member,
    list_audit,
    list_lines,
    list_members,
    list_records,
    search_records,
    sync_status,
    update_record,
    update_settings,
)
from navin.security.workspace_access import current_tool_workspace


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "CRM action on the shared project store (same SQLite as Studio CRM).",
            enum=[
                "search",
                "get",
                "list",
                "create",
                "update",
                "delete",
                "convert_lead",
                "log_activity",
                "move_stage",
                "dashboard",
                "insights",
                "timeline",
                "audit",
                "invite",
                "accept_invite",
                "members",
                "products",
                "lines",
                "followups",
                "sync_status",
                "settings",
            ],
        ),
        kind=StringSchema(
            "Record kind: companies, contacts, leads, opportunities, activities, products, opportunity_lines.",
            enum=list(KINDS) + ["products", "opportunity_lines"],
        ),
        id=StringSchema("Record id (get/update/delete/insights/timeline/convert_lead/move_stage/lines)."),
        query=StringSchema("Search text across the CRM."),
        fields=StringSchema(
            "JSON object of fields for create/update/log_activity/move_stage/invite/lines/convert_lead "
            "(name, firstName, amount, stage, title, email, qty, unitPrice, role, "
            "opportunityName, companyId, contactId, currency, expectedCloseDate, ownerId, ...)."
        ),
        required=["action"],
    )
)
class CrmTool(Tool):
    """Read and write the project CRM. Never invent deals or contacts."""

    _scopes = {"core", "subagent"}

    def __init__(self, *, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser()

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace)

    @property
    def name(self) -> str:
        return "crm"

    @property
    def description(self) -> str:
        return (
            "Shared Navin CRM (Studio #/crm). Same SQLite store as the workbench: "
            "companies, contacts, leads, opportunities, activities, products, line items, "
            "members, audit, company settings and currency. Never invent companies, contacts, amounts, stages, or KPIs: "
            "call this tool first. After a Prospection CSV hunt, write qualified rows here "
            "(create contacts/leads/companies) instead of only HubSpot or sales/crm/ files."
        )

    @property
    def read_only(self) -> bool:
        return False

    def call_read_only(self, arguments: Any) -> bool:
        action = str((arguments or {}).get("action") or "").strip().lower()
        if action == "settings":
            fields = str((arguments or {}).get("fields") or "").strip()
            return not fields
        return action in {
            "search",
            "get",
            "list",
            "dashboard",
            "insights",
            "timeline",
            "audit",
            "members",
            "products",
            "lines",
            "followups",
            "sync_status",
        }

    def _root(self) -> Path:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        return Path(access.project_path or self.workspace)

    def _fields(self, raw: str | None) -> dict[str, Any]:
        if not raw or not str(raw).strip():
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("fields must be a JSON object")
        return data

    def _run(
        self,
        action: str,
        kind: str | None,
        record_id: str | None,
        query: str | None,
        fields_raw: str | None,
    ) -> Any:
        root = self._root()
        fields = self._fields(fields_raw)
        actor = str(fields.get("actor") or fields.get("owner") or "")
        if action == "dashboard":
            return dashboard(root)
        if action == "search":
            return search_records(root, query or "")
        if action == "list":
            return {"records": list_records(root, kind or "contacts")}
        if action == "get":
            if (kind or "") == "settings":
                return get_settings(root)
            return get_record(root, kind or "contacts", record_id or "")
        if action == "settings":
            if fields:
                return update_settings(root, fields, actor=actor)
            return get_settings(root)
        if action == "create":
            return create_record(root, kind or "contacts", fields, actor=actor)
        if action == "update":
            return update_record(root, kind or "contacts", record_id or "", fields, actor=actor)
        if action == "delete":
            return delete_record(root, kind or "contacts", record_id or "", actor=actor)
        if action == "convert_lead":
            return convert_lead(
                root,
                record_id or "",
                owner=str(fields.get("owner") or ""),
                actor=actor,
                fields=fields,
            )
        if action == "log_activity":
            return create_record(root, "activities", fields, actor=actor)
        if action == "move_stage":
            stage = str(fields.get("stage") or "")
            return update_record(root, "opportunities", record_id or "", {"stage": stage}, actor=actor)
        if action == "insights":
            return compute_insights(root, kind or "opportunities", record_id or "")
        if action == "timeline":
            return {"records": compute_timeline(root, kind or "contacts", record_id or "")}
        if action == "audit":
            return {"records": list_audit(root, kind=kind or "", record_id=record_id or "")}
        if action == "invite":
            return invite_member(
                root,
                identity=str(fields.get("identity") or fields.get("email") or fields.get("handle") or ""),
                role=str(fields.get("role") or "member"),
                actor=actor,
                email=str(fields.get("email") or ""),
                handle=str(fields.get("handle") or ""),
                user_id=str(fields.get("userId") or ""),
            )
        if action == "accept_invite":
            return accept_invite(
                root,
                actor=actor or str(fields.get("identity") or ""),
                accept=fields.get("accept", True) is not False,
                invite_id=record_id or str(fields.get("id") or ""),
            )
        if action == "members":
            return list_members(root)
        if action == "products":
            if fields.get("name"):
                return create_record(root, "products", fields, actor=actor)
            return {"records": list_records(root, "products")}
        if action == "lines":
            if fields.get("opportunityId") and (fields.get("name") or fields.get("productId")):
                return create_record(root, "opportunity_lines", fields, actor=actor)
            return {"records": list_lines(root, record_id or str(fields.get("opportunityId") or ""))}
        if action == "followups":
            return due_followups(
                root,
                days=int(fields.get("days") or 7),
                create=bool(fields.get("create")),
                notify=bool(fields.get("notify")),
                actor=actor,
            )
        if action == "sync_status":
            return sync_status(root)
        raise ValueError(f"unknown action: {action}")

    async def execute(
        self,
        action: str,
        kind: str | None = None,
        id: str | None = None,  # noqa: A002
        query: str | None = None,
        fields: str | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            payload = await asyncio.to_thread(
                self._run, (action or "").strip().lower(), kind, id, query, fields
            )
        except CrmError as exc:
            return ToolResult.error(exc.message)
        except ValueError as exc:
            return ToolResult.error(str(exc))
        except json.JSONDecodeError:
            return ToolResult.error("fields must be valid JSON")
        return ToolResult(
            content=json.dumps(payload, ensure_ascii=False, default=str)[:12_000]
        )
