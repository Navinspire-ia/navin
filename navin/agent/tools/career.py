# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool over the same Career store as Studio #/career."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import StringSchema, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Career desk action. status/dossier is a full local read of the CV, "
            "competence dossier, company, channels, pipeline. "
            "read returns one local file (cv, dossier, index, book, profile). "
            "profile writes those facts. search/find collect authorized sources. "
            "ingest/hits stores offers already listed in the UI or returned by "
            "LinkedIn MCP search_jobs (never fetch). "
            "import stores a pasted LinkedIn or closed-board offer (never fetch). "
            "match/rescore scores. prepare/cv/write tailors the master CV to that mission "
            "(reorder, keywords, cover, Word pack). download returns the .docx. "
            "watch reports new Perfect/Good matches and due follow-ups (heartbeat). "
            "start/stop/schedule/tick run the desk hunt loop on a wall-clock "
            "calendar (not heartbeat). apply opens a manual submission flow. "
            "mail_config saves SMTP/IMAP settings and separate explicit opt-ins; mail_test only checks the account. "
            "mail_draft previews the existing CV and cover; send_email sends that revision once, with a real SMTP receipt. "
            "sync_mail reads only replies threaded to these applications. Never automate LinkedIn.",
            enum=[
                "status",
                "dossier",
                "snapshot",
                "read",
                "book",
                "profile",
                "search",
                "collect",
                "ingest",
                "hits",
                "import",
                "paste",
                "match",
                "rescore",
                "qualify",
                "prepare",
                "cv",
                "write",
                "tailor",
                "download",
                "export",
                "apply",
                "mail_config",
                "mail_test",
                "mail_draft",
                "send_email",
                "sync_mail",
                "stage",
                "inbox",
                "classify",
                "followup",
                "find",
                "mission",
                "watch",
                "start",
                "stop",
                "schedule",
                "tick",
                "secret",
                "favorite",
                "unfavorite",
                "archive",
                "unarchive",
                "delete",
            ],
        ),
        id=StringSchema("Opportunity id (job-...) or a local file name for action=read."),
        file=StringSchema(
            "Local file for action=read: cv, dossier, index, book, profile, "
            "opportunities, applications, applications.md, inbox, journal."
        ),
        stage=StringSchema(
            "Pipeline stage: discovered, matched, ready, applied, replied, interview, offer, won, rejected."
        ),
        brief=StringSchema("Find-me-a-mission brief or search query."),
        track=StringSchema("freelance or jobs."),
        titles=StringSchema("Comma titles, e.g. Data Engineer, AI Engineer."),
        countries=StringSchema("Comma ISO countries for primary markets, e.g. FR,AE,SA."),
        countries_secondary=StringSchema("Comma ISO secondary markets, e.g. QA,KW,US."),
        countries_excluded=StringSchema("Comma ISO excluded markets, e.g. GB."),
        source_ids=StringSchema(
            "Comma Career source ids from setup, e.g. remotive,adzuna,linkedin. "
            "search/collect honor this list."
        ),
        stack=StringSchema("Comma stack, e.g. Python, Spark, AWS."),
        min_rate=StringSchema("Minimum daily rate for freelance."),
        max_rate=StringSchema("Maximum daily rate for freelance."),
        min_salary=StringSchema("Minimum yearly salary for jobs."),
        max_salary=StringSchema("Maximum yearly salary for jobs."),
        apply_mode=StringSchema("manual, review or autopilot."),
        display_name=StringSchema("Candidate or talent name."),
        headline=StringSchema("Headline."),
        email=StringSchema("Contact email."),
        application_email=StringSchema("Explicit recruiter application address published in the offer or provided by the user. Never guess it."),
        recipient=StringSchema("One explicit recruiter email for mail_draft/send_email."),
        revision=StringSchema("Exact revision returned by mail_draft; required by send_email."),
        reviewed=StringSchema("true only when the user has reviewed documents marked needs_review."),
        retry=StringSchema("true for an explicitly requested retry after a confirmed failure; uncertain sends are never retried."),
        kind=StringSchema("download/export: cv_docx (CV), cover_docx (letter) or docx (combined pack)."),
        phone=StringSchema("Phone, international format."),
        master_cv=StringSchema("Full master CV text. Facts only. Never invent."),
        strengths=StringSchema("Comma strengths to highlight."),
        weaknesses=StringSchema("Comma honest gaps."),
        highlights=StringSchema("Comma values / proof points."),
        prospect_email=StringSchema("Prospecting email draft."),
        work_mode=StringSchema("remote, hybrid, onsite or any."),
        residence=StringSchema("ISO residence country, e.g. FR."),
        currency=StringSchema("ISO currency, e.g. EUR."),
        visa=StringSchema("Visa / sponsorship note."),
        available_from=StringSchema("Availability date."),
        languages=StringSchema("Comma languages, e.g. fr, en."),
        payload=StringSchema(
            "JSON profile fields for action=profile. For mail_config: {mailbox:{enabled,sender_email,sender_name,"
            "smtp_host,smtp_port,smtp_security,smtp_username,imap_host,imap_port,imap_security,imap_username,"
            "imap_folder,read_replies,auto_send,min_match_score,max_per_day,poll_interval_minutes,allowed_recipient_domains},"
            "smtp_password,imap_password}. Read/auto-send opt-ins require explicit user authorization. "
            "Use the secure UI to enter passwords where possible."
        ),
        hits=StringSchema("JSON array of offers already listed (action=ingest). Never fetched."),
        via=StringSchema(
            "Ingest source. Use linkedin-mcp after mcp_linkedin_search_jobs / "
            "get_saved_jobs / get_job_details. Never fetch those URLs."
        ),
        body=StringSchema("Recruiter email text for action=inbox, or pasted job text for action=import."),
        url=StringSchema("Official job URL the user copied. Never fetched if LinkedIn or another closed board."),
        title=StringSchema("Pasted job title for action=import."),
        company=StringSchema("Pasted company name for action=import."),
        wave=StringSchema("Follow-up wave: j3 or j7."),
        schedule=StringSchema(
            "JSON loop schedule for start/schedule: kind (daily, weekdays, "
            "weekend, weekly, monthly), hour, minute, weekday, day, tz."
        ),
        run_now=StringSchema("true to hunt immediately when starting the loop."),
        tz=StringSchema("IANA timezone for the Career loop schedule."),
        force=StringSchema("true to hunt now on action=tick, even if the next slot is later."),
        name=StringSchema(
            "Secret name for action=secret: ADZUNA_APP_ID, ADZUNA_APP_KEY, "
            "JOOBLE_API_KEY, USAJOBS_API_KEY, USAJOBS_USER_AGENT, JOBOPPORTUNITIES_API_KEY, "
            "CAREER_SMTP_PASSWORD, CAREER_IMAP_PASSWORD."
        ),
        value=StringSchema("Secret value for action=secret. Empty deletes it."),
    )
)
class CareerTool(Tool):
    """Freelance + permanent job desk. Same store as Studio Career."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "career"

    @property
    def description(self) -> str:
        return (
            "Navin Career (Studio #/career). The local book is the source of truth: "
            "CV, strengths, gaps, projects, company, talents, channels, pipeline. "
            "Call status or dossier first to load every stored fact. "
            "status lists every stored offer: live, favorite and archive. "
            "Studio filters are UI-only and never hide an offer from this chat. "
            "read returns cv.md / dossier.md / book.md / profile.json from disk. "
            "Search/collect/find/loop use the same store: profile.source_ids plus "
            "local secrets.json (wizard action=secret) or process env. "
            "Search always runs remotive, published ATS, web search (company "
            "careers + public ATS boards), scrape on open hosts, and LinkedIn "
            "official portals. Adzuna, Jooble and USAJOBS are extra and run only "
            "when that source is enabled and a key is on file. Never invent a "
            "scrape for a keyed API. "
            "After mcp_linkedin_search_jobs / get_saved_jobs, call career ingest "
            "(via=linkedin-mcp) so those jobs enter the book. Never scrape. "
            "Preferred markets (primary/secondary/excluded) change the match score. "
            "Autonomous hunt is start/stop/schedule/tick (desk loop: same store "
            "as Studio #/career, Tauri, navin career, and "
            "python -m navin.career.desk_cli). "
            "watch is the silent heartbeat. Professional mail uses mail_config/mail_test, then mail_draft and "
            "send_email for an explicitly authorized reviewed application; sync_mail retrieves threaded responses. "
            "Report accepted only from mail_receipt.status=accepted, preserving Message-ID. An enabled account "
            "or legacy autopilot mode does not authorize auto_send. Never scrape or auto-apply on LinkedIn."
        )

    @property
    def read_only(self) -> bool:
        return False

    def call_read_only(self, arguments: Any) -> bool:
        action = str((arguments or {}).get("action") or "").strip().lower()
        return action in {"status", "dossier", "snapshot", "read", "book"}

    async def execute(self, **kwargs: Any) -> Any:
        from navin.career.dossier import format_agent_status
        from navin.career.errors import CareerError
        from navin.webui.career_api import handle_career_action

        action = str(kwargs.get("action") or "").strip().lower()
        from navin.agent.tools.context import is_heartbeat_turn
        from navin.career.heartbeat import HEARTBEAT_CAREER_ACTIONS

        if is_heartbeat_turn() and action not in HEARTBEAT_CAREER_ACTIONS:
            return ToolResult.error(
                "Refused on heartbeat. Career silent checks may only run "
                "status/dossier/snapshot/read/book/watch. "
                "Collect, search and apply stay on the desk or a user chat."
            )
        body: dict[str, Any] = {}
        if action == "profile":
            raw_payload = str(kwargs.get("payload") or "").strip()
            if raw_payload:
                try:
                    parsed = json.loads(raw_payload)
                except json.JSONDecodeError:
                    return ToolResult.error("payload must be JSON")
                if isinstance(parsed, dict):
                    body.update(parsed)
            mapping = {
                "titles": "titles",
                "countries": "countries_primary",
                "countries_secondary": "countries_secondary",
                "countries_excluded": "countries_excluded",
                "stack": "stack",
                "apply_mode": "apply_mode",
                "track": "track",
                "display_name": "display_name",
                "headline": "headline",
                "email": "email",
                "phone": "phone",
                "master_cv": "master_cv",
                "strengths": "strengths",
                "weaknesses": "weaknesses",
                "highlights": "highlights",
                "prospect_email": "prospect_email",
                "work_mode": "work_mode",
                "residence": "residence_country",
                "currency": "currency",
                "visa": "visa",
                "available_from": "available_from",
                "languages": "languages",
                "source_ids": "source_ids",
            }
            for src, dest in mapping.items():
                if kwargs.get(src):
                    body[dest] = kwargs[src]
            for money in ("min_rate", "max_rate", "min_salary", "max_salary"):
                if not kwargs.get(money):
                    continue
                try:
                    body[money] = float(kwargs[money])
                except (TypeError, ValueError):
                    pass
        elif action in {"read", "book"}:
            body["file"] = kwargs.get("file") or kwargs.get("id") or "book"
        elif action == "secret":
            body = {"name": kwargs.get("name") or "", "value": kwargs.get("value") or ""}
        elif action == "mail_config":
            try:
                parsed = json.loads(str(kwargs.get("payload") or "{}"))
            except json.JSONDecodeError:
                return ToolResult.error("payload must be JSON")
            if not isinstance(parsed, dict) or not isinstance(parsed.get("mailbox"), dict):
                return ToolResult.error("payload.mailbox must be an object")
            body = parsed
        elif action in {"ingest", "hits"}:
            raw_hits = str(kwargs.get("hits") or kwargs.get("payload") or "").strip()
            if raw_hits:
                try:
                    parsed = json.loads(raw_hits)
                except json.JSONDecodeError:
                    return ToolResult.error("hits must be JSON")
                if isinstance(parsed, list):
                    body["jobs"] = parsed
                elif isinstance(parsed, dict):
                    body.update(parsed)
            if kwargs.get("track"):
                body["track"] = kwargs["track"]
            if kwargs.get("via"):
                body["via"] = str(kwargs["via"]).strip()
        else:
            body = {
                "id": kwargs.get("id") or "",
                "stage": kwargs.get("stage") or "",
                "brief": kwargs.get("brief") or "",
                "query": kwargs.get("brief") or "",
                "track": kwargs.get("track") or "",
                "body": kwargs.get("body") or "",
                "url": kwargs.get("url") or "",
                "title": kwargs.get("title") or "",
                "company": kwargs.get("company") or "",
                "application_email": kwargs.get("application_email") or "",
                "recipient": kwargs.get("recipient") or "",
                "revision": kwargs.get("revision") or "",
                "reviewed": str(kwargs.get("reviewed") or "").strip().lower() == "true",
                "retry": str(kwargs.get("retry") or "").strip().lower() == "true",
                "kind": kwargs.get("kind") or "docx",
                "wave": kwargs.get("wave") or "j3",
                "run_now": str(kwargs.get("run_now") or "").strip().lower() in {"1", "true", "yes"},
                "tz": str(kwargs.get("tz") or "").strip() or None,
                "force": str(kwargs.get("force") or "true").strip().lower() in {"1", "true", "yes"}
                if action == "tick"
                else False,
            }
            raw_schedule = str(kwargs.get("schedule") or "").strip()
            if raw_schedule:
                try:
                    parsed = json.loads(raw_schedule)
                except json.JSONDecodeError:
                    return ToolResult.error("schedule must be JSON")
                if isinstance(parsed, dict):
                    body["schedule"] = parsed
        try:
            mapped = {
                "dossier": "status",
                "book": "read",
                "qualify": "match",
                "rescore": "match",
                "hits": "ingest",
                "mission": "find",
                "cv": "prepare",
                "classify": "inbox",
            }.get(action, action)
            payload = await asyncio.to_thread(handle_career_action, mapped, body)
        except CareerError as exc:
            return ToolResult.error(exc.message)
        if action in {"status", "dossier"}:
            return format_agent_status(payload)
        if action in {"read", "book"}:
            return str(payload.get("text") or "")
        return payload
