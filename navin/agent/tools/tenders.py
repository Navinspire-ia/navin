"""Agent tool over the same Navin Tenders store as Studio #/tenders."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import StringSchema, tool_parameters_schema
from navin.tenders.heartbeat import HEARTBEAT_TENDERS_ACTIONS
from navin.webui.tenders_api import normalize_tenders_action

_READ_ACTIONS = frozenset({"status", "snapshot", "get", "search", "list", "file"})
_TOOL_ACTIONS = (
    "status",
    "snapshot",
    "get",
    "search",
    "list",
    "index",
    "collect",
    "profile",
    "qualify",
    "write",
    "revise",
    "download",
    "stage",
    "mail",
    "send",
    "knowledge",
    "rescore",
    "follow",
    "watch",
    "crm-sync",
    "discover-accept",
    "notify",
    "retry-alerts",
    "upload",
    "file",
    "remove-file",
    "add-reference",
    "custom-source",
    "secret",
    "draft",
    "review",
    "remark",
    "remarks",
    "export",
    "pack",
    "score",
    "analyse",
    "gonogo",
    "follow-up",
    "crm",
    "crm_sync",
    "read-file",
    "read_file",
    "add_reference",
    "custom_source",
    "remove_file",
    "archive",
    "unarchive",
    "favorite",
    "unfavorite",
    "delete",
    "star",
    "unstar",
    "remove-notice",
    "start",
    "stop",
    "schedule",
    "tick",
)
def _normalize_action(raw: Any) -> str:
    return normalize_tenders_action(raw)


_PASSTHROUGH = (
    "host",
    "title",
    "detail",
    "url",
    "api",
    "data",
    "value",
    "filename",
    "to",
    "remarks",
    "client",
    "year",
    "amount",
    "remove",
    "op",
)


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Tenders desk action. status is the full local book. search/list/get read "
            "notices already on disk. collect pulls official APIs. qualify scores + "
            "Go/No-Go. write drafts the ready dossier and Word/PPT pack. "
            "revise applies reviewer remarks. download returns the generated pack. "
            "stage moves the pipeline. "
            "knowledge updates team/price_book/methodology. follow/watch is the silent heartbeat. "
            "start/stop/schedule/tick run the desk collect loop on a wall-clock "
            "calendar (same store as Studio #/tenders, Tauri, navin tenders, "
            "python -m navin.tenders.desk_cli). Never send a buyer mail from the loop. "
            "crm-sync pushes GO notices to the project CRM. "
            "discover-accept, notify, upload, file, remove-file, add-reference, custom-source and secret match the desk wizard. "
            "If Word/PPT models or references are on file, write must reuse their extracts.",
            enum=list(_TOOL_ACTIONS),
        ),
        id=StringSchema("Tender id (tn-...). Required for get, qualify, write, revise, download, stage, mail, send."),
        remarks=StringSchema("Reviewer remarks for action=revise. The writer applies them without inventing facts."),
        query=StringSchema("Search text across the local book (title, buyer, country, draft, score)."),
        stage=StringSchema(
            "Pipeline stage, or archived. Empty returns every stage. "
            "discovered, matched, scored, analysed, go, no-go, drafting, "
            "validating, submitted, clarification, shortlisted, negotiation, won, lost, archived."
        ),
        name=StringSchema("Company name for action=profile."),
        countries=StringSchema(
            "Comma ISO countries. Profile write, or search/list filter, e.g. FR,SA,AE."
        ),
        crafts=StringSchema("Comma crafts, e.g. AI,Data,Cloud."),
        tender_types=StringSchema("Comma tender types, e.g. Works,Services,Framework."),
        project_types=StringSchema("Comma completed project types, e.g. Data platform,TMA."),
        specialty=StringSchema("Company specialty for action=profile."),
        currency=StringSchema("ISO currency for action=profile, e.g. EUR."),
        source_ids=StringSchema("Comma official source ids for action=profile, e.g. ted,boamp."),
        wizard_complete=StringSchema("true to mark the wizard finished (action=profile)."),
        min_budget=StringSchema("Minimum budget for profile scoring, or for search/list."),
        min_deadline_days=StringSchema("Minimum days to deadline for scoring (action=profile)."),
        team=StringSchema("Comma team facts for action=knowledge."),
        price_book=StringSchema("Comma price-book facts for action=knowledge."),
        legal_clauses=StringSchema("Legal clauses text for action=knowledge."),
        min_score=StringSchema("Minimum score 0-100 for profile scoring, or for search/list."),
        send_mode=StringSchema("draft, approval (default) or autonomous."),
        kind=StringSchema("Mail draft kind, download kind (docx/pptx), or upload kind: word_template, ppt_template, reuse_slide, reference."),
        brief=StringSchema("Free-text profile notes, or extra search terms."),
        country=StringSchema("ISO country filter for search/list, e.g. FR."),
        sector=StringSchema("Domain or sector filter for search/list, e.g. Cloud,Works."),
        source_id=StringSchema("Official source id filter for search/list, e.g. ted,boamp."),
        buyer=StringSchema("Buyer name filter for search/list."),
        deadline_from=StringSchema("ISO date YYYY-MM-DD. Keep notices with deadline on or after."),
        deadline_to=StringSchema("ISO date YYYY-MM-DD. Keep notices with deadline on or before."),
        published_from=StringSchema("ISO date YYYY-MM-DD. Publication date from."),
        published_to=StringSchema("ISO date YYYY-MM-DD. Publication date to."),
        arrived_from=StringSchema("ISO date YYYY-MM-DD. Arrival fetched_at from."),
        arrived_to=StringSchema("ISO date YYYY-MM-DD. Arrival fetched_at to."),
        include_archived=StringSchema("search/list: true (default) includes archive. false is live only."),
        favorite=StringSchema("search/list: true only favorites, false only unstarred."),
        limit=StringSchema("search/list cap. 0 or all returns every match. Default is the full book."),
        max_score=StringSchema("Maximum score 0-100 for search/list."),
        max_budget=StringSchema("Maximum budget for search/list."),
        go=StringSchema("Filter search/list: true, false, go, no-go."),
        send=StringSchema("For follow/watch: false for a dry-run (no company digest)."),
        schedule=StringSchema(
            "JSON loop schedule for start/schedule: kind (daily, weekdays, "
            "weekend, weekly, monthly), hour, minute, weekday, day, tz."
        ),
        run_now=StringSchema("true to collect immediately when starting the loop."),
        tz=StringSchema("IANA timezone for the Tenders loop schedule."),
        force=StringSchema("true to collect now on action=tick, even if the next slot is later."),
        host=StringSchema("Official host to keep after collect (action=discover-accept)."),
        title=StringSchema("Notify title, or upload/reference title."),
        detail=StringSchema("Notify body for a channel test."),
        url=StringSchema("Official portal URL for action=custom-source."),
        api=StringSchema("Optional API endpoint for action=custom-source."),
        data=StringSchema("Base64 file payload for action=upload."),
        value=StringSchema("Secret value for action=secret (sam_gov or a custom key)."),
        filename=StringSchema("Original file name for action=upload."),
        to=StringSchema("Recipient email for action=send."),
    )
)
class TendersTool(Tool):
    """Find, qualify, answer and follow public tenders. Same store as Studio."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "tenders"

    @property
    def description(self) -> str:
        return (
            "Navin Tenders (Studio #/tenders). The live book is local "
            "(~/.navin/tenders/: profile.json, tenders.json, index.json, book.md). "
            "status returns every notice, the company dossier, knowledge and file paths. "
            "The chat may discuss the full book: live, favorite, archive, GO and no-go. "
            "search/list filter that book (country, sector, dates, score, budget) and "
            "default to every match including archive. get opens one notice in full. "
            "collect pulls official portals. qualify / write / revise / download / mail / stage follow the pipeline. "
            "Autonomous hunt is start/stop/schedule/tick (desk loop: same store "
            "as Studio #/tenders, Tauri, navin tenders, and "
            "python -m navin.tenders.desk_cli). "
            "follow is the heartbeat (follow/watch/status only on that session). "
            "Never collect, write, start, schedule or tick from heartbeat. "
            "Never send a buyer mail from the loop or from heartbeat. "
            "send in approval mode needs the desk click - this tool never forges approved=true. "
            "upload / file / remove-file / add-reference / custom-source / secret / notify / discover-accept "
            "are the same wizard actions as Studio. If models or references are on file, write must reuse them. "
            "Never invent a notice that is not in the store. "
            "LinkedIn MCP (Tools > Tenders MCP, stickerdaniel/linkedin-mcp-server) is the "
            "recommended buyer-research option after the user enables it and signs in: "
            "get_company_profile, get_company_employees, search_people, get_person_profile, "
            "get_feed, search_posts, inbox reads. Job tools are hiring context only, never a notice. "
            "Never invent a notice from LinkedIn. "
            "connect_with_person and send_message need confirm=true after the user agrees."
        )

    @property
    def read_only(self) -> bool:
        return False

    def call_read_only(self, arguments: Any) -> bool:
        return _normalize_action((arguments or {}).get("action")) in _READ_ACTIONS

    async def execute(self, **kwargs: Any) -> Any:
        from navin.tenders.errors import TenderError
        from navin.tenders.index import format_agent_status
        from navin.webui.tenders_api import handle_tenders_action

        action = _normalize_action(kwargs.get("action"))
        from navin.agent.tools.context import is_heartbeat_turn

        if is_heartbeat_turn() and action not in HEARTBEAT_TENDERS_ACTIONS:
            return ToolResult.error(
                "Refused on heartbeat. Tenders silent checks may only run "
                "status/snapshot/get/search/list/index/file/follow/watch. "
                "Collect, write, mail and send stay on the desk or a user chat."
            )
        body: dict[str, Any] = {
            "id": kwargs.get("id") or "",
            "stage": kwargs.get("stage") or "",
            "kind": kwargs.get("kind") or ("relance" if action == "mail" else ""),
            "query": kwargs.get("query") or "",
            "country": kwargs.get("country") or "",
            "go": kwargs.get("go") or "",
        }
        if kwargs.get("brief") and action in {"search", "list"} and not body["query"]:
            body["query"] = kwargs["brief"]
        if action in {"search", "list"}:
            for key in (
                "countries",
                "sector",
                "source_id",
                "buyer",
                "deadline_from",
                "deadline_to",
                "published_from",
                "published_to",
                "arrived_from",
                "arrived_to",
                "include_archived",
                "favorite",
                "limit",
                "min_score",
                "max_score",
                "min_budget",
                "max_budget",
            ):
                if kwargs.get(key) not in (None, ""):
                    body[key] = kwargs[key]
        if action == "profile":
            if kwargs.get("name"):
                body["name"] = kwargs["name"]
            if kwargs.get("countries"):
                body["countries"] = kwargs["countries"]
            if kwargs.get("crafts"):
                body["crafts"] = kwargs["crafts"]
            if kwargs.get("tender_types"):
                body["tender_types"] = kwargs["tender_types"]
            if kwargs.get("project_types"):
                body["project_types"] = kwargs["project_types"]
            for key in ("specialty", "currency", "source_ids", "send_mode"):
                if kwargs.get(key):
                    body[key] = kwargs[key]
            if kwargs.get("country"):
                body["country"] = kwargs["country"]
            raw_done = str(kwargs.get("wizard_complete") or "").strip().lower()
            if raw_done in {"1", "true", "yes", "on"}:
                body["wizard_complete"] = True
            if kwargs.get("min_score"):
                try:
                    body["min_score"] = float(kwargs["min_score"])
                except (TypeError, ValueError):
                    pass
            if kwargs.get("min_budget"):
                try:
                    body["min_budget"] = float(kwargs["min_budget"])
                except (TypeError, ValueError):
                    pass
            if kwargs.get("min_deadline_days"):
                try:
                    body["min_deadline_days"] = int(kwargs["min_deadline_days"])
                except (TypeError, ValueError):
                    pass
            if kwargs.get("brief"):
                body["brief"] = kwargs["brief"]
        if action == "knowledge":
            if kwargs.get("brief"):
                body["methodology"] = kwargs["brief"]
            if kwargs.get("name"):
                body["name"] = kwargs["name"]
            if kwargs.get("legal_clauses"):
                body["legal_clauses"] = kwargs["legal_clauses"]
            if kwargs.get("team"):
                body["team"] = [part.strip() for part in str(kwargs["team"]).split(",") if part.strip()]
            if kwargs.get("price_book"):
                body["price_book"] = [
                    part.strip() for part in str(kwargs["price_book"]).split(",") if part.strip()
                ]
        if action in {"secret", "custom-source", "upload"} and kwargs.get("name"):
            body["name"] = kwargs["name"]
        if action in {"follow", "watch"} and kwargs.get("send") not in (None, ""):
            body["send"] = kwargs.get("send")
        for key in _PASSTHROUGH:
            value = kwargs.get(key)
            if value not in (None, ""):
                body[key] = value
        if action == "upload" and kwargs.get("filename") and not body.get("name"):
            body["name"] = kwargs["filename"]
        if action in {"start", "schedule", "tick"}:
            body["run_now"] = str(kwargs.get("run_now") or "").strip().lower() in {"1", "true", "yes"}
            body["tz"] = str(kwargs.get("tz") or "").strip() or None
            body["force"] = (
                str(kwargs.get("force") or "true").strip().lower() in {"1", "true", "yes"}
                if action == "tick"
                else False
            )
            raw_schedule = str(kwargs.get("schedule") or "").strip()
            if raw_schedule:
                try:
                    parsed = json.loads(raw_schedule)
                except json.JSONDecodeError:
                    return ToolResult.error("schedule must be JSON")
                if isinstance(parsed, dict):
                    body["schedule"] = parsed
        if action == "send":
            # Desk HTTP may pass approved=true after a human click. The agent
            # schema has no approved field and must never forge one.
            body.pop("approved", None)
        try:
            payload = await asyncio.to_thread(handle_tenders_action, action, body)
        except TenderError as exc:
            return ToolResult.error(exc.message)
        if action == "status":
            return format_agent_status(payload)
        return payload
