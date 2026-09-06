"""Leads tool: turn raw prospect rows into CRM-ready, deduped, exportable data.

Prospecting breadth stays with the agent (search / scrape / directories); this
tool is the deterministic backend that normalizes fields, enforces a schema,
merges duplicates, qualifies tiers, and writes atomic csv/json/jsonl/xlsx. It
gives the leads module the same "real engine" footing that scrape and seo have,
instead of leaving data quality to the model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.path_utils import project_rooted_path
from navin.agent.tools.schema import StringSchema, tool_parameters_schema
from navin.config_base import Base
from navin.leads.engine import (
    dedupe_leads,
    export_leads,
    normalize_leads,
    qualify_tier,
    summarize_leads,
)
from navin.leads.schema import CANONICAL_FIELDS, validate_lead
from navin.security.workspace_access import current_tool_workspace
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path

_EXPORT_FORMATS = ("csv", "json", "jsonl", "xlsx")


class LeadsToolConfig(Base):
    """Leads engine configuration."""

    enabled: bool = True
    # Default country hint used when normalizing national phone numbers.
    default_country: str | None = None


def _parse_records(records: str | None) -> list[dict[str, Any]]:
    if not records or not records.strip():
        raise ValueError("records JSON is required")
    data = json.loads(records)
    if isinstance(data, dict) and "leads" in data:
        data = data["leads"]
    if isinstance(data, dict) and "records" in data:
        data = data["records"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("records must be a JSON array of lead objects")
    return [dict(row) for row in data if isinstance(row, dict)]


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Desk actions (same store as Studio #/leads): status/snapshot, "
            "profile, hunt/search, enrich, watch (heartbeat), rescore (BANT-F), "
            "sequence (j0/j3/j7 with localized drafts), draft/redraft (rewrite a step), "
            "sequences (draft every due step; sends only in autonomous mode), "
            "optout (email or domain, stops every sequence), lookalike, stage, crm, delete, keys, "
            "start/stop/schedule/tick (desk loop, not heartbeat), "
            "outreach/send (email, whatsapp, telegram, teams). "
            "Engine actions on raw rows: normalize, validate, "
            "dedupe, qualify, export, pipeline.",
            enum=[
                "status",
                "snapshot",
                "profile",
                "setup",
                "hunt",
                "search",
                "discover",
                "enrich",
                "watch",
                "follow",
                "rescore",
                "score",
                "start",
                "stop",
                "schedule",
                "tick",
                "sequence",
                "cadence",
                "draft",
                "redraft",
                "sequences",
                "optout",
                "lookalike",
                "peers",
                "stage",
                "crm",
                "push",
                "delete",
                "remove",
                "keys",
                "secrets",
                "outreach",
                "send",
                "message",
                "normalize",
                "validate",
                "dedupe",
                "qualify",
                "export",
                "pipeline",
            ],
        ),
        id=StringSchema("Lead id for enrich, stage, crm, sequence, draft, lookalike or delete."),
        stage=StringSchema("Pipeline stage: new, qualified, contacted, replied, meeting, opportunity, won."),
        icp_name=StringSchema("ICP name for hunt/profile."),
        sector=StringSchema("Sector for hunt/profile."),
        countries=StringSchema("Comma ISO countries, e.g. FR,GB."),
        cities=StringSchema("Comma cities to focus the hunt on (empty = main cities of each country)."),
        keywords=StringSchema("Comma extra search keywords (niche, product, certification)."),
        signals=StringSchema("Comma job titles a prospect hires when it needs the offer (hiring signal)."),
        offer=StringSchema("One line on what you sell; drives the outreach drafts."),
        sender_name=StringSchema("Signature name for the drafts."),
        execution_mode=StringSchema(
            "approval (loop drafts and waits) or autonomous (loop sends due steps within daily_send_cap).",
            enum=["approval", "autonomous"],
        ),
        daily_send_cap=StringSchema("Max emails the autonomous loop may send per day."),
        titles=StringSchema("Comma roles, e.g. CEO, CTO, Head of Data."),
        size_min=StringSchema("Minimum employee count."),
        size_max=StringSchema("Maximum employee count."),
        count=StringSchema("How many companies to hunt."),
        step=StringSchema("Sequence step (1, 2 or 3) for draft/redraft; 0 or omit for every unsent step."),
        value=StringSchema("Email address or domain for optout."),
        payload=StringSchema("JSON object merged into the ICP profile or provider keys."),
        records=StringSchema(
            "JSON array of lead objects (or {leads:[...]}). Fields: company, "
            "website/domain, person, role, email, phone, country, sector, size, "
            "signal, source, confidence, linkedin_url."
        ),
        format=StringSchema(
            "Export format: csv, json, jsonl, xlsx.", enum=list(_EXPORT_FORMATS)
        ),
        path=StringSchema(
            "Workspace-relative output path for export/pipeline (e.g. sales/leads.csv)."
        ),
        country=StringSchema(
            "ISO-2 country hint (e.g. FR, US) for national phone normalization."
        ),
        channel=StringSchema(
            "Outreach channel: email, whatsapp, telegram or teams.",
            enum=["email", "whatsapp", "telegram", "teams"],
        ),
        to=StringSchema("Destination: email address, WhatsApp number, Telegram chat id or Teams id."),
        subject=StringSchema("Email subject or short title."),
        body=StringSchema("Message body. Required for a real send."),
        send=StringSchema("true to send now, false or omit to prepare a draft."),
        schedule=StringSchema(
            "JSON loop schedule for start/schedule: kind (daily, weekdays, "
            "weekend, weekly, monthly), hour, minute, weekday, day, tz."
        ),
        run_now=StringSchema("true to hunt immediately when starting the loop."),
        tz=StringSchema("IANA timezone for the Leads loop schedule."),
        force=StringSchema("true to hunt now on action=tick, even if the next slot is later."),
        required=["action"],
    )
)
class LeadsTool(Tool):
    """Waterfall prospecting desk plus deterministic CSV engine."""

    _scopes = {"core", "subagent"}
    config_key = "leads"
    _ENGINE_ACTIONS = frozenset(
        {"normalize", "validate", "dedupe", "qualify", "export", "pipeline"}
    )
    _READ_ACTIONS = frozenset({"status", "snapshot"})

    @classmethod
    def config_cls(cls):
        return LeadsToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.leads.enabled

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace, config=ctx.config.leads)

    def __init__(self, *, workspace: str | Path, config: LeadsToolConfig) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config

    @property
    def name(self) -> str:
        return "leads"

    @property
    def description(self) -> str:
        return (
            "Navin Leads (Studio #/leads). The local book is the source of truth "
            "(~/.navin/leads/: profile.json, leads.json). "
            "Call status or snapshot first. hunt runs the waterfall "
            "(SIRENE / Companies House / OpenCorporates, public LinkedIn job listings as "
            "hiring signals, localized web search with list-page mining, OpenStreetMap, "
            "then Apollo / Places / Crunchbase). Each hunt rotates cities and angles. "
            "enrich fills missing fields (Pappers, Places details, PDL, Apollo, Hunter, "
            "public /about /contact scrape, pattern email guess with MX check). "
            "Stop when a field is filled. "
            "rescore writes BANT-F evidence, why and the next action. "
            "sequence starts a 3-step cold cadence (j0 / j3 / j7) with drafts in the "
            "prospect's language; draft/redraft rewrites a step; sequences drafts every due "
            "step and sends only when profile.execution_mode=autonomous (daily_send_cap, "
            "opt-out list). optout stops a contact or a whole domain for good. "
            "Autonomous hunt is start/stop/schedule/tick (desk loop: same store "
            "as Studio #/leads, Tauri, navin leads, and "
            "python -m navin.leads.desk_cli). "
            "watch is the silent heartbeat. Never hunt, start or tick from heartbeat. "
            "lookalike hunts peers of a seed company. "
            "Never scrape LinkedIn. LinkedIn is a deep-link only. "
            "outreach/send uses the connected Email / WhatsApp / Telegram / Teams "
            "channels (Settings > Channels). Heartbeat never sends outreach. "
            "normalize/validate/dedupe/qualify/export/pipeline still clean raw rows "
            "the agent scraped. Prefer the desk book over a one-off CSV."
        )

    @property
    def read_only(self) -> bool:
        return False

    def call_read_only(self, arguments: Any) -> bool:
        action = str((arguments or {}).get("action") or "").strip().lower()
        return action in self._READ_ACTIONS

    def _resolve_out_path(self, raw: str) -> Path:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_allowed_path(
                project_rooted_path(raw, workspace, [access.allowed_root]),
                workspace=workspace,
                allowed_root=access.allowed_root,
                strict=False,
            )
        except WorkspaceBoundaryError as exc:
            raise ValueError(
                f"path must stay inside the project ({access.allowed_root})"
            ) from exc
        return Path(resolved)

    async def execute(
        self,
        action: str,
        records: str | None = None,
        format: str | None = None,  # noqa: A002 - tool schema name
        path: str | None = None,
        country: str | None = None,
        **kwargs: Any,
    ) -> Any:
        action = (action or "").strip().lower()
        from navin.agent.tools.context import is_heartbeat_turn
        from navin.leads.heartbeat import HEARTBEAT_LEADS_ACTIONS

        if is_heartbeat_turn() and action not in HEARTBEAT_LEADS_ACTIONS:
            return ToolResult.error(
                "Refused on heartbeat. Leads silent checks may only run "
                "status/snapshot/watch/follow/rescore. Hunt, start, schedule, tick, "
                "enrich, sequence, lookalike, keys, CRM and outreach stay on the desk."
            )
        if action in self._ENGINE_ACTIONS:
            try:
                return await asyncio.to_thread(
                    self._run, action, records, format, path, country
                )
            except ValueError as exc:
                return ToolResult.error(f"Error: {exc}")
            except Exception as exc:  # noqa: BLE001
                return ToolResult.error(f"Error: leads failed: {exc}")
        return await asyncio.to_thread(self._run_desk, action, kwargs)

    def _run_desk(self, action: str, kwargs: dict[str, Any]) -> Any:
        from navin.leads.desk import format_agent_status
        from navin.leads.errors import LeadsError
        from navin.webui.leads_api import handle_leads_action

        body: dict[str, Any] = {}
        raw_payload = str(kwargs.get("payload") or "").strip()
        if raw_payload:
            try:
                parsed = json.loads(raw_payload)
            except json.JSONDecodeError:
                return ToolResult.error("payload must be JSON")
            if isinstance(parsed, dict):
                body.update(parsed)
        for key in (
            "id",
            "stage",
            "icp_name",
            "sector",
            "countries",
            "titles",
            "size_min",
            "size_max",
            "count",
            "cities",
            "keywords",
            "signals",
            "offer",
            "sender_name",
            "execution_mode",
            "daily_send_cap",
            "step",
            "value",
            "channel",
            "to",
            "subject",
            "body",
            "send",
        ):
            if kwargs.get(key):
                body[key] = kwargs[key]
        raw_schedule = str(kwargs.get("schedule") or "").strip()
        if raw_schedule:
            try:
                parsed = json.loads(raw_schedule)
            except json.JSONDecodeError:
                return ToolResult.error("schedule must be JSON")
            if isinstance(parsed, dict):
                body["schedule"] = parsed
        if str(kwargs.get("run_now") or "").strip():
            body["run_now"] = str(kwargs.get("run_now")).strip().lower() in {"1", "true", "yes"}
        if str(kwargs.get("tz") or "").strip():
            body["tz"] = str(kwargs.get("tz")).strip()
        if action == "tick":
            body["force"] = str(kwargs.get("force") or "true").strip().lower() in {
                "1",
                "true",
                "yes",
            }
        try:
            payload = handle_leads_action(action, body)
        except LeadsError as exc:
            return ToolResult.error(exc.message)
        if action in {"status", "snapshot"}:
            return format_agent_status(payload)
        return payload

    def _run(
        self,
        action: str,
        records: str | None,
        fmt: str | None,
        path: str | None,
        country: str | None,
    ) -> str:
        default_country = (country or self.config.default_country) or None

        if action not in ("normalize", "validate", "dedupe", "qualify", "export", "pipeline"):
            return ToolResult.error(
                "Error: unknown action. Use normalize, validate, dedupe, qualify, "
                "export, or pipeline."
            )

        rows = _parse_records(records)
        leads = normalize_leads(rows, default_country=default_country)

        if action == "normalize":
            return json.dumps(
                {"leads": [x.to_row() for x in leads], "count": len(leads)},
                ensure_ascii=False,
                indent=2,
            )

        if action == "validate":
            report = []
            for lead in leads:
                issues = validate_lead(lead)
                report.append(
                    {
                        "company": lead.company,
                        "email": lead.email,
                        "issues": issues,
                        "crm_ready": not issues,
                    }
                )
            ok = sum(1 for r in report if r["crm_ready"])
            return json.dumps(
                {"rows": report, "crm_ready": ok, "total": len(report)},
                ensure_ascii=False,
                indent=2,
            )

        if action == "dedupe":
            unique, removed = dedupe_leads(leads)
            return json.dumps(
                {
                    "leads": [x.to_row() for x in unique],
                    "count": len(unique),
                    "removed": removed,
                },
                ensure_ascii=False,
                indent=2,
            )

        if action == "qualify":
            for lead in leads:
                lead.tier = qualify_tier(lead)
            return json.dumps(
                {
                    "leads": [x.to_row() for x in leads],
                    "summary": summarize_leads(leads),
                },
                ensure_ascii=False,
                indent=2,
            )

        # export / pipeline both write a file.
        if not path:
            return ToolResult.error("Error: path is required for export/pipeline")
        out_format = (fmt or "csv").lower()
        if action == "pipeline":
            leads, removed = dedupe_leads(leads)
            for lead in leads:
                lead.tier = qualify_tier(lead)
        else:
            removed = 0
        out_path = self._resolve_out_path(path)
        info = export_leads(leads, out_format, out_path)
        payload: dict[str, Any] = {
            "export": {"path": info.path, "format": info.format, "count": info.count},
            "summary": summarize_leads(leads),
            "fields": list(CANONICAL_FIELDS),
        }
        if action == "pipeline":
            payload["deduped_removed"] = removed
        return json.dumps(payload, ensure_ascii=False, indent=2)
