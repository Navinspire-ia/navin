# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Campaigns tool: the agent runs email and WhatsApp campaigns and handles replies."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import StringSchema, tool_parameters_schema

_READ_ONLY = frozenset({
    "dashboard", "contacts", "lists", "templates", "campaigns", "campaign_get", "sends",
    "preview", "replies", "reply_get", "crm_preview",
})
# A heartbeat turn handles replies; it never launches or edits campaigns.
_HEARTBEAT_ACTIONS = frozenset({"dashboard", "replies", "reply_get", "reply_update", "reply_send", "campaign_get"})


@tool_parameters(tool_parameters_schema(
    action=StringSchema(
        "dashboard | contacts (q, list_id) | lists | list_create (name) | list_add (list_id, ids) | "
        "contact_save (fields) | templates | template_save (fields) | campaigns | campaign_get (id) | "
        "campaign_save (fields: name, channel email|whatsapp, subject, subject_variants [A/B subjects], preheader, "
        "html, list_ids, contact_ids, steps [follow-ups: delay_days, condition no_reply|no_open|always, subject, "
        "html], wa_template, wa_params...) | preview (id) | test (id, to) | schedule (id, schedule_at ISO or empty = now) | "
        "pause | resume | cancel (id) | sends (id) | crm_preview / crm_import (fields: kinds, filters, "
        "new_list_name) | prospects_search (fields: sector or keywords, countries, size_min, size_max, titles, "
        "domains, limit, find_people) | prospects_import (fields: search_id, indexes, only_with_email, "
        "new_list_name) | enrich (ids; fields: find_email, verify, company) | replies (status, classification) | reply_get (id) | reply_update (id, fields: "
        "classification, summary, handoff_brief, draft_reply, assigned_to, status) | reply_send (id, body)",
        enum=["dashboard", "contacts", "lists", "list_create", "list_add", "contact_save", "templates",
              "template_save", "campaigns", "campaign_get", "campaign_save", "preview", "test", "schedule",
              "pause", "resume", "cancel", "sends", "crm_preview", "crm_import", "prospects_search",
              "prospects_import", "enrich", "replies", "reply_get",
              "reply_update", "reply_send"],
    ),
    id=StringSchema("Campaign, reply or contact id."),
    fields=StringSchema("JSON object with the fields for save/update/crm actions."),
    q=StringSchema("Search text for contacts."),
    list_id=StringSchema("List id."),
    ids=StringSchema("Comma-separated contact ids for list_add."),
    to=StringSchema("Comma-separated test addresses or phone numbers for test."),
    schedule_at=StringSchema("ISO date-time for schedule; empty sends now."),
    status=StringSchema("Filter for replies (new, drafted, answered, handed_off, ignored) or sends."),
    classification=StringSchema("Filter for replies."),
    body=StringSchema("Answer text for reply_send."),
))
class CampaignsTool(Tool):
    """Real campaigns on Navin's own email (SMTP) and WhatsApp Cloud API setup."""

    _scopes = {"core", "teammate"}

    def __init__(self, workspace: str | None = None) -> None:
        self._workspace = workspace

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=getattr(ctx, "workspace", None))

    @property
    def name(self) -> str:
        return "campaigns"

    @property
    def description(self) -> str:
        return (
            "Campaigns Studio: contact lists (import, CRM, Leads), email templates, email and WhatsApp "
            "campaigns (preview, test, schedule, pause), sequences of threaded follow-ups stopped by replies, "
            "A/B subject tests, delivery stats per step and variant, and replies: classify them, write the "
            "hand-off brief, draft or send the answer. Launching (schedule) sends real messages to real people: "
            "only when the user asked for it, after a preview and a test."
        )

    def call_read_only(self, arguments: Any) -> bool:
        return isinstance(arguments, dict) and arguments.get("action", "dashboard") in _READ_ONLY

    async def execute(self, action: str = "dashboard", **kwargs: Any) -> Any:
        from navin.agent.tools.context import is_heartbeat_turn
        from navin.campaigns.store import CampaignError

        if is_heartbeat_turn() and action not in _HEARTBEAT_ACTIONS:
            return ToolResult.error(
                f"Error: campaigns action={action} does not run on the heartbeat; it only handles replies."
            )
        try:
            result = await asyncio.to_thread(self._run, action, kwargs)
        except CampaignError as exc:
            return ToolResult.error(f"Error: {exc.message}")
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")
        return ToolResult(content=json.dumps(result, ensure_ascii=False, default=str)[:14000])

    def _project(self) -> Any:
        from pathlib import Path

        from navin.security.workspace_access import current_tool_workspace

        try:
            scope = current_tool_workspace(self._workspace, restrict_to_workspace=True)
            return Path(scope.project_path) if scope and scope.project_path else None
        except Exception:  # noqa: BLE001 - no project bound: CRM actions say so
            return None

    def _run(self, action: str, args: dict[str, Any]) -> Any:
        from navin.campaigns import inbound, store
        from navin.campaigns.render import html_to_text
        from navin.webui.campaigns_api import handle_campaigns_action

        fields: dict[str, Any] = {}
        if args.get("fields"):
            parsed = json.loads(args["fields"]) if isinstance(args["fields"], str) else args["fields"]
            if not isinstance(parsed, dict):
                raise ValueError("fields must be a JSON object")
            fields = parsed
        ident = str(args.get("id") or "")
        simple = {
            "dashboard": ("dashboard", {}, {}),
            "contacts": ("contacts.list", {}, {"q": args.get("q") or "", "list_id": args.get("list_id") or "",
                                                "limit": 50}),
            "lists": ("lists.list", {}, {}),
            "list_create": ("lists.save", {"name": fields.get("name") or args.get("q") or "", **fields}, {}),
            "list_add": ("lists.add", {"list_id": args.get("list_id") or "",
                                       "contact_ids": [i.strip() for i in str(args.get("ids") or "").split(",")
                                                       if i.strip()]}, {}),
            "contact_save": ("contacts.save", fields, {}),
            "templates": ("templates.list", {}, {}),
            "template_save": ("templates.save", fields, {}),
            "campaigns": ("campaigns.list", {}, {}),
            "campaign_get": ("campaigns.get", {"id": ident}, {}),
            "campaign_save": ("campaigns.save", {**fields, **({"id": ident} if ident else {})}, {}),
            "preview": ("campaigns.preview", {"campaign": {"id": ident, **fields}}, {}),
            "test": ("campaigns.test", {"campaign": {"id": ident, **fields},
                                        "to": [t.strip() for t in str(args.get("to") or "").split(",")]}, {}),
            "schedule": ("campaigns.schedule", {"id": ident, "schedule_at": args.get("schedule_at") or ""}, {}),
            "pause": ("campaigns.pause", {"id": ident}, {}),
            "resume": ("campaigns.resume", {"id": ident}, {}),
            "cancel": ("campaigns.cancel", {"id": ident}, {}),
            "sends": ("campaigns.sends", {}, {"id": ident, "status": args.get("status") or "", "limit": 100}),
            "crm_preview": ("crm.preview", fields, {}),
            "crm_import": ("crm.import", fields, {}),
            "prospects_search": ("prospects.search", {"criteria": fields}, {}),
            "prospects_import": ("prospects.import", fields, {}),
            "enrich": ("contacts.enrich", {"ids": [i.strip() for i in str(args.get("ids") or "").split(",")
                                                   if i.strip()], **fields}, {}),
            "replies": ("replies.list", {}, {"status": args.get("status") or "",
                                              "classification": args.get("classification") or "", "limit": 30}),
            "reply_update": ("replies.update", {**fields, "id": ident}, {}),
        }
        if action == "reply_get":
            reply = store.get_reply(ident)
            campaign = store.get_campaign(reply["campaign_id"]) if reply["campaign_id"] else {}
            context = {}
            if campaign:
                context = {"name": campaign["name"], "subject": campaign["subject"], "channel": campaign["channel"],
                           "text": html_to_text(campaign.get("html") or "")[:3000]}
            contact = store.get_contact(reply["contact_id"]) if reply.get("contact_id") else {}
            thread = store.contact_thread(reply["contact_id"]) if reply.get("contact_id") else []
            rules = ("WhatsApp: free text only within 24 h of their message; keep it short and conversational."
                     if reply.get("channel") == "whatsapp" else "Email: reply in the same thread, plain and short.")
            return {"reply": reply, "campaign": context, "contact": contact, "thread": thread, "channel_rules": rules}
        if action == "reply_send":
            return inbound.send_reply(ident, str(args.get("body") or fields.get("body") or ""),
                                      str(fields.get("subject") or ""))
        if action not in simple:
            raise ValueError(f"unknown campaigns action {action!r}")
        name, body, query = simple[action]
        project = self._project() if action.startswith("crm_") else None
        return handle_campaigns_action(name, body, query, crm_project=project)
