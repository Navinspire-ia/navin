"""Wiring and behavior for the Navin Tenders studio module."""

from __future__ import annotations

import ast
import asyncio
import base64
import datetime as dt
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from navin.agent.loop import AgentLoop
from navin.agent.model_routes import (
    PRODUCT_MODULE_ROUTE_ROLES,
    WORKFLOW_ROUTE_ROLES,
    product_module_role,
    workflow_role_for_content,
)
from navin.agent.tools.loader import ToolLoader
from navin.agent.tools.sandbox import writable_host_paths
from navin.agent.tools.tenders import TendersTool
from navin.config.paths import get_runtime_subdir
from navin.command.builtin import (
    _DELIVERY_WORKFLOWS,
    _HTML_REPORT_WORKFLOWS,
    _TRACKED_WORKFLOWS,
    _WORKFLOW_BRIEFS,
    BUILTIN_COMMAND_SPECS,
    builtin_command_palette,
)
from navin.agent.tools.context import RequestContext, is_heartbeat_turn, request_context
from navin.command.modules import (
    CODE_HIDDEN_COMMANDS,
    HEARTBEAT_DENIED_TOOLS,
    VALID_PRODUCT_MODULES,
    default_preload_skills_for_module,
    disabled_skills_for_module,
    exclusive_studio_skills,
    extra_denied_tools_for_module,
    extra_preload_skills_for_module,
    is_command_allowed_for_module,
    kept_tools_for_module,
)
from navin.tenders.ai import (
    ask,
    keeps_only_known_facts,
    polish_mail,
    polish_response,
    routing_snapshot,
    task_preset,
    task_role,
)
from navin.tenders.collect import _country_code, collect
from navin.tenders.errors import TenderError
from navin.tenders.normalize import host_of, iso_date, looks_like_notice, normalize_tender, tender_id
from navin.tenders.score import score_tender
from navin.tenders.scrape_net import (
    host_is_official,
    parse_search_hits,
    scrape_official_net,
)
from navin.tenders.sources import (
    catalog,
    coverage_holes,
    official_hosts,
    sources_for_countries,
    web_search_queries,
)
from navin.tenders.desk import revise_one, snapshot, write_one
from navin.tenders.enrich import enrich_notice, fetch_text_is_noise, html_to_text, notice_is_thin
from navin.tenders.index import format_dossier, search_notices, write_local_index
from navin.tenders.needs import (
    TENDER_TYPES,
    expand_need_terms,
    lookup_need,
    official_cpv_divisions,
    public_needs_catalog,
)
from navin.tenders.profile import filed_documents, normalize_templates, wizard_ready
from navin.tenders.stack import (
    LINKEDIN_MCP_CONFIRM,
    LINKEDIN_MCP_JOBS,
    LINKEDIN_MCP_OFFICIAL,
    LINKEDIN_MCP_TOOLS,
    TENDERS_MCP,
    TENDERS_REPORT_SKILLS,
    TENDERS_SKILLS,
    TENDERS_TOOLS,
    module_stack,
)
from navin.tenders.heartbeat import profile_is_armed, tick_watch
from navin.tenders.normalize import SEND_MODES
from navin.tenders.store import TenderStore, default_channels, default_profile, extract_office_text
from navin.agent.tool_surface import tool_name_is_denied
from navin.webui.mcp_presets_api import (
    MCP_PRESETS,
    _materialize_server,
    mcp_deny_prefixes,
    mcp_servers_denied_for_module,
)
from navin.tenders.writer import (
    analyse_tender,
    build_response,
    commercial_draft,
    extract_requirements,
    go_nogo,
)
from navin.webui.tenders_api import (
    HEARTBEAT_TENDERS_ACTIONS,
    _watch_should_send,
    handle_tenders_action,
    normalize_tenders_action,
)

ROOT = Path(__file__).resolve().parents[1]
EM_DASH = "\u2014"
EN_DASH = "\u2013"


def _tenders_ui_source() -> str:
    folder = ROOT / "webui/src/components/studio/tenders"
    parts = [path.read_text(encoding="utf-8") for path in sorted(folder.rglob("*.tsx")) if ".test." not in path.name]
    return "\n".join(parts)


def _score_profile() -> dict:
    profile = default_profile()
    profile["crafts"] = ["AI", "Data", "Cloud", "Digital"]
    profile["countries"] = ["FR", "AE", "SA", "QA", "KW", "MA", "TN", "SN", "CI", "US", "CA", "GB"]
    profile["min_budget"] = 50_000
    profile["min_deadline_days"] = 10
    profile["min_score"] = 70
    return profile


def _notice(**overrides: object) -> dict:
    raw = {
        "title": "Modernisation du SI decisionnel",
        "description": "Refonte du SI decisionnel et de la plateforme analytics.",
        "country": "FR",
        "buyer": "DINUM",
        "deadline": "2099-12-31",
        "publication_date": "2026-01-15",
        "budget": 180_000,
        "source_url": "https://ted.europa.eu/en/notice/-/detail/2026-123456",
        "reference": "2026-123456",
        "eligibility": "ISO 27001, three references",
    }
    raw.update(overrides)
    return normalize_tender(raw, source_id="ted")


def _filled_profile() -> dict:
    profile = _score_profile()
    profile.update(
        {
            "name": "Acme Digital",
            "legal_name": "Acme Digital SAS",
            "specialty": "Architecture data et Cloud",
            "strengths": ["ISO 27001", "API", "lac de donnees"],
            "country": "FR",
            "phone": "+33 1 23 45 67 89",
            "email": "ao@acme.example",
            "website": "https://acme.example",
            "currency": "EUR",
            "crafts": ["Data", "Cloud", "Architecture", "API"],
            "tender_types": ["MOE", "AMOA"],
            "project_types": ["lac de donnees", "API"],
            "certifications": ["ISO 27001"],
            "partners": [{"name": "Hexa Cloud", "role": "hebergement", "country": "FR"}],
            "sites": [{"kind": "hq", "city": "Paris", "country": "FR"}],
            "headcount": 42,
            "turnover": 2_400_000,
            "methodology": "Cadrage, conception, realisation, recette. Atelier Cloud puis recette.",
            "legal_clauses": "CCAG TIC, confidentialite.",
            "team": [
                {"role": "Chef de projet", "name": "Sam Leroy"},
                {"role": "Architecte", "name": "Lea Martin"},
            ],
            "price_book": [{"item": "JH architecte", "amount": "950", "unit": "EUR"}],
            "references": [
                {
                    "title": "Lac de donnees ministere",
                    "client": "DINUM",
                    "year": "2024",
                    "country": "FR",
                }
            ],
            "ai_assist": False,
        }
    )
    return profile


def _rich_notice() -> dict:
    notice = _notice(
        title="Architecture data et API pour le lac de donnees ministeriel",
        description=(
            "Le prestataire livrera une architecture data, un lac de donnees, des API REST "
            "et un hebergement Cloud. Volumes : 12 sources. Stack acheteur : PostgreSQL. "
            "Duree de mission : 9 mois, charge 420 jours-homme. "
            "Comite hebdomadaire de pilotage. Offre au meilleur rapport qualite-prix."
        ),
        eligibility="ISO 27001, trois references, offre financiere",
        budget=420_000,
    )
    notice["cdc_text"] = (
        "Cahier des charges.\n\n"
        "Architecture : lac de donnees, API REST, hebergement Cloud, PostgreSQL.\n\n"
        "Volumes : 12 sources a integrer.\n\n"
        "Duree de mission : 9 mois. Charge : 420 jours-homme.\n\n"
        "Gouvernance : comite hebdomadaire de pilotage.\n\n"
        "Livrables : schemas, documentation, transfert."
    )
    notice["score"] = 88
    notice["score_breakdown"] = {"days_left": 40, "technical": 80}
    return notice


class TendersProductModuleTest(unittest.TestCase):
    def test_module_registered(self) -> None:
        self.assertIn("tenders", VALID_PRODUCT_MODULES)
        self.assertIn("/tenders", CODE_HIDDEN_COMMANDS)
        self.assertTrue(is_command_allowed_for_module("/tenders", "tenders"))
        self.assertFalse(is_command_allowed_for_module("/tenders", "code"))
        self.assertIn("/tenders", _HTML_REPORT_WORKFLOWS)
        self.assertIn("/tenders", _DELIVERY_WORKFLOWS)
        self.assertIn("/tenders", _TRACKED_WORKFLOWS)

    def test_palette_and_brief(self) -> None:
        specs = {spec.command: spec for spec in BUILTIN_COMMAND_SPECS}
        self.assertIn("/tenders", specs)
        title, skills, brief = _WORKFLOW_BRIEFS["/tenders"]
        self.assertIn("Tenders", title)
        self.assertIn("tender-agent", skills)
        self.assertIn("tenders", brief)
        self.assertIn("action=search", brief)
        self.assertIn("action=get", brief)
        self.assertIn("navin tenders", brief)
        self.assertIn("action=start", brief)
        self.assertIn("action=stop", brief)
        self.assertIn("Do not create a chat cron", brief)
        self.assertIn("Never collect, write, start, schedule or tick from heartbeat", brief)
        palette = {row["command"] for row in builtin_command_palette("tenders")}
        self.assertIn("/tenders", palette)
        self.assertIn("/scrape", palette)
        self.assertNotIn("/trading", palette)
        exclusive = exclusive_studio_skills()
        self.assertIn("tender-agent", exclusive.get("tenders", set()))
        self.assertTrue((ROOT / "navin/skills/tender-agent/SKILL.md").is_file())
        self.assertIn("tender-agent", default_preload_skills_for_module("tenders"))
        self.assertIn("scrape-operator", default_preload_skills_for_module("tenders"))
        self.assertIn("web-extractor", default_preload_skills_for_module("tenders"))
        self.assertIn("proposal-writer", default_preload_skills_for_module("tenders"))
        self.assertIn("action=follow", brief)
        self.assertIn("tenders action=follow", brief)
        self.assertIn("product_module=tenders", brief)
        self.assertTrue(is_command_allowed_for_module("/scrape", "tenders"))
        self.assertIn("tender-agent", disabled_skills_for_module("code"))
        self.assertNotIn("tender-agent", disabled_skills_for_module("tenders"))

    def test_tool_is_discovered(self) -> None:
        names = {cls.__name__ for cls in ToolLoader().discover()}
        self.assertIn("TendersTool", names)


class TendersAgentWiringTest(unittest.TestCase):
    def test_loop_routes_and_keeps_tenders_tools(self) -> None:
        self.assertEqual(WORKFLOW_ROUTE_ROLES.get("/tenders"), "deep")
        self.assertEqual(PRODUCT_MODULE_ROUTE_ROLES.get("tenders"), "deep")
        self.assertEqual(product_module_role("tenders"), "deep")
        denied = extra_denied_tools_for_module("tenders")
        self.assertEqual(denied, frozenset({"career", "trading", "leads", "marketing"}))
        locked = AgentLoop._locked_denied_tools(None, {"product_module": "tenders"})
        self.assertIn("career", locked)
        self.assertIn("trading", locked)
        self.assertNotIn("tenders", locked)
        self.assertNotIn("scrape", locked)
        self.assertNotIn("web_search", locked)
        live = AgentLoop._denied_tools(None, {"product_module": "tenders"})
        self.assertNotIn("scrape", live)
        self.assertNotIn("web_search", live)
        self.assertTrue(is_command_allowed_for_module("/tenders", "tenders"))
        self.assertTrue(is_command_allowed_for_module("/scrape", "tenders"))
        self.assertFalse(is_command_allowed_for_module("/career", "tenders"))
        self.assertNotIn("linkedin", mcp_servers_denied_for_module("career"))
        self.assertNotIn("linkedin", mcp_servers_denied_for_module("tenders"))
        self.assertNotIn("mcp_linkedin_", mcp_deny_prefixes("career"))
        self.assertNotIn("mcp_linkedin_", mcp_deny_prefixes("tenders"))
        self.assertFalse(tool_name_is_denied("mcp_linkedin_get_company_profile", mcp_deny_prefixes("career")))
        self.assertFalse(tool_name_is_denied("mcp_linkedin_get_company_profile", mcp_deny_prefixes("tenders")))
        self.assertTrue(tool_name_is_denied("mcp_linkedin-ads_list_accounts", mcp_deny_prefixes("career")))
        self.assertFalse(tool_name_is_denied("mcp_linkedin-ads_list_accounts", mcp_deny_prefixes("ads")))
        self.assertIn("linkedin", mcp_servers_denied_for_module("notes"))
        self.assertIn("linkedin-ads", mcp_servers_denied_for_module("notes"))
        self.assertIn("linkedin-ads", mcp_servers_denied_for_module("career"))
        self.assertNotIn("linkedin-ads", mcp_servers_denied_for_module("ads"))
        career_locked = AgentLoop._locked_denied_tools(None, {"product_module": "career"})
        self.assertFalse(tool_name_is_denied("mcp_linkedin_send_message", career_locked))
        tenders_locked = AgentLoop._locked_denied_tools(None, {"product_module": "tenders"})
        self.assertFalse(tool_name_is_denied("mcp_linkedin_send_message", tenders_locked))

    def test_preload_matches_stack_and_brief(self) -> None:
        preload = default_preload_skills_for_module("tenders")
        self.assertEqual(preload, list(TENDERS_SKILLS))
        _title, skills, brief = _WORKFLOW_BRIEFS["/tenders"]
        for name in TENDERS_SKILLS:
            self.assertIn(name, skills, name)
            self.assertTrue((ROOT / f"navin/skills/{name}/SKILL.md").is_file(), name)
        self.assertIn("tenders action=follow", brief)
        self.assertIn("tenders action=collect", brief)
        exclusive = exclusive_studio_skills()
        self.assertIn("tender-agent", exclusive.get("tenders", set()))
        self.assertIn("rfp-writer", exclusive.get("tenders", set()))
        self.assertIn("proposal-writer", exclusive.get("tenders", set()))
        extras = extra_preload_skills_for_module("tenders")
        self.assertEqual(extras, list(TENDERS_REPORT_SKILLS))
        self.assertIn("studio-html-report", extras)
        loop = AgentLoop.__new__(AgentLoop)
        chat = SimpleNamespace(metadata={"product_module": "tenders"})
        injected = loop._preload_skills_for_message(chat)
        self.assertIsNotNone(injected)
        for name in (*TENDERS_SKILLS, *TENDERS_REPORT_SKILLS):
            self.assertIn(name, injected, name)

    def test_tool_exposes_every_desk_action(self) -> None:
        tool = TendersTool()
        enum = tool.parameters["properties"]["action"]["enum"]
        for action in (
            "status",
            "collect",
            "follow",
            "watch",
            "discover-accept",
            "notify",
            "upload",
            "file",
            "remove-file",
            "add-reference",
            "custom-source",
            "secret",
            "crm-sync",
            "draft",
            "follow-up",
            "read-file",
            "archive",
            "unarchive",
            "favorite",
            "unfavorite",
            "delete",
            "start",
            "stop",
            "schedule",
            "tick",
        ):
            self.assertIn(action, enum, action)
        self.assertTrue(tool.call_read_only({"action": "status"}))
        self.assertTrue(tool.call_read_only({"action": "read-file"}))
        self.assertTrue(tool.call_read_only({"action": "read_file"}))
        self.assertFalse(tool.call_read_only({"action": "follow"}))
        self.assertIn("navin tenders", tool.description)
        self.assertIn("python -m navin.tenders.desk_cli", tool.description)
        self.assertIn("Never collect, write, start, schedule or tick from heartbeat", tool.description)
        self.assertFalse(tool.call_read_only({"action": "index"}))
        self.assertFalse(tool.call_read_only({"action": "draft"}))
        self.assertFalse(tool.call_read_only({"action": "discover-accept"}))

    def test_sandbox_can_write_tenders_runtime_dir(self) -> None:
        tenders_dir = get_runtime_subdir("tenders").resolve()
        paths = writable_host_paths(str(tempfile.gettempdir()))
        resolved = []
        for item in paths:
            try:
                resolved.append(Path(item).resolve())
            except (OSError, RuntimeError, ValueError):
                continue
        self.assertIn(tenders_dir, resolved)
        sandbox = (ROOT / "navin/agent/tools/sandbox.py").read_text(encoding="utf-8")
        self.assertIn("navin career|tenders|trading", sandbox)
        linkedin_mcp = Path.home() / ".linkedin-mcp"
        self.assertTrue(linkedin_mcp.is_dir())
        self.assertIn(linkedin_mcp.resolve(), resolved)

    def test_sandbox_falls_back_to_home_navin_desks(self) -> None:
        home_tenders = Path.home() / ".navin" / "tenders"
        with patch("navin.config.paths.get_runtime_subdir", side_effect=OSError("no instance")):
            paths = writable_host_paths(str(tempfile.gettempdir()))
        resolved = []
        for item in paths:
            try:
                resolved.append(Path(item).resolve())
            except (OSError, RuntimeError, ValueError):
                continue
        self.assertTrue(home_tenders.is_dir())
        self.assertIn(home_tenders.resolve(), resolved)
        self.assertIn((Path.home() / ".linkedin-mcp").resolve(), resolved)

    def test_heartbeat_loop_mcp_and_stack_are_the_same_desk(self) -> None:
        stack = module_stack()
        self.assertEqual(stack["tool"], "tenders")
        self.assertEqual(stack["desk"], "#/tenders")
        self.assertEqual(stack["skills"], list(TENDERS_SKILLS))
        self.assertEqual(stack["tools"], list(TENDERS_TOOLS))
        self.assertEqual({row["id"] for row in TENDERS_MCP}, {"exa", "linkedin"})
        self.assertEqual(TENDERS_MCP[0]["id"], "linkedin")
        self.assertTrue(TENDERS_MCP[0]["recommended"])
        self.assertEqual(len(LINKEDIN_MCP_OFFICIAL), 19)
        self.assertEqual(set(LINKEDIN_MCP_OFFICIAL), set(LINKEDIN_MCP_TOOLS) | set(LINKEDIN_MCP_JOBS) | set(LINKEDIN_MCP_CONFIRM))
        by_name = {preset.name: preset for preset in MCP_PRESETS}
        self.assertIn("tenders", by_name["exa"].modules)
        self.assertIn("career", by_name["exa"].modules)
        linkedin = by_name["linkedin"]
        self.assertEqual(linkedin.modules, ("tenders", "career"))
        self.assertEqual(linkedin.fields, ())
        self.assertEqual(linkedin.server.command, "uvx")
        self.assertIn("mcp-server-linkedin@latest", linkedin.server.args)
        self.assertEqual(linkedin.server.env.get("UV_HTTP_TIMEOUT"), "300")
        self.assertEqual(linkedin.server.tool_timeout, 180)
        self.assertFalse(linkedin.auto_enable)
        self.assertIn("career", linkedin.modules)
        self.assertIn("github.com/stickerdaniel/linkedin-mcp-server", linkedin.docs_url)
        self.assertIn("Recommended option", linkedin.description)
        self.assertIn("--login", linkedin.note)
        self.assertIn("--import-from-browser", linkedin.note)
        self.assertIn("get_feed", linkedin.note)
        self.assertIn("close_session", linkedin.note)
        self.assertIn("search_jobs", linkedin.note)
        self.assertNotEqual(linkedin.name, "linkedin-ads")
        materialized = _materialize_server(linkedin, {}, None)
        self.assertEqual(materialized.command, "uvx")
        self.assertIn("mcp-server-linkedin@latest", materialized.args)
        li_mcp = next(row for row in stack["mcp"] if row["id"] == "linkedin")
        self.assertEqual(stack["mcp"][0]["id"], "linkedin")
        self.assertTrue(li_mcp["recommended"])
        self.assertEqual(li_mcp["official"], list(LINKEDIN_MCP_OFFICIAL))
        self.assertEqual(li_mcp["tools"], list(LINKEDIN_MCP_TOOLS))
        self.assertEqual(li_mcp["jobs"], list(LINKEDIN_MCP_JOBS))
        self.assertIn("get_company_profile", li_mcp["tools"])
        self.assertIn("get_sidebar_profiles", li_mcp["tools"])
        self.assertIn("get_feed", li_mcp["tools"])
        self.assertIn("close_session", li_mcp["tools"])
        self.assertIn("search_jobs", li_mcp["jobs"])
        self.assertIn("send_message", li_mcp["confirm"])
        self.assertIn("--login", li_mcp["login"])
        self.assertIn("company", li_mcp["groups"])
        self.assertIn("scrapling", stack["skills"])
        connectors = {row["id"]: row for row in stack["connectors"]}
        self.assertEqual(connectors["linkedin"]["ingest"], "mcp_session")
        self.assertFalse(connectors["linkedin"]["live"])
        self.assertTrue(connectors["linkedin"]["recommended"])
        self.assertEqual(connectors["exa"]["ingest"], "mcp")
        heartbeat = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        template = (ROOT / "navin/templates/HEARTBEAT.md").read_text(encoding="utf-8")
        skill = (ROOT / "navin/skills/tender-agent/SKILL.md").read_text(encoding="utf-8")
        monitor = (ROOT / "navin/skills/tender-monitor/SKILL.md").read_text(encoding="utf-8")
        en = (ROOT / "webui/src/i18n/locales/en/common.json").read_text(encoding="utf-8")
        fr = (ROOT / "webui/src/i18n/locales/fr/common.json").read_text(encoding="utf-8")
        for body in (heartbeat, template, skill, monitor):
            self.assertIn("tenders action=follow", body)
        preamble = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("HEARTBEAT_OK", preamble)
        self.assertNotIn("'All clear.'", preamble)
        self.assertIn("product_module=tenders", skill)
        self.assertIn("Tools > Tenders MCP", skill)
        self.assertIn("get_company_employees", skill)
        self.assertIn("get_feed", skill)
        self.assertIn("close_session", skill)
        self.assertIn("search_jobs", skill)
        self.assertIn("scrapling", skill)
        self.assertIn("confirm=true", skill)
        self.assertIn("Do not create a chat cron", skill)
        self.assertIn("## Wiring", skill)
        self.assertIn("navin tenders", skill)
        self.assertIn("python -m navin.tenders.desk_cli", skill)
        self.assertIn("tenders action=start", skill)
        self.assertIn("Never collect", skill)
        self.assertIn("navin tenders", monitor)
        self.assertIn("tenders action=start", monitor)
        self.assertNotIn("collect then follow", skill)
        self.assertNotIn("knowledge-base", skill)
        self.assertIn("tenders action=knowledge", skill)
        self.assertIn("10. Keep", skill)
        self.assertIn("linkedin-mcp-server", monitor)
        self.assertIn("Do not create a chat cron", monitor)
        self.assertIn("crm.sqlite", template)
        self.assertIn("tenders action=collect", en)
        self.assertIn("tenders action=follow", en)
        self.assertIn("tendersMcp", en)
        self.assertIn("tendersMcpHint", en)
        self.assertIn("mcpRecommended", en)
        self.assertIn("mcpWizardBody", en)
        self.assertIn("stickerdaniel/linkedin-mcp-server", en)
        self.assertIn("tenders action=collect", fr)
        self.assertIn("tenders action=follow", fr)
        self.assertIn("kpiNew", en)
        self.assertIn("kpiNew", fr)
        self.assertIn("sendAutoHint", en)
        self.assertIn("sendAutoHint", fr)
        self.assertIn("methodologyHint", en)
        self.assertIn("methodologyHint", fr)
        self.assertIn("previewExtract", en)
        self.assertIn("previewExtract", fr)
        self.assertIn("loadingExtract", en)
        self.assertIn("loadingExtract", fr)
        self.assertIn("tendersMcp", fr)
        self.assertIn("tendersMcpHint", fr)
        self.assertIn("mcpRecommended", fr)
        self.assertIn("mcpWizardBody", fr)
        for name in TENDERS_TOOLS:
            self.assertIn(name, stack["tools"])
        loop_src = (ROOT / "navin/agent/loop.py").read_text(encoding="utf-8")
        self.assertNotIn("TENDERS_TOOLS", loop_src)
        stack_src = (ROOT / "navin/tenders/stack.py").read_text(encoding="utf-8")
        self.assertIn("not a closed allowlist", stack_src)
        cli = subprocess.run(
            [sys.executable, "-m", "navin.webui.mcp_presets_cli", "list"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(cli.returncode, 0, cli.stderr)
        catalog = json.loads(cli.stdout)
        names = {row["name"] for row in catalog.get("presets") or []}
        self.assertIn("linkedin", names)
        self.assertIn("exa", names)
        linkedin_row = next(row for row in catalog["presets"] if row["name"] == "linkedin")
        self.assertEqual(linkedin_row.get("modules"), ["tenders", "career"])

    def test_linkedin_mcp_policy_matches_official_catalog(self) -> None:
        from navin.agent.tools.mcp import (
            _LINKEDIN_CONFIRM_TOOLS,
            _LINKEDIN_JOB_TOOLS,
            linkedin_mcp_tool_description,
            linkedin_mcp_tool_parameters,
            linkedin_write_refusal,
        )

        self.assertEqual(_LINKEDIN_CONFIRM_TOOLS, frozenset(LINKEDIN_MCP_CONFIRM))
        self.assertEqual(_LINKEDIN_JOB_TOOLS, frozenset(LINKEDIN_MCP_JOBS))
        outreach = linkedin_mcp_tool_description("linkedin", "send_message", "Send a message")
        self.assertIn("confirm=true", outreach)
        self.assertIsNone(
            linkedin_write_refusal(
                "linkedin",
                "send_message",
                confirm=True,
                user_text="Oui, envoie le message.",
            )
        )
        self.assertIn(
            "without confirmation",
            linkedin_write_refusal("linkedin", "send_message", confirm=False, user_text="Oui") or "",
        )
        self.assertIn(
            "last message",
            linkedin_write_refusal(
                "linkedin",
                "send_message",
                confirm=True,
                user_text="Look up the buyer company page.",
            )
            or "",
        )
        self.assertIn(
            "heartbeat",
            linkedin_write_refusal(
                "linkedin",
                "send_message",
                confirm=True,
                user_text="Oui, envoie.",
                heartbeat=True,
            )
            or "",
        )
        self.assertIn(
            "heartbeat",
            linkedin_write_refusal(
                "linkedin",
                "connect_with_person",
                confirm=True,
                user_text="You are executing periodic heartbeat tasks.",
            )
            or "",
        )
        self.assertIn(
            "last message",
            linkedin_write_refusal(
                "linkedin",
                "send_message",
                confirm=True,
                user_text="go",
            )
            or "",
        )
        session = SimpleNamespace(called=False)

        async def _call_tool(name: str, arguments: dict) -> None:
            session.called = True
            raise AssertionError(f"LinkedIn write reached the server: {name}")

        session.call_tool = _call_tool
        from navin.agent.tools.mcp import MCPToolWrapper

        wrapper = MCPToolWrapper(
            session,
            "linkedin",
            SimpleNamespace(
                name="send_message",
                description="Send a message",
                inputSchema={"type": "object", "properties": {}},
            ),
        )
        refused = asyncio.run(wrapper.execute(confirm=True, message="hello"))
        self.assertTrue(getattr(refused, "is_error", False))
        self.assertFalse(session.called)
        jobs = linkedin_mcp_tool_description("linkedin", "search_jobs", "Search jobs")
        self.assertIn("never treat this as a public notice", jobs)
        self.assertIn("career action=ingest via=linkedin-mcp", jobs)
        saved = linkedin_mcp_tool_description("linkedin", "get_saved_jobs", "Saved jobs")
        self.assertIn("career action=ingest via=linkedin-mcp", saved)
        company = linkedin_mcp_tool_description(
            "linkedin", "get_company_profile", "Company page"
        )
        self.assertIn("buyer research", company)
        self.assertEqual(
            linkedin_mcp_tool_description("exa", "web_search_exa", "Search the web"),
            "Search the web",
        )
        schema = linkedin_mcp_tool_parameters(
            "linkedin",
            "send_message",
            {"type": "object", "properties": {"message": {"type": "string"}}},
        )
        self.assertEqual(schema["properties"]["confirm"]["type"], "boolean")
        plain = linkedin_mcp_tool_parameters(
            "linkedin",
            "get_company_profile",
            {"type": "object", "properties": {}},
        )
        self.assertNotIn("confirm", plain["properties"])

    def test_agent_send_never_forges_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme", "send_mode": "approval"})
            notice = _notice(reference="SEND-AGENT")
            store.save_tenders([notice])
            with patch("navin.webui.tenders_api._store", return_value=store):
                handle_tenders_action("mail", {"id": notice["id"], "kind": "clarification"})
                tool = TendersTool()
                result = asyncio.run(
                    tool.execute(action="send", id=notice["id"], to="buyer@example.com")
                )
                forged = asyncio.run(
                    tool.execute(
                        action="send",
                        id=notice["id"],
                        to="buyer@example.com",
                        approved=True,
                    )
                )
            self.assertTrue(getattr(result, "is_error", False) or "approval" in str(result).lower())
            self.assertIn("approval", str(result).lower())
            self.assertTrue(getattr(forged, "is_error", False) or "approval" in str(forged).lower())
            self.assertIn("approval", str(forged).lower())
            row = store.get(notice["id"])
            sent = [item for item in (row.get("mail") or []) if item.get("sent")]
            self.assertFalse(sent)

    def test_heartbeat_turn_refuses_collect_and_send(self) -> None:
        from navin.agent.tools.context import RequestContext, request_context

        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme", "send_mode": "approval"})
            notice = _notice(reference="HB-1")
            store.save_tenders([notice])
            tool = TendersTool()
            ctx = RequestContext(
                channel="telegram",
                chat_id="1",
                session_key="heartbeat",
                metadata={"heartbeat": True},
            )
            with patch("navin.webui.tenders_api._store", return_value=store):
                with request_context(ctx):
                    collect_result = asyncio.run(tool.execute(action="collect"))
                    send_result = asyncio.run(
                        tool.execute(action="send", id=notice["id"], to="buyer@example.com")
                    )
                    write_result = asyncio.run(tool.execute(action="write", id=notice["id"]))
                    mail_result = asyncio.run(
                        tool.execute(action="mail", id=notice["id"], kind="relance")
                    )
                    profile_result = asyncio.run(tool.execute(action="profile", name="X"))
                    notify_result = asyncio.run(tool.execute(action="notify", title="x"))
                    crm_result = asyncio.run(tool.execute(action="crm-sync"))
                    stage_result = asyncio.run(
                        tool.execute(action="stage", id=notice["id"], stage="submitted")
                    )
                    knowledge_result = asyncio.run(tool.execute(action="knowledge", brief="x"))
                    secret_result = asyncio.run(
                        tool.execute(action="secret", name="sam_gov", value="k")
                    )
                    qualify_result = asyncio.run(tool.execute(action="qualify", id=notice["id"]))
                    follow_result = asyncio.run(tool.execute(action="follow"))
                    status_result = asyncio.run(tool.execute(action="status"))
            for result, label in (
                (collect_result, "collect"),
                (send_result, "send"),
                (write_result, "write"),
                (mail_result, "mail"),
                (profile_result, "profile"),
                (notify_result, "notify"),
                (crm_result, "crm-sync"),
                (stage_result, "stage"),
                (knowledge_result, "knowledge"),
                (secret_result, "secret"),
                (qualify_result, "qualify"),
            ):
                self.assertTrue(getattr(result, "is_error", False), label)
                self.assertIn("heartbeat", str(result).lower(), label)
            self.assertFalse(getattr(follow_result, "is_error", False))
            self.assertIn("watch", follow_result)
            self.assertFalse(getattr(status_result, "is_error", False))
            with patch("navin.webui.tenders_api._store", return_value=store):
                with request_context(ctx):
                    with patch("navin.tenders.watch.deliver_alert") as deliver:
                        dry = asyncio.run(tool.execute(action="follow", send="false"))
            self.assertFalse(getattr(dry, "is_error", False))
            deliver.assert_not_called()
            loop_src = (ROOT / "navin/agent/loop.py").read_text(encoding="utf-8")
            self.assertIn('if session_key == "heartbeat":', loop_src)
            self.assertIn('metadata["heartbeat"] = True', loop_src)

    def test_agent_profile_can_finish_wizard_scoring_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with patch("navin.webui.tenders_api._store", return_value=store):
                tool = TendersTool()
                snap = asyncio.run(
                    tool.execute(
                        action="profile",
                        name="Atelier",
                        country="FR",
                        countries="FR,SA",
                        crafts="Cloud,Data",
                        specialty="Data platforms",
                        currency="EUR",
                        source_ids="ted,boamp",
                        wizard_complete="true",
                        min_budget="50000",
                        min_deadline_days="12",
                        min_score="65",
                    )
                )
            profile = store.load_profile()
            self.assertTrue(wizard_ready(profile))
            self.assertEqual(profile["country"], "FR")
            self.assertEqual(profile["currency"], "EUR")
            self.assertEqual(profile["specialty"], "Data platforms")
            self.assertEqual(profile["min_budget"], 50000.0)
            self.assertEqual(profile["min_deadline_days"], 12)
            self.assertEqual(profile["min_score"], 65)
            self.assertIn("ted", profile["source_ids"])
            self.assertEqual(snap["profile"]["name"], "Atelier")

    def test_agent_knowledge_accepts_team_and_price_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with patch("navin.webui.tenders_api._store", return_value=store):
                tool = TendersTool()
                asyncio.run(
                    tool.execute(
                        action="knowledge",
                        brief="Design then build.",
                        team="Aymen, architect",
                        price_book="TMA 650 EUR",
                        legal_clauses="No joint venture.",
                    )
                )
            profile = store.load_profile()
            self.assertEqual(profile["methodology"], "Design then build.")
            self.assertEqual(profile["team"], ["Aymen", "architect"])
            self.assertEqual(profile["price_book"], ["TMA 650 EUR"])
            self.assertEqual(profile["legal_clauses"], "No joint venture.")

    def test_agent_tool_reaches_wizard_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with patch("navin.webui.tenders_api._store", return_value=store):
                tool = TendersTool()
                snap = asyncio.run(tool.execute(action="snapshot"))
                self.assertEqual(snap["stack"]["tool"], "tenders")
                accepted = asyncio.run(tool.execute(action="discover-accept", host="example.gov"))
                self.assertTrue(
                    any(row.get("host") == "example.gov" for row in accepted.get("discoveries") or [])
                    or accepted.get("profile") is not None
                )
                notified = asyncio.run(
                    tool.execute(action="notify", title="Navin Tenders", detail="wiring check")
                )
                self.assertIn("notify", notified)
                custom = asyncio.run(
                    tool.execute(
                        action="custom-source",
                        name="Lab API",
                        url="https://example.gov/tenders",
                    )
                )
                self.assertTrue(custom["profile"].get("custom_sources"))

    def test_shell_opens_tenders_first(self) -> None:
        app = (ROOT / "webui/src/App.tsx").read_text(encoding="utf-8")
        sidebar = (ROOT / "webui/src/components/Sidebar.tsx").read_text(encoding="utf-8")
        self.assertIn('path === "/tenders"', app)
        self.assertIn('params.get("notice")', app)
        self.assertIn("tenderNotice", app)
        self.assertIn("TendersWorkspace", app)
        self.assertIn('"tenders"', sidebar)
        self.assertLess(sidebar.find('"tenders"'), sidebar.find('"trading"'))
        self.assertLess(sidebar.find('"tenders"'), sidebar.find('"leads"'))
        self.assertIn("onOpenTendersStudio", sidebar)
        self.assertIn("useExternalLinkOpener", app)
        http = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn("async def _handle_tenders", http)
        self.assertIn('if exc.message != "missing file content"', http)
        api = (ROOT / "webui/src/lib/tenders-api.ts").read_text(encoding="utf-8")
        self.assertIn('apiBodyHeaders("{}")', api)
        self.assertIn('TENDERS_DESK_HASH = "#/tenders"', api)
        self.assertIn("officialTenderHref", api)
        desk = _tenders_ui_source()
        self.assertIn('type View = "work" | "setup"', desk)
        self.assertIn('setView("work")', desk)
        self.assertIn('setView("setup")', desk)
        self.assertIn("TendersWizard", desk)
        self.assertIn("FileLibrary", desk)
        self.assertIn("NeedPicker", desk)
        self.assertIn("needs_catalog", desk)
        self.assertIn("pinElementToScrollStart", desk)
        self.assertIn("data-zone-header", desk)
        self.assertIn("pickFiles", desk)
        self.assertIn("add-reference", desk)
        self.assertIn("wizard_ready", desk)
        self.assertIn("TenderKpiGrid", desk)
        self.assertIn("buildTenderKpis", desk)
        workspace = (ROOT / "webui/src/components/studio/tenders/TendersWorkspace.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("live.kpis.open", workspace)
        self.assertIn("live.kpis.qualified", workspace)
        self.assertIn("live.kpis.new", workspace)
        self.assertIn('postTenders(token, "file"', workspace)
        self.assertIn("if (!token || !fileId)", workspace)
        self.assertNotIn("live.tenders.length", workspace)
        kpis = (ROOT / "webui/src/components/studio/tenders/TendersKpis.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-testid="tenders-kpi-grid"', kpis)
        self.assertNotIn("motion.", kpis)
        self.assertIn('run("collect"', desk)
        self.assertIn("function coverageLabel", desk)
        self.assertIn("function accessLabel", desk)
        self.assertIn("function SourcesTable", desk)
        self.assertIn("scrape + web search", desk)
        self.assertIn("function OfficialLink", desk)
        self.assertIn("openInOsBrowser", desk)
        self.assertNotIn("window.open", (ROOT / "webui/src/components/studio/tenders/tenders-ui.tsx").read_text(encoding="utf-8"))
        opener_hook = (ROOT / "webui/src/hooks/useExternalLinkOpener.ts").read_text(encoding="utf-8")
        self.assertIn("openInOsBrowser", opener_hook)
        self.assertIn('action.kind === "internal"', opener_hook)
        self.assertIn("window.location.hash", opener_hook)
        self.assertNotIn("window.open", opener_hook)
        urls = (ROOT / "webui/src/lib/external-url.ts").read_text(encoding="utf-8")
        self.assertIn("export function ideNavigationHash", urls)
        markdown = (ROOT / "webui/src/components/MarkdownTextRenderer.tsx").read_text(encoding="utf-8")
        self.assertIn("ideNavigationHash", markdown)
        self.assertIn("data-open-url", desk)
        self.assertIn("MODELS_HASH", desk)
        self.assertIn("openInIdeHash", desk)
        self.assertIn("send_mode: sendMode", desk)
        self.assertIn("discover-accept", desk)
        self.assertIn("function CollectReport", desk)
        self.assertIn("function DiscoveriesPane", desk)
        tauri = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn('http://tauri.localhost/#/tenders', tauri)
        self.assertIn("tauri://localhost/#/tenders", tauri)
        self.assertIn("tauri://localhost/#/tenders?notice=abc", tauri)
        self.assertIn("official_tender_portals_leave_the_ide", tauri)
        self.assertIn("every_catalog_url_leaves_the_ide", tauri)
        self.assertIn(".on_navigation({", tauri)
        self.assertIn(".on_new_window(move |url, _features|", tauri)
        self.assertIn("open_external_in_os_browser", tauri)
        self.assertIn("apply_in_app_navigation", tauri)
        self.assertIn("in_app_hash", tauri)
        self.assertIn("window.location.hash", tauri)
        opener = (ROOT / "desktop/src-tauri/capabilities/gateway-opener.json").read_text(
            encoding="utf-8"
        )
        self.assertIn('"linux"', opener)
        self.assertIn('"macOS"', opener)
        self.assertIn('"windows"', opener)
        self.assertIn("http://tauri.localhost", opener)
        self.assertIn("http://asset.localhost", opener)
        self.assertIn("tauri://localhost", opener)
        self.assertIn('"url": "https://**"', opener)
        self.assertIn("opener:allow-open-url", opener)
        catalog_urls = (ROOT / "webui/src/lib/tender-catalog-urls.json").read_text(encoding="utf-8")
        self.assertIn('"ted"', catalog_urls)
        self.assertIn("https://ted.europa.eu/", catalog_urls)
        electron = (ROOT / "desktop-electron/main.js").read_text(encoding="utf-8")
        self.assertIn("tauri.localhost", electron)
        self.assertIn('"tauri:"', electron)
        self.assertIn("setWindowOpenHandler", electron)
        self.assertIn("will-navigate", electron)
        self.assertIn("openExternal", electron)
        self.assertIn("window.location.hash", electron)
        self.assertIn("parsed.hash", electron)
        desktop = (ROOT / "webui/src/lib/desktop.ts").read_text(encoding="utf-8")
        self.assertIn("isInternalDesktopUrl", desktop)
        self.assertIn("isDesktopAppUrl", desktop)
        self.assertIn("isTauriCustomScheme", desktop)
        self.assertIn("tauri.localhost", desktop)
        self.assertIn("TAURI_CUSTOM_SCHEMES", desktop)
        vite = (ROOT / "webui/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("tendersDeskApi", vite)
        self.assertIn('"-m", "navin.tenders.desk_cli"', vite)
        self.assertTrue((ROOT / "navin/tenders/desk_cli.py").is_file())

    def test_tenders_options_are_identical_on_linux_windows_and_macos(self) -> None:
        """No OS branch, no Tauri default that drops heartbeat / send / LinkedIn / desk."""
        for path in (ROOT / "navin/tenders").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    combo = f"{node.value.id}.{node.attr}"
                    self.assertNotIn(
                        combo,
                        {"sys.platform", "os.name"},
                        f"{path} branches on {combo}",
                    )
        self.assertEqual(SEND_MODES, ("draft", "approval", "autonomous"))
        self.assertEqual(default_profile()["send_mode"], "approval")
        self.assertIn("tenders", VALID_PRODUCT_MODULES)
        self.assertEqual(extra_preload_skills_for_module("tenders"), list(TENDERS_REPORT_SKILLS))
        self.assertEqual(kept_tools_for_module("tenders"), frozenset(TENDERS_TOOLS))
        stack = module_stack()
        self.assertEqual(stack["desk"], "#/tenders")
        self.assertIn("Default send_mode is approval", stack["rules"])
        self.assertIn("follow is the silent heartbeat", stack["rules"])
        self.assertIn("navin tenders", stack["rules"])
        self.assertIn("python -m navin.tenders.desk_cli", stack["rules"])
        self.assertIn("desk loop hunts", stack["rules"])
        self.assertIn("Do not create a chat cron", stack["rules"])
        self.assertIn("uvx mcp-server-linkedin@latest", stack["rules"])
        linkedin = next(row for row in MCP_PRESETS if row.name == "linkedin")
        self.assertEqual(linkedin.server.command, "uvx")
        self.assertIn("mcp-server-linkedin@latest", linkedin.server.args)
        self.assertEqual(linkedin.modules, ("tenders", "career"))
        self.assertFalse(linkedin.auto_enable)
        opener = json.loads((ROOT / "desktop/src-tauri/capabilities/gateway-opener.json").read_text())
        self.assertEqual(opener["platforms"], ["linux", "macOS", "windows"])
        for host in (
            "http://127.0.0.1:*",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "tauri://localhost",
        ):
            self.assertIn(host, opener["remote"]["urls"], host)
        commands = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn('name="tenders"', commands)
        self.assertIn("navin.tenders.desk_cli", commands)
        self.assertIn("tenders_desk", commands)
        self.assertIn("augment_path_for_user_tools", commands)
        self.assertIn("tick_heartbeat_desks", commands)
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("WebAutomaticDashSubstitutionEnabled", rust)
        self.assertIn("NSAutomaticDashSubstitutionEnabled", rust)
        self.assertIn('http://tauri.localhost/#/tenders', rust)
        self.assertIn("https://tauri.localhost/#/tenders", rust)
        self.assertIn("https://tauri.localhost/#/tenders?notice=abc", rust)
        self.assertIn("https://tauri.localhost/#/tenders?pane=tenders", rust)
        self.assertIn("tauri://localhost/#/tenders", rust)
        self.assertIn("tauri://localhost/#/tenders?pane=tenders", rust)
        self.assertIn("#/tenders?pane=tenders", rust)
        desktop_ts = (ROOT / "webui/src/lib/desktop.ts").read_text(encoding="utf-8")
        self.assertIn("Linux, Windows, macOS", desktop_ts)
        self.assertIn("tauri.localhost", desktop_ts)
        self.assertIn("TAURI_CUSTOM_SCHEMES", desktop_ts)
        workspace = (ROOT / "webui/src/components/studio/tenders/TendersWorkspace.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-testid="tenders-start-loop"', workspace)
        self.assertIn('data-testid="tenders-pause-loop"', workspace)
        self.assertNotIn('data-testid="tenders-schedule"', workspace)
        self.assertIn("setScheduleOpen(true)", workspace)
        self.assertIn('data-testid="tenders-run-cycle"', workspace)
        self.assertIn('postTenders', (ROOT / "webui/src/lib/tenders-api.ts").read_text(encoding="utf-8"))
        entitlements = (ROOT / "desktop/src-tauri/entitlements.plist").read_text(encoding="utf-8")
        self.assertIn("com.apple.security.network.client", entitlements)
        self.assertIn("com.apple.security.network.server", entitlements)
        caps = ROOT / "desktop/src-tauri/capabilities"
        for name in ("gateway-opener.json", "gateway-dialog.json", "gateway-save.json", "gateway-zoom.json"):
            payload = json.loads((caps / name).read_text(encoding="utf-8"))
            self.assertEqual(payload["platforms"], ["linux", "macOS", "windows"], name)
        for sidecar in (
            "tauri.sidecar.linux.conf.json",
            "tauri.sidecar.windows.conf.json",
            "tauri.sidecar.macos.conf.json",
            "tauri.sidecar.macos-x64.conf.json",
        ):
            raw = (ROOT / "desktop/src-tauri" / sidecar).read_text(encoding="utf-8")
            self.assertIn("navin-dist", raw)
            self.assertNotIn("tenders", raw.lower())
            self.assertNotIn("heartbeat", raw.lower())

    def test_desk_is_single_composition_not_a_split(self) -> None:
        desk = _tenders_ui_source()
        self.assertIn("PipelineList", desk)
        self.assertIn("NoticePager", desk)
        self.assertIn("openFavorites", desk)
        self.assertIn("openArchive", desk)
        self.assertIn("rowMenuItems", desk)
        self.assertIn("DossierPane", desk)
        self.assertIn("filterPlay", desk)
        self.assertIn("biddingAs", desk)
        self.assertIn("max-w-5xl", desk)
        self.assertNotIn("lg:w-52", desk)
        self.assertNotIn("lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]", desk)
        self.assertNotIn("lg:grid-cols-2", desk)
        self.assertIn('useState<View>("work")', desk)
        self.assertIn("FollowUpBanner", desk)
        self.assertIn("ModelRouting", desk)
        self.assertIn("heroEmptyTitle", desk)
        self.assertIn("followSend", desk)
        self.assertIn("NoticePager", desk)
        self.assertIn("openFavorites", desk)
        self.assertIn("openArchive", desk)
        self.assertIn("crmPush", desk)
        self.assertIn("sendNow", desk)
        self.assertIn("sendAutoHint", desk)
        self.assertIn('tx("submit", "Mark submitted")', desk)
        self.assertIn('data-testid="tenders-response-draft"', desk)
        self.assertIn('data-testid="tenders-remarks"', desk)
        self.assertIn('data-testid="tenders-revise"', desk)
        self.assertNotIn("min-w-[5.2rem]", desk)
        wizard = (ROOT / "webui/src/components/studio/tenders/TendersWizard.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("methodology", wizard)
        self.assertIn("saveSamIfNeeded", wizard)
        self.assertIn("onBlur={saveSamIfNeeded}", wizard)
        self.assertIn("onPreviewFile", wizard)
        self.assertIn("onPreview={onPreviewFile}", wizard)
        workspace = (ROOT / "webui/src/components/studio/tenders/TendersWorkspace.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn('postTenders(token, "file"', workspace)
        self.assertIn("if (!token || !fileId)", workspace)
        chrome = (ROOT / "webui/src/components/studio/tenders/wizard/WizardChrome.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("previewExtract", chrome)
        self.assertIn("row.excerpt", chrome)
        self.assertIn("} catch {", chrome)
        self.assertIn("setLoading(\"\")", chrome)

    def test_i18n_and_copy_have_no_em_dash(self) -> None:
        for relative in (
            "webui/src/components/studio/tenders/TendersWorkspace.tsx",
            "webui/src/components/studio/tenders/TendersWizard.tsx",
            "webui/src/components/studio/tenders/TendersDesk.tsx",
            "navin/skills/tender-agent/SKILL.md",
            "navin/tenders/desk.py",
            "navin/tenders/desk_cli.py",
            "navin/tenders/notify.py",
            "navin/tenders/scrape_net.py",
            "navin/tenders/collect.py",
            "navin/tenders/profile.py",
            "navin/tenders/watch.py",
            "navin/tenders/crm_sync.py",
            "navin/tenders/writer.py",
            "navin/tenders/ai.py",
            "navin/tenders/index.py",
            "navin/agent/tools/tenders.py",
            "webui/src/components/studio/tenders/money.ts",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(EM_DASH, text, relative)
            self.assertNotIn(EN_DASH, text, relative)
        en = json.loads((ROOT / "webui/src/i18n/locales/en/common.json").read_text(encoding="utf-8"))
        fr = json.loads((ROOT / "webui/src/i18n/locales/fr/common.json").read_text(encoding="utf-8"))
        self.assertIn("tenders", en["thread"]["sessionInfo"]["createSeed"])
        seed = en["thread"]["sessionInfo"]["createSeed"]["tenders"]
        seed_fr = fr["thread"]["sessionInfo"]["createSeed"]["tenders"]
        for body in (seed, seed_fr):
            self.assertIn("navin tenders", body)
            self.assertIn("action=start", body)
            self.assertIn("action=stop", body)
            self.assertIn("action=schedule", body)
            self.assertNotIn("Create a loop for this tenders chat", body)
        self.assertIn("Do not create a chat cron", seed)
        self.assertIn("Never send a buyer mail", seed)
        self.assertIn("tendersStudio", en["sidebar"])
        blob = json.dumps(
            {
                "en": en["studio"]["tenders"],
                "fr": fr["studio"]["tenders"],
                "seed": en["thread"]["sessionInfo"]["createSeed"]["tenders"],
            },
            ensure_ascii=False,
        )
        self.assertNotIn(EM_DASH, blob)
        self.assertNotIn(EN_DASH, blob)


class TendersCatalogTest(unittest.TestCase):
    def test_priority_portals_are_listed(self) -> None:
        rows = catalog()
        ids = {row["id"] for row in rows}
        self.assertEqual(len(ids), len(rows))
        for sid in (
            "ted",
            "place",
            "boamp",
            "etimad",
            "haicop",
            "maroc-marches",
            "ungm",
            "world-bank",
            "afdb",
            "sam-gov",
            "canadabuys",
            "senegal-armds",
            "cote-ivoire-sigmap",
            "luxembourg-pmp",
            "sweden-uhmynd",
            "cameroon-armp",
            "algeria-marches",
            "kenya-tenders",
            "nigeria-nocopo",
        ):
            self.assertIn(sid, ids)
        ingest = {row["id"]: row["ingest"] for row in rows}
        self.assertEqual(ingest["ted"], "api")
        self.assertEqual(ingest["world-bank"], "api")
        self.assertEqual(ingest["boamp"], "api")
        self.assertEqual(ingest["find-a-tender"], "api")
        self.assertEqual(ingest["canadabuys"], "opendata")
        self.assertEqual(ingest["place"], "html")
        self.assertEqual(coverage_holes(), [])
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["place"]["coverage"], "search")
        self.assertEqual(by_id["place"]["access"], "scrape")
        self.assertEqual(by_id["belgium-eproc"]["access"], "scrape")
        self.assertEqual(by_id["luxembourg-pmp"]["access"], "scrape")
        self.assertEqual(by_id["sweden-uhmynd"]["access"], "scrape")
        self.assertEqual(by_id["ted"]["access"], "api")
        self.assertEqual(by_id["canadabuys"]["access"], "opendata")
        self.assertEqual(by_id["germany-evergabe"]["covered_by"], "ted")
        self.assertEqual(by_id["germany-evergabe"]["access"], "covered")
        self.assertEqual(by_id["sam-gov"]["coverage"], "key")
        self.assertEqual(by_id["etimad"]["coverage"], "search")
        self.assertGreaterEqual(len(rows), 70)
        self.assertTrue(all(row.get("url", "").startswith("https://") for row in rows))
        self.assertTrue(all(row.get("access") for row in rows))
        hosts = official_hosts()
        self.assertIn("tenders.etimad.sa", hosts)
        self.assertIn("ungm.org", hosts)
        self.assertTrue(host_is_official("www.tenders.etimad.sa", hosts))
        self.assertFalse(host_is_official("marchesonline.com", hosts))

    def test_country_filter_keeps_international_and_eu(self) -> None:
        ids = {row["id"] for row in sources_for_countries(["SA"])}
        self.assertIn("etimad", ids)
        self.assertIn("ted", ids)
        self.assertIn("world-bank", ids)
        self.assertIn("ungm", ids)
        self.assertNotIn("place", ids)

    def test_web_search_covers_preferred_countries(self) -> None:
        queries = web_search_queries(
            countries=["FR", "SA", "TN"],
            crafts=["AI", "Data"],
        )
        texts = " ".join(row["query"] for row in queries)
        self.assertIn("appel d'offres", texts)
        self.assertIn("مناقصة", texts)
        self.assertIn("site:gouv.fr", texts)
        self.assertIn("site:gov.sa", texts)
        all_iso = {row["country"] for row in catalog() if row["coverage"] == "search"}
        queries = web_search_queries(countries=sorted(all_iso), crafts=["AI"])
        covered = {row["country"] for row in queries}
        self.assertTrue(all_iso <= covered, all_iso - covered)


class TendersNormalizeTest(unittest.TestCase):
    def test_iso_dates_and_stable_ids(self) -> None:
        self.assertEqual(iso_date("15/03/2026"), "2026-03-15")
        self.assertEqual(iso_date("published 2026-03-15T12:00"), "2026-03-15")
        self.assertEqual(host_of("https://TED.europa.eu/en/notice"), "ted.europa.eu")
        first = tender_id("ted", "2026-1")
        self.assertEqual(first, tender_id("ted", "2026-1"))
        self.assertTrue(first.startswith("tn-"))
        dashed = normalize_tender({"title": "Croatia \u2013 Cloud"}, source_id="ted")
        self.assertNotIn("\u2013", dashed["title"])
        self.assertIn("-", dashed["title"])
        row = normalize_tender({"name": "  Cloud  RFP  "}, source_id="ungm")
        self.assertEqual(row["title"], "Cloud RFP")
        self.assertEqual(row["country"], "INTL")
        self.assertEqual(row["stage"], "discovered")
        self.assertIsNone(row["budget"])

    def test_homepage_is_not_a_notice(self) -> None:
        portal = normalize_tender(
            {"title": "eSupply - The official supply portal of Dubai Government", "country": "AE"},
            source_id="esupply",
        )
        self.assertFalse(looks_like_notice(portal))
        self.assertFalse(looks_like_notice({"title": "Untitled notice"}))
        real = _notice()
        self.assertTrue(looks_like_notice(real))


class TendersScoreTest(unittest.TestCase):
    def test_decisionnel_matches_data_craft(self) -> None:
        profile = _score_profile()
        scored = score_tender(
            {
                "title": "Modernisation du SI decisionnel",
                "description": "Refonte du SI decisionnel et de la plateforme analytics.",
                "country": "FR",
            },
            profile,
        )
        self.assertGreaterEqual(scored["breakdown"]["technical"], 70)
        self.assertGreaterEqual(scored["breakdown"]["country"], 90)

    def test_off_country_and_tiny_budget_are_penalized(self) -> None:
        profile = _score_profile()
        profile["countries"] = ["FR"]
        profile["min_budget"] = 80_000
        scored = score_tender(
            {
                "title": "Office chairs",
                "description": "fourniture de sieges",
                "country": "JP",
                "budget": 12_000,
                "deadline": "2000-01-01",
            },
            profile,
        )
        self.assertLess(scored["breakdown"]["country"], 40)
        self.assertLess(scored["breakdown"]["budget"], 40)
        self.assertEqual(scored["breakdown"]["deadline"], 0.0)

    def test_right_country_never_carries_an_off_craft_notice(self) -> None:
        profile = _score_profile()
        profile["min_score"] = 70
        scored = score_tender(
            {
                "title": "Entretien des espaces verts et deneigement des parkings",
                "description": "Tonte, taille, deneigement des voies d'acces.",
                "country": "FR",
                "budget": 180_000,
                "deadline": "2099-10-02",
            },
            profile,
        )
        self.assertLess(scored["breakdown"]["technical"], 45)
        self.assertLess(scored["score"], 70)
        decision = go_nogo(
            {"score": scored["score"], "score_breakdown": scored["breakdown"]},
            profile,
        )
        self.assertFalse(decision["go"])
        self.assertIn("domains", decision["reason"])
        self.assertNotIn("meets the", decision["reason"])

    def test_catalog_domain_and_tender_type_are_scored(self) -> None:
        profile = default_profile()
        profile["countries"] = ["FR"]
        profile["crafts"] = ["Services informatiques"]
        profile["tender_types"] = ["Services"]
        profile["project_types"] = ["Plateforme data / BI"]
        it_notice = {
            "title": "Prestations de services informatiques",
            "description": "Infogerance et modernisation du systeme d'information.",
            "country": "FR",
            "cpv": "72000000",
        }
        scored = score_tender(it_notice, profile)
        self.assertGreaterEqual(scored["breakdown"]["technical"], 70)
        self.assertGreaterEqual(scored["breakdown"]["type"], 90)
        works_profile = dict(profile)
        works_profile["crafts"] = ["Travaux de construction"]
        works_profile["tender_types"] = ["Travaux"]
        works_profile["project_types"] = ["Genie civil / voiries"]
        works = score_tender(
            {
                "title": "Marche de travaux de voirie",
                "description": "Travaux de genie civil et renovation de chaussee.",
                "country": "FR",
                "cpv": "45000000",
            },
            works_profile,
        )
        self.assertGreaterEqual(works["breakdown"]["technical"], 70)
        self.assertGreaterEqual(works["breakdown"]["type"], 90)


class TendersNeedsCatalogTest(unittest.TestCase):
    def test_catalog_covers_official_cpv_and_ted_types(self) -> None:
        official = {
            "03000000",
            "09000000",
            "14000000",
            "15000000",
            "16000000",
            "18000000",
            "19000000",
            "22000000",
            "24000000",
            "30000000",
            "31000000",
            "32000000",
            "33000000",
            "34000000",
            "35000000",
            "37000000",
            "38000000",
            "39000000",
            "41000000",
            "42000000",
            "43000000",
            "44000000",
            "45000000",
            "48000000",
            "50000000",
            "51000000",
            "55000000",
            "60000000",
            "63000000",
            "64000000",
            "65000000",
            "66000000",
            "70000000",
            "71000000",
            "72000000",
            "73000000",
            "75000000",
            "76000000",
            "77000000",
            "79000000",
            "80000000",
            "85000000",
            "90000000",
            "92000000",
            "98000000",
        }
        self.assertEqual(set(official_cpv_divisions()), official)
        self.assertGreaterEqual(len(TENDER_TYPES), 15)
        self.assertIsNotNone(lookup_need("AI"))
        self.assertIsNotNone(lookup_need("Intelligence artificielle"))
        self.assertIsNotNone(lookup_need("IT services"))
        self.assertIsNotNone(lookup_need("Services informatiques"))
        self.assertIsNotNone(lookup_need("Works"))
        self.assertIsNotNone(lookup_need("Travaux"))
        self.assertTrue(any("informatique" in item for item in expand_need_terms(["Services informatiques"])))
        catalog = public_needs_catalog()
        self.assertGreaterEqual(len(catalog["domains"]), 40)
        self.assertGreaterEqual(len(catalog["tender_types"]), 15)
        self.assertGreaterEqual(len(catalog["project_types"]), 15)

    def test_needs_persist_index_and_collect_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            saved = store.save_profile(
                {
                    "name": "Atelier Needs",
                    "countries": ["FR"],
                    "crafts": ["Services informatiques", "AI"],
                    "tender_types": ["Travaux", "Accord-cadre"],
                    "project_types": ["Plateforme data / BI", "Ponts"],
                }
            )
            self.assertEqual(saved["crafts"], ["Services informatiques", "AI"])
            self.assertEqual(saved["tender_types"], ["Travaux", "Accord-cadre"])
            self.assertEqual(saved["project_types"], ["Plateforme data / BI", "Ponts"])
            dossier = format_dossier(saved)
            self.assertIn("Services informatiques", dossier)
            self.assertIn("Travaux", dossier)
            self.assertIn("Plateforme data / BI", dossier)
            self.assertIn("Ponts", dossier)
            paths = write_local_index(store)
            book = Path(paths["book"]).read_text(encoding="utf-8")
            index = json.loads(Path(paths["index_json"]).read_text(encoding="utf-8"))
            self.assertIn("Services informatiques", book)
            self.assertIn("Travaux", book)
            self.assertEqual(index["profile"]["crafts"], ["Services informatiques", "AI"])
            self.assertEqual(index["profile"]["tender_types"], ["Travaux", "Accord-cadre"])
            snap = snapshot(store)
            self.assertIn("needs_catalog", snap)
            self.assertTrue(any(row["id"] == "it-services" for row in snap["needs_catalog"]["domains"]))
            texts = " ".join(row["query"] for row in snap["queries"])
            self.assertIn("Services informatiques", texts)
            self.assertIn("Travaux", texts)
            letter = build_response(_notice(), saved)["letter"]
            self.assertIn("Services informatiques", letter)
            self.assertIn("Travaux", letter)
            self.assertIn("Plateforme data / BI", letter)


class TendersWriterTest(unittest.TestCase):
    def test_analyse_flags_kbis_and_budget(self) -> None:
        analysis = analyse_tender(
            {
                "title": "AO cloud",
                "description": "joindre le Kbis et les certifications",
                "eligibility": "Kbis + ISO",
            },
            _score_profile(),
        )
        self.assertTrue(any("budget" in gap for gap in analysis["gaps"]))
        self.assertTrue(any("Kbis" in gap or "kbis" in gap.lower() for gap in analysis["gaps"]))

    def test_go_nogo_respects_deadline_floor(self) -> None:
        profile = _score_profile()
        tomorrow = (dt.date.today() + dt.timedelta(days=3)).isoformat()
        scored = score_tender(
            {"title": "Data platform", "description": "cloud data", "country": "FR", "deadline": tomorrow},
            profile,
        )
        decision = go_nogo(
            {"score": scored["score"], "score_breakdown": scored["breakdown"], "title": "Data platform"},
            profile,
        )
        self.assertFalse(decision["go"])
        self.assertIn("deadline", decision["reason"])

    def test_response_is_notice_specific(self) -> None:
        profile = _score_profile()
        profile["name"] = "Acme Digital"
        profile["references"] = [{"title": "SI decisionnel ministere", "year": "2024"}]
        notice = _notice()
        notice["score"] = 82
        notice["analysis"] = analyse_tender(notice, profile)
        dossier = build_response(notice, profile)
        self.assertIn("Acme Digital", dossier["letter"])
        self.assertIn(notice["title"], dossier["letter"])
        self.assertIn("SI decisionnel ministere", dossier["references"])
        self.assertEqual(dossier["send_mode"], "approval")
        self.assertIn("AI", dossier["architecture"])
        self.assertIn(notice["title"], dossier["architecture"])
        self.assertIn("2099-12-31", dossier["planning"])
        self.assertNotIn("Effort estime", dossier["planning"])
        self.assertNotIn("Estimated effort", dossier["planning"])
        self.assertIn("non lue dans l'avis", dossier["planning"])
        self.assertIn("DOSSIER DE CANDIDATURE", dossier["cover"])
        self.assertIn("Sommaire", dossier["toc"])
        self.assertIn("Presentation de la societe", dossier["company"] + dossier["toc"])
        self.assertIn("Comprehension du besoin", dossier["need"] + dossier["toc"])
        self.assertIn("Demarche", dossier["approach"] + dossier["toc"])
        self.assertIn("Vision", dossier["vision"] + dossier["toc"])
        self.assertIn("Reponse fonctionnelle", dossier["functional"] + dossier["toc"])
        self.assertIn("suivi", dossier["followup_kpi"].lower())
        self.assertIn("RACI", dossier["raci_risks"])
        self.assertIn("Gouvernance", dossier["governance"])
        self.assertIn("180", str(dossier["financial_schedule"]))
        self.assertGreaterEqual(dossier["requirements_count"], 4)
        self.assertIn("ISO 27001", dossier["compliance_matrix"])
        self.assertIn(notice["title"], commercial_draft(notice, "clarification"))
        self.assertIn("acknowledge", commercial_draft(notice, "ack").lower())

    def test_planning_does_not_print_guessed_effort_days(self) -> None:
        profile = _score_profile()
        notice = _notice()
        notice["effort_days"] = 12
        notice["score"] = 40
        notice["score_breakdown"] = {"days_left": 3, "technical": 10}
        dossier = build_response(notice, profile)
        self.assertIn("non lue dans l'avis", dossier["planning"])
        self.assertNotIn("12 jours", dossier["planning"])
        self.assertNotIn("8 jours", dossier["planning"])
        self.assertNotIn("2 jours", dossier["planning"])
        self.assertNotIn("Effort estime", dossier["planning"])

    def test_company_chapter_prints_partner_names(self) -> None:
        from navin.tenders.bid_pack import draft_company

        text = draft_company(
            {"title": "AO cloud", "country": "FR"},
            {
                "legal_name": "Atelier Demo",
                "partners": [{"name": "Hexa Cloud", "role": "hebergement", "country": "FR"}],
            },
            lang="fr",
            missing="n/a",
            company="Atelier Demo",
            crafts="Cloud",
            tender_types="AO",
            project_types="IT",
            specialty="Cloud",
            strengths="IA",
        )
        self.assertIn("Hexa Cloud / hebergement / FR", text)
        self.assertNotIn("{'name':", text)

    def test_reply_follows_the_buyer_language(self) -> None:
        profile = _score_profile()
        profile["name"] = "Acme Digital"
        french = build_response(_notice(country="FR"), profile)
        self.assertEqual(french["language"], "fr")
        self.assertIn("Objet :", french["letter"])
        self.assertNotIn("Dear", french["letter"])
        self.assertIn("Exigence", french["compliance_matrix"])
        self.assertNotIn("cahier des charges", french["executive_summary"].split("criteres :")[0])
        self.assertNotIn("not extracted", french["executive_summary"])
        self.assertIn(
            "accusons reception",
            commercial_draft(_notice(country="FR"), "ack", profile).lower(),
        )
        gulf = build_response(
            _notice(
                country="AE",
                title="IT infrastructure modernization for the authority",
                description="Rebuild the analytics platform and the decision support services.",
                eligibility="ISO 27001, three references",
            ),
            profile,
        )
        self.assertEqual(gulf["language"], "en")
        self.assertIn("Subject:", gulf["letter"])
        self.assertIn(
            "acknowledge",
            commercial_draft(
                _notice(
                    country="AE",
                    title="IT infrastructure modernization for the authority",
                    description="Rebuild the analytics platform and the decision support services.",
                ),
                "ack",
                profile,
            ).lower(),
        )

    def test_title_only_notice_still_builds_a_full_ready_dossier(self) -> None:
        profile = _score_profile()
        profile["name"] = "Acme Digital"
        profile["methodology"] = "Atelier Cloud puis recette. Phase 2 planning interne."
        profile["team"] = [{"role": "Chef de projet", "name": "Sam"}]
        profile["references"] = [{"title": "SI decisionnel ministere", "year": "2024"}]
        notice = _notice(description="", eligibility="ISO 27001; trois references; depot PLACE")
        notice["analysis"] = analyse_tender(notice, profile)
        dossier = build_response(notice, profile)
        self.assertIn("Acme Digital", dossier["letter"])
        self.assertIn("Architecture", dossier["architecture"])
        self.assertIn("Chef de projet", dossier["planning"])
        self.assertIn("depot PLACE", dossier["compliance_matrix"])
        self.assertGreaterEqual(dossier["compliance_matrix"].count("\n| "), 5)
        self.assertTrue(dossier["ready"])
        reqs = extract_requirements(notice, profile, notice["analysis"])
        self.assertTrue(any("ISO 27001" in row["text"] for row in reqs))

    def test_thin_notice_is_enriched_from_the_official_page(self) -> None:
        notice = _notice(description="")
        self.assertTrue(notice_is_thin(notice))
        html = (
            "<html><body><h1>CDC</h1><p>Le prestataire doit fournir une architecture "
            "decisionnelle, un planning de 12 semaines et trois references.</p></body></html>"
        )
        filled = enrich_notice(notice, fetch=lambda _url: html.encode("utf-8"))
        self.assertTrue(filled.get("enriched"))
        self.assertIn("architecture decisionnelle", filled["description"])
        self.assertGreater(len(html_to_text(html)), 40)
        skipped = enrich_notice(_notice(), fetch=lambda _url: b"ignored")
        self.assertFalse(skipped.get("enriched"))

    def test_boamp_chrome_is_not_used_as_the_need(self) -> None:
        chrome = (
            "Detail d'un avis | boamp.fr - boamp.fr {{ bo_host = \"https://compte.boamp.fr\" ; '' }} "
            "Republique francaise Menu BOAMP.fr Bulletin officiel des annonces des marches publics "
            "Espace acheteur public Service d'alerte Fermer Accueil espace entreprise"
        )
        self.assertTrue(fetch_text_is_noise(chrome))
        notice = _notice(description=chrome)
        cleaned = enrich_notice(notice, fetch=lambda _url: chrome.encode("utf-8"))
        self.assertNotIn("boamp.fr", cleaned.get("description") or "")
        self.assertNotIn("{{", cleaned.get("description") or "")
        dossier = build_response(cleaned, _score_profile())
        self.assertNotIn("boamp.fr", dossier["need"])
        self.assertNotIn("{{", dossier["letter"])

    def test_deadline_is_a_milestone_not_an_exhibit(self) -> None:
        from navin.tenders.bid_pack import _requirement_kind

        self.assertEqual(_requirement_kind("Date limite 2026-09-10"), "milestone")
        profile = _score_profile()
        notice = _notice(deadline="2026-09-10")
        notice["analysis"] = analyse_tender(notice, profile)
        dossier = build_response(notice, profile)
        self.assertFalse(any("Date limite" in row["text"] for row in extract_requirements(notice, profile, notice["analysis"])))
        self.assertNotIn("Date limite 2026-09-10 : piece a produire", dossier["functional"])
        self.assertIn("2026-09-10", dossier["planning"])
        self.assertIn("2026-09-10", dossier["cover"])

    def test_amoa_does_not_sell_ai_cloud_lots_or_repeat_the_title_as_need(self) -> None:
        profile = _score_profile()
        profile["name"] = "Atelier Demo"
        profile["crafts"] = ["AI", "Cloud"]
        profile["specialty"] = "Cloud AI"
        notice = {
            "title": "ASSISTANCE A MAITRISE D'OUVRAGE (AMOA)",
            "description": "",
            "country": "FR",
            "buyer": "CNBF",
            "deadline": "2026-09-10",
            "reference": "26-83997",
        }
        notice["analysis"] = analyse_tender(notice, profile)
        dossier = build_response(notice, profile)
        self.assertIn("CDC n'est pas lisible", dossier["need"] + dossier["vision"] + dossier["executive_summary"])
        self.assertIn("livrables, volumes", dossier["need"])
        self.assertNotIn("lot tenu pour couvrir", dossier["architecture"])
        self.assertIn("pas un lot de ce marche", dossier["architecture"])
        self.assertNotIn("tenu dans le perimetre AI", dossier["functional"])
        self.assertNotIn("Types d'AO deja tenus : non renseigne", dossier["letter"])
        self.assertIn("A completer au dossier societe", dossier["company"])
        self.assertNotIn("non renseigne / non renseigne", dossier["company"])
        self.assertNotIn("Date limite 2026-09-10 : piece a produire", dossier["functional"])

    def test_hollow_profile_keeps_gaps_in_one_register(self) -> None:
        notice = _notice()
        profile = {"name": "Atelier Demo", "crafts": ["AI"], "countries": ["FR"]}
        dossier = build_response(notice, profile)
        self.assertIn("A completer au dossier societe", dossier["company"])
        self.assertLess(dossier["company"].count("non renseigne"), 3)
        self.assertIn("Aucun nom au dossier societe", dossier["staffing"])

    def test_filled_file_dossier_has_no_holes(self) -> None:
        from navin.tenders.bid_pack import SECTION_KEYS, SECTION_TITLES

        profile = _filled_profile()
        notice = _rich_notice()
        notice["analysis"] = analyse_tender(notice, profile)
        dossier = build_response(notice, profile)
        hole = "non renseigne"
        for key in SECTION_KEYS:
            body = str(dossier.get(key) or "")
            self.assertNotIn(hole, body, msg=f"{key}: {body[:240]}")
            self.assertNotIn("not on file", body.lower(), msg=key)
            if key == "revision_notes":
                continue
            self.assertTrue(body.strip(), msg=f"chapitre vide : {key}")
        self.assertNotIn("A completer au dossier societe", dossier["company"])
        self.assertIn("Sam Leroy", dossier["staffing"])
        self.assertIn("Lea Martin", dossier["staffing"])
        self.assertIn("ISO 27001", dossier["company"])
        self.assertIn("950", dossier["financial_schedule"])
        self.assertIn("bordereau au dossier", dossier["compliance_matrix"])
        self.assertIn("PostgreSQL", dossier["architecture"])
        self.assertIn("9 mois", dossier["planning"])
        self.assertIn("hebdomadaire", dossier["followup_kpi"].lower())
        self.assertIn("Lac de donnees ministere", dossier["references"])
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(profile)
            store.save_tenders([notice])
            written = write_one(store, notice["id"])
            row = next(item for item in written["tenders"] if item["id"] == notice["id"])
            dossier = row["response"]
            for key in SECTION_KEYS:
                body = str(dossier.get(key) or "")
                self.assertNotIn(hole, body, msg=f"pack {key}")
            docx_path = store.files_dir / dossier["exports"]["docx"]["path"]
            pptx_path = store.files_dir / dossier["exports"]["pptx"]["path"]
            with zipfile.ZipFile(docx_path) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
            self.assertNotIn(hole, xml)
            self.assertNotIn("\u2014", xml)
            self.assertNotIn("\u2013", xml)
            for title in (
                SECTION_TITLES["fr"]["letter"],
                SECTION_TITLES["fr"]["company"],
                SECTION_TITLES["fr"]["staffing"],
                SECTION_TITLES["fr"]["architecture"],
                "DOSSIER DE CANDIDATURE",
                "Sam Leroy",
                "ISO 27001",
                "950",
            ):
                self.assertIn(title, xml)
            with zipfile.ZipFile(pptx_path) as archive:
                ppt = "\n".join(
                    archive.read(name).decode("utf-8", errors="ignore")
                    for name in archive.namelist()
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml")
                )
            ppt_text = re.sub(r"<[^>]+>", " ", ppt)
            self.assertNotIn(hole, ppt_text)
            self.assertIn("DOSSIER DE CANDIDATURE", ppt_text)

    def test_write_builds_word_and_ppt_and_revise_keeps_remarks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(
                {
                    "name": "Acme Digital",
                    "crafts": ["AI", "Data"],
                    "countries": ["FR"],
                    "specialty": "Data",
                    "methodology": "Atelier Cloud",
                    "ai_assist": False,
                }
            )
            notice = _notice()
            store.save_tenders([notice])
            written = write_one(store, notice["id"])
            row = next(item for item in written["tenders"] if item["id"] == notice["id"])
            self.assertEqual(row["stage"], "drafting")
            self.assertIn("AI", row["response"]["architecture"])
            self.assertTrue(row["response"].get("pack_ready"))
            self.assertIn("docx", row["response"]["exports"])
            self.assertIn("pptx", row["response"]["exports"])
            docx_path = store.files_dir / row["response"]["exports"]["docx"]["path"]
            pptx_path = store.files_dir / row["response"]["exports"]["pptx"]["path"]
            self.assertTrue(docx_path.is_file())
            self.assertTrue(pptx_path.is_file())
            self.assertGreater(docx_path.stat().st_size, 1000)
            with zipfile.ZipFile(docx_path) as zf:
                xml = zf.read("word/document.xml").decode("utf-8")
            self.assertTrue("DOSSIER DE CANDIDATURE" in xml or "SUBMISSION DOSSIER" in xml)
            self.assertTrue("Sommaire" in xml or "Contents" in xml)
            self.assertTrue("Presentation de la societe" in xml or "Company presentation" in xml)
            self.assertTrue("Reponse technique" in xml or "Technical response" in xml)
            self.assertNotIn("JavaScript is disabled", xml)
            with patch("navin.webui.tenders_api._store", return_value=store):
                downloaded = handle_tenders_action("download", {"id": notice["id"], "kind": "docx"})
            self.assertTrue(downloaded["download"]["data"])
            self.assertIn("docx", downloaded["download"]["name"])
            revised = revise_one(store, notice["id"], "Ajouter le lot data dans la lettre.")
            updated = next(item for item in revised["tenders"] if item["id"] == notice["id"])
            self.assertEqual(updated["stage"], "validating")
            self.assertIn("Ajouter le lot data", updated["response"]["revision_notes"])
            self.assertIn("Ajouter le lot data", updated["response"]["letter"])
            self.assertTrue(updated["response_reviews"])


class TendersModelRoutingTest(unittest.TestCase):
    """The desk calls the models the user routed in Settings, or no model at all."""

    def _profile(self) -> dict:
        profile = _score_profile()
        profile["name"] = "Acme Digital"
        return profile

    def test_tasks_map_onto_the_settings_roles(self) -> None:
        self.assertEqual(WORKFLOW_ROUTE_ROLES.get("/tenders"), "deep")
        self.assertEqual(workflow_role_for_content("/tenders collect"), "deep")
        self.assertEqual(task_role("qualify"), "deep")
        self.assertEqual(task_role("write"), "docs")
        self.assertEqual(task_role("mail"), "docs")

    def test_preset_follows_the_configured_routes(self) -> None:
        routes = {"deep": "frontier", "docs": "economy", "dev": "everyday"}
        with patch("navin.agent.model_routes.load_model_routes", return_value=routes):
            self.assertEqual(task_preset("qualify"), "frontier")
            self.assertEqual(task_preset("write"), "economy")
        # Role unset in Settings: fall back to the everyday model, not to nothing.
        with patch("navin.agent.model_routes.load_model_routes", return_value={"dev": "everyday"}):
            self.assertEqual(task_preset("qualify"), "everyday")

    def test_no_route_means_no_model_call_at_all(self) -> None:
        with (
            patch("navin.agent.model_routes.load_model_routes", return_value={}),
            patch("navin.tenders.ai._chat") as chat,
        ):
            self.assertIsNone(task_preset("write"))
            self.assertEqual(ask("write", "system", "user"), ("", ""))
            chat.assert_not_called()

    def test_the_desk_can_be_kept_fully_deterministic(self) -> None:
        with (
            patch("navin.agent.model_routes.load_model_routes", return_value={"deep": "frontier"}),
            patch("navin.tenders.ai._chat") as chat,
        ):
            self.assertEqual(ask("qualify", "s", "u", profile={"ai_assist": False}), ("", ""))
            chat.assert_not_called()

    def test_snapshot_names_the_model_behind_each_task(self) -> None:
        models = {"frontier": "qwen/qwen3.8-max", "economy": "deepseek/deepseek-v4-flash"}
        config = SimpleNamespace(
            model_routes={"deep": "frontier", "docs": "economy"},
            resolve_preset=lambda name: SimpleNamespace(model=models[name]),
        )
        with patch("navin.config.loader.load_config", return_value=config):
            snap = routing_snapshot({})
        rows = {row["task"]: row for row in snap["tasks"]}
        self.assertTrue(snap["enabled"])
        self.assertEqual(snap["routed"], 3)
        self.assertEqual(rows["qualify"]["model"], "qwen/qwen3.8-max")
        self.assertEqual(rows["write"]["model"], "deepseek/deepseek-v4-flash")
        self.assertEqual(rows["mail"]["role"], "docs")

    def test_an_answer_that_invents_a_figure_is_refused(self) -> None:
        material = "Budget: 180 000\nDeadline: 2099-12-31\nReferences on file: not on file"
        self.assertTrue(
            keeps_only_known_facts(
                material,
                "Nous repondons avant le 2099-12-31 pour 180000 EUR. References : not on file.",
            )
        )
        self.assertFalse(
            keeps_only_known_facts(material, "Nous avons livre 42 projets. References : not on file.")
        )
        # Hiding a gap is inventing too: the bid team must still see the hole.
        self.assertFalse(
            keeps_only_known_facts(material, "References : trois plateformes data livrees.")
        )

    def test_a_polished_letter_replaces_the_template_only_when_it_is_clean(self) -> None:
        tender = _notice()
        profile = self._profile()
        base = build_response(tender, profile)
        invented = ("Acme Digital a livre 42 plateformes data.", "qwen/qwen3.8-max")
        with patch("navin.tenders.ai.ask", return_value=invented):
            kept = polish_response(tender, profile, base)
        self.assertEqual(kept["letter"], base["letter"])
        self.assertNotIn("model", kept)

        clean = (
            "Objet : Candidature\n\nAcme Digital depose une offre. "
            "Specialite : non renseigne.\n---\nAcme Digital comprend le besoin.",
            "qwen/qwen3.8-max",
        )
        with patch("navin.tenders.ai.ask", return_value=clean):
            polished = polish_response(tender, profile, base)
        self.assertIn("Acme Digital depose une offre", polished["letter"])
        self.assertIn("comprend le besoin", polished["executive_summary"])
        self.assertEqual(polished["model"], "qwen/qwen3.8-max")
        self.assertEqual(polished["route"], "docs")

    def test_the_model_explains_the_verdict_but_never_flips_it(self) -> None:
        tender = _notice()
        profile = self._profile()
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_tenders([tender])
            store.save_profile({"name": "Acme", "crafts": "Bridges", "countries": "FR"})
            note = ("This one is out of scope, check the lots before dropping it.", "m")
            with (
                patch("navin.webui.tenders_api._store", return_value=store),
                patch("navin.tenders.ai.ask", return_value=note),
            ):
                snap = handle_tenders_action("qualify", {"id": tender["id"]})
        row = next(item for item in snap["tenders"] if item["id"] == tender["id"])
        self.assertFalse(row["go"])
        self.assertEqual(row["stage"], "no-go")
        self.assertIn("out of scope", row["go_note"])

    def test_a_mail_draft_keeps_the_template_when_the_model_is_silent(self) -> None:
        tender = _notice()
        profile = self._profile()
        base = commercial_draft(tender, "relance", profile)
        with patch("navin.tenders.ai.ask", return_value=("", "")):
            self.assertEqual(polish_mail(tender, profile, "relance", base), base)


class TendersFollowUpTest(unittest.TestCase):
    def _store(self, tmp: str) -> TenderStore:
        store = TenderStore(Path(tmp))
        store.save_profile({"name": "Acme", "locale": "fr-FR", "channels": {"telegram": True, "telegram_to": "42"}})
        return store

    def test_one_digest_for_the_whole_book_then_silence(self) -> None:
        from navin.tenders.watch import pending_alerts, run_watch

        soon = (dt.date.today() + dt.timedelta(days=2)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.save_tenders(
                [
                    {**_notice(reference="GO-1"), "stage": "go", "go": True, "score": 82},
                    {
                        **_notice(reference="DUE-1", title="Plateforme data", deadline=soon),
                        "stage": "go",
                        "go": True,
                        "score": 74,
                    },
                    {**_notice(reference="NO-1", title="Espaces verts"), "stage": "no-go", "go": False},
                ]
            )
            events = pending_alerts(store)
            kinds = sorted({event["kind"] for event in events})
            self.assertEqual(kinds, ["deadline", "go"])
            self.assertTrue(all(event["id"] for event in events))
            with patch("navin.tenders.watch.deliver_alert", return_value={"webui": True}) as sender:
                first = run_watch(store)
            self.assertEqual(sender.call_count, 1, "the book must ship as one digest, not one ping per notice")
            self.assertGreaterEqual(first["count"], 3)
            digest = sender.call_args.kwargs["detail"]
            self.assertIn("GO:", digest)
            self.assertIn("J-2", digest)
            self.assertNotIn("Espaces verts", digest)
            with patch("navin.tenders.watch.deliver_alert", return_value={"webui": True}) as again:
                second = run_watch(store)
            self.assertEqual(second["count"], 0)
            self.assertEqual(again.call_count, 0, "a pushed alert must never be pushed twice")

    def test_failed_digest_is_not_marked_sent(self) -> None:
        from navin.tenders.watch import pending_alerts, run_watch

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.save_tenders([{**_notice(reference="GO-FAIL"), "stage": "go", "go": True, "score": 80}])
            self.assertTrue(pending_alerts(store))
            with patch(
                "navin.tenders.watch.deliver_alert",
                return_value={
                    "webui": False,
                    "telegram": False,
                    "whatsapp": False,
                    "email": False,
                    "teams": False,
                    "slack": False,
                },
            ):
                first = run_watch(store)
            self.assertFalse(first.get("delivered"))
            self.assertGreaterEqual(first["count"], 1)
            self.assertTrue(pending_alerts(store), "a failed digest must stay pending")
            row = store.get(store.load_tenders()[0]["id"])
            self.assertFalse(row.get("alerts_sent"))
            from navin.tenders.watch import digest_was_delivered

            self.assertFalse(digest_was_delivered({}))
            self.assertFalse(digest_was_delivered({"webui": False, "telegram": False}))
            self.assertTrue(digest_was_delivered({"webui": True}))
            self.assertTrue(digest_was_delivered({"email": "ok"}))

    def test_submitted_notice_asks_for_a_follow_up_after_silence(self) -> None:
        from navin.tenders.watch import pending_alerts

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            old = dt.datetime.now().timestamp() - 40 * 86400
            store.save_tenders(
                [{**_notice(reference="SUB-1"), "stage": "submitted", "go": True, "submitted_at": old}]
            )
            events = pending_alerts(store)
            self.assertEqual([event["kind"] for event in events], ["relance"])
            self.assertGreaterEqual(events[0]["days"], 21)

    def test_snapshot_exposes_what_is_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.save_tenders([{**_notice(reference="GO-2"), "stage": "go", "go": True, "score": 80}])
            with patch("navin.webui.tenders_api._store", return_value=store):
                snap = handle_tenders_action("snapshot")
            self.assertGreaterEqual(snap["follow_up"]["pending"], 1)
            self.assertTrue(snap["follow_up"]["events"])


class TendersCrmBridgeTest(unittest.TestCase):
    def test_go_notices_land_in_the_crm_once(self) -> None:
        from navin.crm.store import list_records
        from navin.tenders.crm_sync import sync_crm

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            store = TenderStore(Path(tmp) / "tenders")
            store.save_profile({"name": "Acme", "currency": "EUR"})
            store.save_tenders(
                [
                    {**_notice(reference="GO-1"), "stage": "go", "go": True, "score": 82},
                    {**_notice(reference="NO-1", title="Espaces verts"), "stage": "no-go", "go": False},
                ]
            )
            with patch("navin.tenders.crm_sync.project_root", return_value=project):
                first = sync_crm(store)
                second = sync_crm(store)
            self.assertEqual(first["created"], 1, "only the notice you chase becomes a deal")
            self.assertEqual(first["updated"], 0)
            self.assertEqual(second["created"], 0, "a second sync must not duplicate the deal")
            self.assertEqual(second["updated"], 1)
            opps = list_records(project, "opportunities")
            self.assertEqual(len(opps), 1)
            self.assertEqual(opps[0]["stage"], "qualifie")
            self.assertEqual(opps[0]["amount"], 180_000.0)
            self.assertEqual(opps[0]["source"], "tenders")
            companies = list_records(project, "companies")
            self.assertEqual([row["name"] for row in companies], ["DINUM"])
            self.assertTrue(store.get(_notice(reference="GO-1")["id"])["crm_opportunity_id"])

    def test_stage_changes_move_the_deal(self) -> None:
        from navin.crm.store import list_records
        from navin.tenders.crm_sync import sync_crm

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            store = TenderStore(Path(tmp) / "tenders")
            store.save_profile({"name": "Acme"})
            notice = {**_notice(reference="GO-9"), "stage": "go", "go": True, "score": 78}
            store.save_tenders([notice])
            with patch("navin.tenders.crm_sync.project_root", return_value=project):
                sync_crm(store)
                store.patch(notice["id"], {"stage": "submitted"})
                sync_crm(store)
            self.assertEqual(list_records(project, "opportunities")[0]["stage"], "negociation")


class TendersSendTest(unittest.TestCase):
    def _ready(self, tmp: str) -> tuple[TenderStore, dict]:
        store = TenderStore(Path(tmp))
        store.save_profile({"name": "Acme", "send_mode": "approval"})
        notice = _notice(reference="SEND-1")
        store.save_tenders([notice])
        return store, notice

    def test_approval_mode_refuses_a_silent_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, notice = self._ready(tmp)
            with patch("navin.webui.tenders_api._store", return_value=store):
                handle_tenders_action("mail", {"id": notice["id"], "kind": "clarification"})
                with self.assertRaises(TenderError) as ctx:
                    handle_tenders_action("send", {"id": notice["id"], "to": "buyer@example.com"})
            self.assertEqual(ctx.exception.status, 409)
            self.assertIn("approval", ctx.exception.message.lower())

    def test_draft_mode_never_sends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, notice = self._ready(tmp)
            store.save_profile({"send_mode": "draft"})
            with patch("navin.webui.tenders_api._store", return_value=store):
                with self.assertRaises(TenderError) as ctx:
                    handle_tenders_action(
                        "send",
                        {"id": notice["id"], "to": "buyer@example.com", "approved": True},
                    )
            self.assertEqual(ctx.exception.status, 409)

    def test_approved_send_uses_the_last_draft_and_leaves_a_trail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, notice = self._ready(tmp)
            with patch("navin.webui.tenders_api._store", return_value=store):
                handle_tenders_action("mail", {"id": notice["id"], "kind": "clarification"})
                with patch("navin.tenders.crm_sync.project_root", side_effect=TenderError("no project")):
                    with patch("navin.crm.outreach._send_email") as smtp:
                        snap = handle_tenders_action(
                            "send",
                            {
                                "id": notice["id"],
                                "to": "buyer@example.com",
                                "approved": True,
                                "kind": "clarification",
                            },
                        )
            self.assertEqual(smtp.call_count, 1)
            dest, subject, body = smtp.call_args.args
            self.assertEqual(dest, "buyer@example.com")
            self.assertIn(notice["title"], body)
            self.assertTrue(subject)
            self.assertEqual(snap["send"]["to"], "buyer@example.com")
            row = next(item for item in snap["tenders"] if item["id"] == notice["id"])
            self.assertTrue(row["mail"][-1]["sent"])
            self.assertTrue(any(event["kind"] == "send" for event in snap["journal"]))

    def test_send_picks_the_named_draft_when_several_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, notice = self._ready(tmp)
            store.patch(
                notice["id"],
                {
                    "mail": [
                        {"kind": "clarification", "body": "CLARIF BODY"},
                        {"kind": "relance", "body": "RELANCE BODY"},
                    ]
                },
            )
            with patch("navin.webui.tenders_api._store", return_value=store):
                with patch("navin.tenders.crm_sync.project_root", side_effect=TenderError("no project")):
                    with patch("navin.crm.outreach._send_email") as smtp:
                        handle_tenders_action(
                            "send",
                            {
                                "id": notice["id"],
                                "to": "buyer@example.com",
                                "approved": True,
                                "kind": "clarification",
                            },
                        )
            dest, subject, body = smtp.call_args.args
            self.assertEqual(dest, "buyer@example.com")
            self.assertIn("CLARIF BODY", body)
            self.assertNotIn("RELANCE BODY", body)

    def test_autonomous_send_does_not_need_a_desk_click(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, notice = self._ready(tmp)
            store.save_profile({"send_mode": "autonomous"})
            with patch("navin.webui.tenders_api._store", return_value=store):
                handle_tenders_action("mail", {"id": notice["id"], "kind": "clarification"})
                with patch("navin.tenders.crm_sync.project_root", side_effect=TenderError("no project")):
                    with patch("navin.crm.outreach._send_email") as smtp:
                        snap = handle_tenders_action(
                            "send",
                            {"id": notice["id"], "to": "buyer@example.com"},
                        )
            self.assertEqual(smtp.call_count, 1)
            row = next(item for item in snap["tenders"] if item["id"] == notice["id"])
            self.assertTrue(row["mail"][-1]["sent"])

    def test_follow_dry_run_does_not_push_a_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, notice = self._ready(tmp)
            store.save_tenders([{**notice, "stage": "go", "go": True, "score": 80}])
            with patch("navin.webui.tenders_api._store", return_value=store):
                with patch("navin.tenders.watch.deliver_alert") as deliver:
                    snap = handle_tenders_action("follow", {"send": "false"})
            deliver.assert_not_called()
            self.assertGreaterEqual(snap["watch"]["count"], 1)
            self.assertEqual(snap["watch"]["sent"], {})
            row = store.get(notice["id"])
            self.assertFalse(row.get("alerts_sent"))

    def test_send_without_a_draft_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, notice = self._ready(tmp)
            with patch("navin.webui.tenders_api._store", return_value=store):
                with self.assertRaises(TenderError):
                    handle_tenders_action(
                        "send",
                        {"id": notice["id"], "to": "buyer@example.com", "approved": True},
                    )


class TendersGuardTest(unittest.TestCase):
    def test_watch_send_flag_truth_table(self) -> None:
        self.assertTrue(_watch_should_send(None))
        self.assertTrue(_watch_should_send(""))
        self.assertTrue(_watch_should_send(True))
        self.assertTrue(_watch_should_send("true"))
        self.assertTrue(_watch_should_send("yes"))
        self.assertFalse(_watch_should_send(False))
        self.assertFalse(_watch_should_send(0))
        self.assertFalse(_watch_should_send("0"))
        self.assertFalse(_watch_should_send("false"))
        self.assertFalse(_watch_should_send("FALSE"))
        self.assertFalse(_watch_should_send("no"))
        self.assertFalse(_watch_should_send("off"))

    def test_heartbeat_detector_and_loop_denylist(self) -> None:
        self.assertFalse(is_heartbeat_turn())
        with request_context(
            RequestContext(channel="cli", chat_id="1", session_key="heartbeat")
        ):
            self.assertTrue(is_heartbeat_turn())
        with request_context(
            RequestContext(
                channel="cli",
                chat_id="1",
                session_key="webui:1",
                metadata={"heartbeat": True},
            )
        ):
            self.assertTrue(is_heartbeat_turn())
        with request_context(
            RequestContext(channel="cli", chat_id="1", session_key="webui:1")
        ):
            self.assertFalse(is_heartbeat_turn())
        locked = AgentLoop._locked_denied_tools(None, {"heartbeat": True})
        live = AgentLoop._denied_tools(None, {"heartbeat": True})
        for name in HEARTBEAT_DENIED_TOOLS:
            self.assertIn(name, locked, name)
            self.assertIn(name, live, name)
        self.assertNotIn("tenders", locked)
        self.assertNotIn("career", locked)
        self.assertNotIn("exec", locked)
        self.assertNotIn("scrape", AgentLoop._locked_denied_tools(None, {}))
        self.assertTrue({"follow", "watch", "status", "file", "read-file"} <= set(HEARTBEAT_TENDERS_ACTIONS))
        self.assertNotIn("collect", HEARTBEAT_TENDERS_ACTIONS)
        self.assertNotIn("send", HEARTBEAT_TENDERS_ACTIONS)
        self.assertNotIn("write", HEARTBEAT_TENDERS_ACTIONS)
        self.assertNotIn("start", HEARTBEAT_TENDERS_ACTIONS)
        self.assertNotIn("stop", HEARTBEAT_TENDERS_ACTIONS)
        self.assertNotIn("schedule", HEARTBEAT_TENDERS_ACTIONS)
        self.assertNotIn("tick", HEARTBEAT_TENDERS_ACTIONS)
        self.assertEqual(normalize_tenders_action("read-file"), "file")
        self.assertEqual(normalize_tenders_action("draft"), "write")
        self.assertEqual(normalize_tenders_action("score"), "qualify")
        self.assertEqual(normalize_tenders_action("follow-up"), "follow")
        self.assertTrue(
            AgentLoop._is_heartbeat_metadata(None, {"session_key": "heartbeat"})
        )
        self.assertFalse(AgentLoop._is_heartbeat_metadata(None, {"session_key": "webui:1"}))
        by_session = AgentLoop._locked_denied_tools(None, {"session_key": "heartbeat"})
        for name in HEARTBEAT_DENIED_TOOLS:
            self.assertIn(name, by_session, name)

    def test_api_heartbeat_refuses_collect_even_without_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme"})
            ctx = RequestContext(
                channel="telegram",
                chat_id="1",
                session_key="heartbeat",
                metadata={"heartbeat": True},
            )
            with patch("navin.webui.tenders_api._store", return_value=store):
                with request_context(ctx):
                    with self.assertRaises(TenderError) as ctx_err:
                        handle_tenders_action("collect")
                    with self.assertRaises(TenderError):
                        handle_tenders_action("send", {"id": "tn-x", "to": "a@b.c"})
                    with self.assertRaises(TenderError):
                        handle_tenders_action("notify", {"title": "x"})
                    with self.assertRaises(TenderError):
                        handle_tenders_action("start", {"schedule": {"kind": "daily", "hour": 9}})
                    with self.assertRaises(TenderError):
                        handle_tenders_action("stop")
                    with self.assertRaises(TenderError):
                        handle_tenders_action("schedule", {"schedule": {"kind": "daily", "hour": 6}})
                    with self.assertRaises(TenderError):
                        handle_tenders_action("tick", {"force": True})
                    snap = handle_tenders_action("snapshot")
            self.assertEqual(ctx_err.exception.status, 403)
            self.assertIn("heartbeat", ctx_err.exception.message.lower())
            self.assertIn("tenders", snap)

    def test_agent_autonomous_send_does_not_need_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme", "send_mode": "autonomous"})
            notice = _notice(reference="AUTO-AGENT")
            store.save_tenders([notice])
            with patch("navin.webui.tenders_api._store", return_value=store):
                handle_tenders_action("mail", {"id": notice["id"], "kind": "clarification"})
                with patch("navin.tenders.crm_sync.project_root", side_effect=TenderError("no project")):
                    with patch("navin.crm.outreach._send_email") as smtp:
                        result = asyncio.run(
                            TendersTool().execute(
                                action="send",
                                id=notice["id"],
                                to="buyer@example.com",
                            )
                        )
            self.assertFalse(getattr(result, "is_error", False), result)
            self.assertEqual(smtp.call_count, 1)
            self.assertEqual(result["send"]["to"], "buyer@example.com")

    def test_follow_send_true_marks_alerts_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme"})
            notice = _notice(reference="GO-MARK")
            store.save_tenders([{**notice, "stage": "go", "go": True, "score": 80}])
            with patch("navin.webui.tenders_api._store", return_value=store):
                with patch("navin.tenders.watch.deliver_alert", return_value={"webui": True}):
                    first = handle_tenders_action("follow")
                    second = handle_tenders_action("follow")
            self.assertGreaterEqual(first["watch"]["count"], 1)
            self.assertTrue(first["watch"].get("delivered"))
            self.assertEqual(second["watch"]["count"], 0)
            self.assertIn("go", store.get(notice["id"]).get("alerts_sent") or [])

    def test_profile_keeps_methodology_from_the_wizard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with patch("navin.webui.tenders_api._store", return_value=store):
                handle_tenders_action(
                    "profile",
                    {
                        "name": "Atelier",
                        "methodology": "Design then build.",
                        "country": "FR",
                    },
                )
            self.assertEqual(store.load_profile()["methodology"], "Design then build.")

    def test_profile_keeps_team_price_book_and_legal_clauses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with patch("navin.webui.tenders_api._store", return_value=store):
                handle_tenders_action(
                    "profile",
                    {
                        "name": "Atelier",
                        "team": [{"name": "Ada", "role": "Lead"}],
                        "price_book": [{"item": "Day", "amount": "800"}],
                        "legal_clauses": "NDA on file.",
                    },
                )
            saved = store.load_profile()
            self.assertEqual(saved["team"][0]["name"], "Ada")
            self.assertEqual(saved["price_book"][0]["item"], "Day")
            self.assertEqual(saved["legal_clauses"], "NDA on file.")

    def test_audit_closeout_keeps_tools_report_skills_and_gateway_tick(self) -> None:
        self.assertEqual(kept_tools_for_module("tenders"), frozenset(TENDERS_TOOLS))
        self.assertEqual(kept_tools_for_module("career"), frozenset())
        tenders = AgentLoop._locked_denied_tools(None, {"product_module": "tenders"})
        for name in TENDERS_TOOLS:
            self.assertNotIn(name, tenders, name)
        self.assertIn("career", tenders)
        self.assertIn("leads", tenders)
        mixed = AgentLoop._locked_denied_tools(
            None, {"product_module": "tenders", "heartbeat": True}
        )
        self.assertIn("scrape", mixed)
        self.assertIn("cron", mixed)
        self.assertNotIn("tenders", mixed)
        self.assertNotIn("open_preview", mixed)
        _title, _skills, brief = _WORKFLOW_BRIEFS["/tenders"]
        self.assertNotIn("knowledge base", brief)
        self.assertIn("tenders action=knowledge", brief)
        commands = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("tick_heartbeat_desks", commands)
        self.assertLess(commands.find("tick_heartbeat_desks"), commands.find("HEARTBEAT.md missing"))
        self.assertIn("career_note", commands)
        desks = (ROOT / "navin/gateway/heartbeat_desks.py").read_text(encoding="utf-8")
        self.assertIn("from navin.tenders.heartbeat import heartbeat_prompt_note, tick_watch", desks)
        self.assertIn("career_tick", desks)
        heartbeat = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        template = (ROOT / "navin/templates/HEARTBEAT.md").read_text(encoding="utf-8")
        for body in (heartbeat, template):
            self.assertIn("tenders action=follow", body)
            self.assertIn("gateway already ran", body)
            self.assertIn("Never collect", body)
            self.assertIn("Studio Start loop", body)
            self.assertIn("Do not create a chat cron", body)
            self.assertNotIn("unless the user explicitly asks", body)

    def test_gateway_watch_tick_needs_a_company_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            self.assertFalse(profile_is_armed(store.load_profile()))
            with patch("navin.tenders.watch.run_watch") as watch:
                self.assertIsNone(tick_watch(store))
            watch.assert_not_called()
            store.save_profile({"name": "Acme"})
            self.assertTrue(profile_is_armed(store.load_profile()))
            with patch("navin.tenders.watch.run_watch", return_value={"count": 0}) as watch:
                self.assertEqual(tick_watch(store), {"count": 0})
            watch.assert_called_once()
            self.assertTrue(watch.call_args.kwargs.get("send"))

    def test_agent_aliases_reach_the_desk_and_stay_blocked_on_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme"})
            notice = _notice(reference="ALIAS-1")
            store.save_tenders([{**notice, "stage": "go", "go": True, "score": 80}])
            ctx = RequestContext(
                channel="telegram",
                chat_id="1",
                session_key="heartbeat",
                metadata={"heartbeat": True},
            )
            tool = TendersTool()
            with patch("navin.webui.tenders_api._store", return_value=store):
                with request_context(ctx):
                    follow = asyncio.run(tool.execute(action="follow-up", send="false"))
                    draft = asyncio.run(tool.execute(action="draft", id=notice["id"]))
                    score = asyncio.run(tool.execute(action="score", id=notice["id"]))
            self.assertFalse(getattr(follow, "is_error", False), follow)
            self.assertIn("watch", follow)
            self.assertTrue(getattr(draft, "is_error", False))
            self.assertIn("heartbeat", str(draft).lower())
            self.assertTrue(getattr(score, "is_error", False))

    def test_agent_aliases_write_and_read_off_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(
                {
                    "name": "Atelier",
                    "country": "FR",
                    "currency": "EUR",
                    "crafts": ["Data"],
                    "countries": ["FR"],
                    "source_ids": ["ted"],
                    "wizard_complete": True,
                }
            )
            notice = _notice(reference="ALIAS-WRITE")
            store.save_tenders([notice])
            with patch("navin.webui.tenders_api._store", return_value=store):
                scored = asyncio.run(TendersTool().execute(action="score", id=notice["id"]))
                drafted = asyncio.run(TendersTool().execute(action="draft", id=notice["id"]))
            self.assertFalse(getattr(scored, "is_error", False), scored)
            self.assertFalse(getattr(drafted, "is_error", False), drafted)
            row = store.get(notice["id"])
            self.assertIsNotNone(row.get("score"))
            self.assertTrue(row.get("response") or row.get("analysis"))

    def test_read_file_alias_returns_an_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme"})
            item = store.save_upload(
                kind="word_template",
                name="house.docx",
                data=base64.b64encode(_office_docx("House style extract")).decode("ascii"),
            )
            with patch("navin.webui.tenders_api._store", return_value=store):
                result = asyncio.run(
                    TendersTool().execute(action="read-file", id=item["file_id"])
                )
                underscored = asyncio.run(
                    TendersTool().execute(action="read_file", id=item["file_id"])
                )
            self.assertFalse(getattr(result, "is_error", False), result)
            self.assertIn("file", result)
            self.assertEqual(result["file"]["file_id"], item["file_id"])
            self.assertIn("House style extract", result["file"].get("text") or result["file"].get("excerpt") or "")
            self.assertFalse(getattr(underscored, "is_error", False), underscored)
            self.assertEqual(underscored["file"]["file_id"], item["file_id"])
            buf = io.StringIO()
            with patch("navin.webui.tenders_api._store", return_value=store):
                with patch("sys.argv", ["navin.tenders.desk_cli", "read-file"]):
                    with patch("sys.stdin", io.StringIO(json.dumps({"id": item["file_id"]}))):
                        with patch("sys.stdout", buf):
                            from navin.tenders.desk_cli import main

                            self.assertEqual(main(), 0)
            cli = json.loads(buf.getvalue())
            self.assertEqual(cli["file"]["file_id"], item["file_id"])
            self.assertIn("House style extract", cli["file"].get("text") or "")

    def test_default_tick_watch_uses_the_runtime_store(self) -> None:
        fake = SimpleNamespace(load_profile=lambda: {})
        with patch("navin.tenders.heartbeat.TenderStore", return_value=fake) as ctor:
            with patch("navin.tenders.watch.run_watch") as watch:
                self.assertIsNone(tick_watch())
        ctor.assert_called_once()
        watch.assert_not_called()

    def test_report_skills_stay_tenders_only(self) -> None:
        self.assertEqual(extra_preload_skills_for_module("career"), [])
        self.assertEqual(extra_preload_skills_for_module(None), [])
        self.assertEqual(extra_preload_skills_for_module("code"), [])
        loop = AgentLoop.__new__(AgentLoop)
        career = loop._preload_skills_for_message(SimpleNamespace(metadata={"product_module": "career"}))
        self.assertNotIn("studio-html-report", career or [])

    def test_tick_watch_pushes_a_real_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme"})
            notice = _notice(reference="TICK-1")
            store.save_tenders([{**notice, "stage": "go", "go": True, "score": 88}])
            with patch("navin.tenders.watch.deliver_alert", return_value={"webui": True}) as deliver:
                first = tick_watch(store)
                second = tick_watch(store)
            self.assertGreaterEqual(first["count"], 1)
            self.assertTrue(first.get("delivered"))
            deliver.assert_called_once()
            self.assertEqual(second["count"], 0)

    def test_tick_heartbeat_desks_isolates_failures_and_ticks_tenders(self) -> None:
        from navin.career.store import CareerStore
        from navin.gateway.heartbeat_desks import tick_heartbeat_desks

        payload = {"count": 2, "digest": "Match: Staff Data Engineer (Paris Co)"}
        with patch("navin.tenders.heartbeat.tick_watch", side_effect=RuntimeError("tenders down")) as tenders:
            with patch("navin.career.heartbeat.tick_watch", return_value=payload) as career:
                with patch("navin.leads.heartbeat.tick_watch", return_value=None):
                    with patch("navin.marketing.heartbeat.tick_watch", return_value=None):
                        note = tick_heartbeat_desks()
        tenders.assert_called_once()
        career.assert_called_once()
        self.assertIn("watch.count=2", note)
        self.assertIn("Staff Data Engineer", note)

        marker = object()
        with patch("navin.tenders.heartbeat.tick_watch", return_value=None) as tenders:
            with patch("navin.career.heartbeat.tick_watch", side_effect=RuntimeError("career down")):
                with patch("navin.leads.heartbeat.tick_watch", return_value=None):
                    with patch("navin.marketing.heartbeat.tick_watch", return_value=None):
                        with patch("navin.trading.heartbeat.tick_watch", return_value=None):
                            note = tick_heartbeat_desks(tenders_store=marker)
        self.assertEqual(note, "")
        tenders.assert_called_once_with(marker)

        with tempfile.TemporaryDirectory() as tmp:
            tenders_root = Path(tmp) / "tenders"
            career_root = Path(tmp) / "career"
            tenders_store = TenderStore(tenders_root)
            career_store = CareerStore(career_root)
            tenders_store.save_profile({"name": "Acme"})
            notice = _notice(reference="TICK-DESK")
            tenders_store.save_tenders([{**notice, "stage": "go", "go": True, "score": 91}])
            with patch("navin.tenders.watch.deliver_alert", return_value={"webui": True}) as deliver:
                with patch("navin.leads.heartbeat.tick_watch", return_value=None):
                    with patch("navin.marketing.heartbeat.tick_watch", return_value=None):
                        with patch("navin.trading.heartbeat.tick_watch", return_value=None):
                            note = tick_heartbeat_desks(
                                tenders_store=tenders_store,
                                career_store=career_store,
                            )
            self.assertIn("Tenders watch already ran", note)
            self.assertIn("watch.count=1", note)
            deliver.assert_called_once()
            self.assertIn("go", tenders_store.get(notice["id"]).get("alerts_sent") or [])

    def test_api_heartbeat_allows_read_and_follow_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme"})
            notice = _notice(reference="HB-READ")
            store.save_tenders([{**notice, "stage": "go", "go": True, "score": 80}])
            item = store.save_upload(
                kind="word_template",
                name="house.docx",
                data=base64.b64encode(_office_docx("House style extract")).decode("ascii"),
            )
            ctx = RequestContext(
                channel="telegram",
                chat_id="1",
                session_key="heartbeat",
                metadata={"heartbeat": True},
            )
            with patch("navin.webui.tenders_api._store", return_value=store):
                with request_context(ctx):
                    file_payload = handle_tenders_action("read-file", {"id": item["file_id"]})
                    got = handle_tenders_action("get", {"id": notice["id"]})
                    indexed = handle_tenders_action("index")
                    follow = handle_tenders_action("follow-up", {"send": "false"})
                    with self.assertRaises(TenderError) as draft_err:
                        handle_tenders_action("draft", {"id": notice["id"]})
                    with self.assertRaises(TenderError) as score_err:
                        handle_tenders_action("score", {"id": notice["id"]})
                    with self.assertRaises(TenderError) as archive_err:
                        handle_tenders_action("archive", {"id": notice["id"]})
                    with self.assertRaises(TenderError) as delete_err:
                        handle_tenders_action("delete", {"id": notice["id"]})
            self.assertEqual(file_payload["file"]["file_id"], item["file_id"])
            self.assertIn("House style extract", file_payload["file"].get("text") or "")
            self.assertEqual(got["notice"]["id"], notice["id"])
            self.assertIn("book", indexed.get("files") or indexed)
            self.assertIn("watch", follow)
            self.assertEqual(draft_err.exception.status, 403)
            self.assertEqual(score_err.exception.status, 403)
            self.assertEqual(archive_err.exception.status, 403)
            self.assertEqual(delete_err.exception.status, 403)

    def test_search_matches_desk_haystack_fields(self) -> None:
        rows = [
            {
                **_notice(reference="HAY-1"),
                "go_reason": "ISO 27001 on file",
                "score": 88,
                "analysis": {"gaps": "need a named DPO"},
                "response": {"letter": "Madame la commission"},
            }
        ]
        self.assertEqual(search_notices(rows, "27001")[0]["id"], rows[0]["id"])
        self.assertEqual(search_notices(rows, "DPO")[0]["id"], rows[0]["id"])
        self.assertEqual(search_notices(rows, "commission")[0]["id"], rows[0]["id"])
        self.assertEqual(search_notices(rows, "88")[0]["id"], rows[0]["id"])
        self.assertEqual(search_notices(rows, "missing-token"), [])
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme"})
            store.save_tenders(rows)
            with patch("navin.webui.tenders_api._store", return_value=store):
                api_hits = handle_tenders_action("search", {"query": "DPO"})
            self.assertGreaterEqual(api_hits["count"], 1)
            self.assertEqual(api_hits["hits"][0]["id"], rows[0]["id"])


class TendersRetentionTest(unittest.TestCase):
    def test_defaults_are_45_and_60_days(self) -> None:
        from navin.tenders.retention import ARCHIVE_AFTER_DAYS, DELETE_AFTER_DAYS, retention_days

        self.assertEqual(ARCHIVE_AFTER_DAYS, 45)
        self.assertEqual(DELETE_AFTER_DAYS, 60)
        self.assertEqual(default_profile()["archive_after_days"], 45)
        self.assertEqual(default_profile()["delete_after_days"], 60)
        self.assertEqual(retention_days({}), (45, 60))
        self.assertEqual(retention_days({"archive_after_days": 90, "delete_after_days": 30}), (90, 90))

    def test_profile_retention_days_reach_the_snapshot(self) -> None:
        from navin.tenders.desk import snapshot

        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            saved = store.save_profile({"archive_after_days": 20, "delete_after_days": 90})
            self.assertEqual(saved["archive_after_days"], 20)
            self.assertEqual(saved["delete_after_days"], 90)
            snap = snapshot(store)
            self.assertEqual(snap["profile"]["archive_after_days"], 20)
            self.assertEqual(snap["profile"]["delete_after_days"], 90)
            self.assertEqual(snap["retention"]["archive_after_days"], 20)
            self.assertEqual(snap["retention"]["delete_after_days"], 90)

    def test_apply_retention_archives_then_deletes(self) -> None:
        from navin.tenders.retention import apply_retention

        now = 2_000_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            fresh = {**_notice(reference="RET-FRESH"), "fetched_at": now - 10 * 86400}
            old = {**_notice(reference="RET-OLD"), "fetched_at": now - 50 * 86400}
            stale = {**_notice(reference="RET-STALE"), "fetched_at": now - 70 * 86400}
            store.save_tenders([fresh, old, stale])
            result = apply_retention(store, now=now)
            ids = {row["id"] for row in store.load_tenders()}
            self.assertEqual(result["archived"], 1)
            self.assertEqual(result["deleted"], 1)
            self.assertIn(fresh["id"], ids)
            self.assertIn(old["id"], ids)
            self.assertNotIn(stale["id"], ids)
            self.assertTrue(store.get(old["id"])["archived"])
            self.assertFalse(store.get(fresh["id"])["archived"])

    def test_desk_actions_favorite_archive_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            notice = _notice(reference="RET-ACT")
            store.save_tenders([notice])
            with patch("navin.webui.tenders_api._store", return_value=store):
                starred = handle_tenders_action("star", {"id": notice["id"]})
                self.assertTrue(next(row for row in starred["tenders"] if row["id"] == notice["id"])["favorite"])
                parked = handle_tenders_action("archive", {"id": notice["id"]})
                parked_row = next(row for row in parked["tenders"] if row["id"] == notice["id"])
                self.assertTrue(parked_row["archived"])
                self.assertEqual(parked["retention"]["archived"], 1)
                self.assertEqual(parked["retention"]["favorites"], 0)
                restored = handle_tenders_action("unarchive", {"id": notice["id"]})
                restored_row = next(row for row in restored["tenders"] if row["id"] == notice["id"])
                self.assertFalse(restored_row["archived"])
                self.assertTrue(restored_row["favorite"])
                gone = handle_tenders_action("delete", {"id": notice["id"]})
            self.assertEqual(gone["deleted"]["id"], notice["id"])
            self.assertEqual(gone["tenders"], [])

    def test_search_includes_archived_notices_by_default(self) -> None:
        from navin.tenders.index import search_notices

        live = {**_notice(reference="RET-LIVE"), "title": "Live cloud lot"}
        parked = {**_notice(reference="RET-PARK"), "title": "Parked cloud lot", "archived": True}
        hits = search_notices([live, parked], "cloud")
        self.assertEqual({row["id"] for row in hits}, {live["id"], parked["id"]})
        live_only = search_notices([live, parked], "cloud", include_archived=False)
        self.assertEqual([row["id"] for row in live_only], [live["id"]])
        archived = search_notices([live, parked], "cloud", stage="archived")
        self.assertEqual([row["id"] for row in archived], [parked["id"]])

    def test_search_filters_country_sector_and_dates(self) -> None:
        from navin.tenders.index import search_notices

        fr = {
            **_notice(reference="FLT-FR"),
            "country": "FR",
            "sector": "Cloud",
            "deadline": "2026-09-15",
            "publication_date": "2026-08-01",
            "score": 80,
            "budget": 50000,
        }
        ae = {
            **_notice(reference="FLT-AE"),
            "country": "AE",
            "sector": "Works",
            "deadline": "2026-10-01",
            "publication_date": "2026-08-20",
            "score": 40,
            "budget": 1000,
        }
        hits = search_notices(
            [fr, ae],
            "",
            country="FR",
            sector="Cloud",
            deadline_from="2026-09-01",
            deadline_to="2026-09-30",
            min_score=70,
            min_budget=10000,
        )
        self.assertEqual([row["id"] for row in hits], [fr["id"]])
        both = search_notices([fr, ae], "", countries="FR,AE")
        self.assertEqual({row["id"] for row in both}, {fr["id"], ae["id"]})


class TendersStoreTest(unittest.TestCase):
    def test_profile_and_upsert_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            self.assertEqual(store.load_profile()["send_mode"], "approval")
            with self.assertRaises(TenderError):
                store.save_profile({"send_mode": "yolo"})
            first = _notice(reference="AO-1")
            store.upsert_tenders([first])
            again = dict(first)
            again["deadline"] = "2099-11-01"
            store.upsert_tenders([again])
            rows = store.load_tenders()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["deadline"], "2099-11-01")
            self.assertTrue(rows[0]["history"])
            with self.assertRaises(TenderError) as ctx:
                store.get("tn-missing")
            self.assertEqual(ctx.exception.status, 404)


class TendersWizardStoreTest(unittest.TestCase):
    def test_empty_defaults_are_not_ready(self) -> None:
        profile = default_profile()
        self.assertEqual(profile["crafts"], [])
        self.assertEqual(profile["countries"], [])
        self.assertFalse(wizard_ready(profile))
        self.assertIn("slack", default_channels())
        self.assertIn("slack_to", default_channels())

    def test_wizard_ready_needs_sources(self) -> None:
        profile = default_profile()
        profile.update(
            {
                "name": "Acme",
                "country": "FR",
                "currency": "EUR",
                "specialty": "Cloud",
                "countries": ["FR"],
                "crafts": ["Cloud"],
            }
        )
        self.assertFalse(wizard_ready(profile))
        profile["source_ids"] = ["ted"]
        self.assertFalse(wizard_ready(profile))
        profile["wizard_complete"] = True
        self.assertTrue(wizard_ready(profile))

    def test_upload_allowlist_and_custom_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with self.assertRaises(TenderError):
                store.save_upload(kind="word_template", name="offer.txt", data="YQ==")
            with self.assertRaises(TenderError):
                store.save_upload(kind="word_template", name="offer.docx", data="")
            item = store.add_custom_source(
                {
                    "name": "Portal X",
                    "url": "https://example.gov/tenders",
                    "api": "https://example.gov/api",
                    "country": "SN",
                    "zone": "africa",
                    "ingest": "api",
                    "api_key": "secret-key",
                }
            )
            self.assertEqual(item["country"], "SN")
            self.assertTrue(store.has_secret("custom:portal-x"))
            store.save_secret("sam_gov", "abc")
            self.assertTrue(store.has_secret("sam_gov"))
            with patch("navin.webui.tenders_api._store", return_value=store):
                snap = handle_tenders_action("snapshot")
                self.assertFalse(snap["wizard_ready"])
                self.assertTrue(snap["profile"]["has_sam_key"])
                custom = snap["profile"]["custom_sources"][0]
                self.assertTrue(custom["has_key"])
                self.assertNotIn("api_key", custom)
                self.assertIn("slack", snap["profile"]["channels"])
                self.assertIn("catalog_by_zone", snap)

    def test_collect_honors_source_ids(self) -> None:
        incoming = [_notice(reference="PICK-1")]
        with (
            patch.dict("navin.tenders.collect.FETCHERS", {}, clear=True),
            patch("navin.tenders.collect.scrape_official_net", return_value={"tenders": incoming, "by_source": {}}),
        ):
            result = collect(countries=["FR"], crafts=["Cloud"], use_tools=True, source_ids=["ted"])
        self.assertEqual({row["source_id"] for row in result["reports"]}, {row["id"] for row in catalog()})
        skipped = next(row for row in result["reports"] if row["source_id"] == "boamp")
        self.assertEqual(skipped["kind"], "catalog")
        self.assertIn("not selected", skipped["detail"])


def _office_docx(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return buffer.getvalue()


def _office_pptx(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            f"<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>",
        )
    return buffer.getvalue()


class TendersKnowledgeFilesTest(unittest.TestCase):
    def test_legacy_single_template_becomes_a_list(self) -> None:
        out = normalize_templates({"word": {"file_id": "abc", "name": "offer.docx", "excerpt": "Hello"}})
        self.assertEqual(len(out["word"]), 1)
        self.assertEqual(out["word"][0]["name"], "offer.docx")
        self.assertEqual(out["ppt"], [])

    def test_import_extract_index_save_and_write_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Atelier Docs", "crafts": ["Cloud"], "specialty": "Cloud"})
            word_a = store.save_upload(
                kind="word_template",
                name="voice-a.docx",
                data=base64.b64encode(_office_docx("House style Atelier Cloud")).decode("ascii"),
            )
            word_b = store.save_upload(
                kind="word_template",
                name="voice-b.docx",
                data=base64.b64encode(_office_docx("Second Word model DINUM")).decode("ascii"),
            )
            ppt = store.save_upload(
                kind="ppt_template",
                name="offer.pptx",
                data=base64.b64encode(_office_pptx("Slide reuse Gulf bid")).decode("ascii"),
            )
            ref = store.save_upload(
                kind="reference",
                name="ref-ministere.docx",
                data=base64.b64encode(_office_docx("SI decisionnel ministere 2024")).decode("ascii"),
                meta={"title": "SI decisionnel", "client": "DINUM", "year": "2024"},
            )
            manual = store.add_reference({"title": "Plateforme data", "client": "Banque", "year": "2023"})
            self.assertEqual(len(store.load_profile()["templates"]["word"]), 2)
            self.assertEqual(len(store.load_profile()["templates"]["ppt"]), 1)
            self.assertEqual(len(store.load_profile()["references"]), 2)
            self.assertIn("Atelier Cloud", word_a["excerpt"])
            self.assertTrue((store.files_dir / f"{word_a['file_id']}.txt").is_file())
            self.assertIn("Atelier Cloud", extract_office_text(store.files_dir / word_a["path"]))
            self.assertIn("Gulf bid", extract_office_text(store.files_dir / ppt["path"]))
            paths = write_local_index(store)
            self.assertTrue(Path(paths["knowledge"]).is_file())
            knowledge = Path(paths["knowledge"]).read_text(encoding="utf-8")
            self.assertIn("House style Atelier Cloud", knowledge)
            self.assertIn("SI decisionnel ministere 2024", knowledge)
            self.assertIn("must reuse", knowledge.lower())
            self.assertIn(ref["file_id"] + ".txt", knowledge)
            docs = filed_documents(store.load_profile())
            self.assertGreaterEqual(len(docs), 4)
            notice = _notice()
            notice["score"] = 82
            dossier = build_response(notice, store.load_profile())
            self.assertTrue(dossier["from_file"])
            self.assertIn("House style Atelier Cloud", dossier["letter"])
            self.assertIn("House style Atelier Cloud", dossier["methodology"])
            self.assertIn("Slide reuse Gulf bid", dossier["methodology"])
            self.assertIn("SI decisionnel", dossier["references"])
            self.assertIn("SI decisionnel ministere 2024", dossier["references"])
            self.assertIn("voice-a.docx", dossier["used_files"])
            with patch("navin.webui.tenders_api._store", return_value=store):
                read = handle_tenders_action("file", {"id": word_b["file_id"]})
                self.assertIn("DINUM", read["file"]["text"])
                indexed = handle_tenders_action("index")
                self.assertTrue(any(row.get("file_id") == ref["file_id"] for row in indexed["index"]["knowledge"]))
                status = handle_tenders_action("status")
                self.assertIn("House style Atelier Cloud", status["book"])
                tool = TendersTool()
                payload = asyncio.run(tool.execute(action="file", id=ppt["file_id"]))
                self.assertIn("Gulf bid", payload["file"]["text"])
                store.remove_file(file_id=word_b["file_id"])
                self.assertEqual(len(store.load_profile()["templates"]["word"]), 1)
                store.remove_file(title=manual["title"])
                self.assertEqual(len(store.load_profile()["references"]), 1)


class TendersApiTest(unittest.TestCase):
    def test_snapshot_and_profile_stay_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with patch("navin.webui.tenders_api._store", return_value=store):
                snap = handle_tenders_action("snapshot")
                self.assertIn("profile", snap)
                self.assertEqual(snap["profile"]["send_mode"], "approval")
                self.assertIn("ted", {row["id"] for row in snap["catalog"]})
                saved = handle_tenders_action(
                    "profile",
                    {
                        "name": "Acme",
                        "countries": "FR,SA,TN",
                        "crafts": "AI,Data",
                        "min_score": 70,
                    },
                )
                self.assertEqual(saved["profile"]["name"], "Acme")
                self.assertEqual(saved["profile"]["countries"], ["FR", "SA", "TN"])
                status = handle_tenders_action("status")
                self.assertIn("kpis", status)
                self.assertIn("headline", status["kpis"])
                self.assertIn("channels", status)
                self.assertIn("models", status)
                self.assertIn("tasks", status["models"])
                self.assertIn("telegram", status["channels"])
                self.assertIn("teams", status["channels"])
                wired = handle_tenders_action(
                    "profile",
                    {
                        "channels": {
                            "telegram": True,
                            "whatsapp": True,
                            "email": True,
                            "teams": True,
                            "telegram_to": "123",
                            "whatsapp_to": "+33600000000",
                            "email_to": "ops@example.com",
                            "teams_to": "19:room",
                        }
                    },
                )
                self.assertTrue(wired["profile"]["channels"]["telegram"])
                self.assertTrue(wired["profile"]["channels"]["teams"])
                self.assertEqual(wired["profile"]["channels"]["telegram_to"], "123")
                with patch("navin.tenders.notify.deliver_alert", return_value={"webui": True, "telegram": False}):
                    ping = handle_tenders_action("notify", {"title": "Navin Tenders", "detail": "test"})
                self.assertIn("notify", ping)

    def test_configured_company_gets_a_go_and_profile_change_rescores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_tenders(
                [
                    _notice(),
                    _notice(
                        title="Entretien des espaces verts",
                        description="Tonte, taille et deneigement des voies d'acces.",
                        reference="GREEN-1",
                    ),
                ]
            )
            with patch("navin.webui.tenders_api._store", return_value=store):
                snap = handle_tenders_action(
                    "profile",
                    {
                        "name": "Acme Digital",
                        "specialty": "Data platforms",
                        "country": "FR",
                        "currency": "EUR",
                        "countries": "FR",
                        "crafts": "Data,Cloud",
                        "min_score": 70,
                        "min_budget": 50_000,
                        "references": [
                            {"title": "SI decisionnel ministere", "year": "2024"},
                            {"title": "Plateforme data groupe", "year": "2025"},
                            {"title": "Migration cloud", "year": "2023"},
                        ],
                    },
                )
                rows = {row["title"]: row for row in snap["tenders"]}
                fit = rows["Modernisation du SI decisionnel"]
                miss = rows["Entretien des espaces verts"]
                self.assertTrue(fit["go"], fit["go_reason"])
                self.assertEqual(fit["stage"], "go")
                self.assertFalse(miss["go"])
                self.assertIn("domains", miss["go_reason"])
                self.assertGreater(fit["score"], miss["score"])
                self.assertGreaterEqual(snap["kpis"]["qualified"], 1)
                narrowed = handle_tenders_action("profile", {"crafts": "Bridges"})
                after = {row["title"]: row for row in narrowed["tenders"]}
                self.assertFalse(after["Modernisation du SI decisionnel"]["go"])

    def test_open_kpi_matches_the_desk_in_play_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            notice = _notice()
            store.save_tenders([notice])
            store.save_profile({"name": "Acme", "crafts": "Bridges", "countries": "FR"})
            with patch("navin.webui.tenders_api._store", return_value=store):
                scored = handle_tenders_action("qualify", {"id": notice["id"]})
                self.assertEqual(scored["kpis"]["open"], 0)
                drafted = handle_tenders_action("write", {"id": notice["id"]})
                row = next(item for item in drafted["tenders"] if item["id"] == notice["id"])
                self.assertFalse(row["go"])
                self.assertEqual(row["stage"], "drafting")
                self.assertEqual(drafted["kpis"]["open"], 1)

    def test_full_happy_path_without_inventing_notices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            notice = _notice()
            store.save_tenders([notice])
            store.save_profile({"name": "Acme", "references": [{"title": "Plateforme data 2024"}]})
            with patch("navin.webui.tenders_api._store", return_value=store):
                qualified = handle_tenders_action("qualify", {"id": notice["id"]})
                row = next(item for item in qualified["tenders"] if item["id"] == notice["id"])
                self.assertIsInstance(row["score"], float)
                self.assertIn(row["stage"], {"go", "no-go"})
                self.assertTrue(row["go_reason"])
                written = handle_tenders_action("write", {"id": notice["id"]})
                row = next(item for item in written["tenders"] if item["id"] == notice["id"])
                self.assertEqual(row["stage"], "drafting")
                self.assertIn("Acme", row["response"]["letter"])
                letter_fold = (
                    str(row["response"]["letter"] or "")
                    .lower()
                    .replace("é", "e")
                    .replace("è", "e")
                )
                self.assertIn("decisionnel", letter_fold)
                mailed = handle_tenders_action("mail", {"id": notice["id"], "kind": "clarification"})
                self.assertIn("precisions", mailed["draft"].lower())
                mail_fold = str(mailed["draft"] or "").lower().replace("é", "e").replace("è", "e")
                self.assertIn("decisionnel", mail_fold)
                submitted = handle_tenders_action("stage", {"id": notice["id"], "stage": "submitted"})
                row = next(item for item in submitted["tenders"] if item["id"] == notice["id"])
                self.assertEqual(row["stage"], "submitted")
                won = handle_tenders_action("stage", {"id": notice["id"], "stage": "won"})
                self.assertEqual(won["kpis"]["won"], 1)
                self.assertEqual(won["kpis"]["win_rate"], 100.0)
                known = handle_tenders_action(
                    "knowledge",
                    {"references": [{"title": "Banque mondiale BI"}]},
                )
                self.assertEqual(known["profile"]["references"][0]["title"], "Banque mondiale BI")

    def test_collect_mock_scores_and_does_not_invent_on_failure(self) -> None:
        incoming = [_notice(reference="LIVE-1")]
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with (
                patch("navin.webui.tenders_api._store", return_value=store),
                patch(
                    "navin.tenders.desk.collect",
                    return_value={
                        "tenders": incoming,
                        "reports": [{"source_id": "ted", "ok": True, "detail": "1 TED", "count": 1}],
                        "discovered_sources": [
                            {"host": "example.gov.xx", "url": "https://example.gov.xx/", "hits": 3}
                        ],
                    },
                ),
            ):
                snap = handle_tenders_action("collect")
                self.assertEqual(len(snap["tenders"]), 1)
                self.assertIn(snap["tenders"][0]["stage"], {"go", "no-go"})
                self.assertTrue(snap["collect"][0]["ok"])
                accepted = handle_tenders_action("discover-accept", {"host": "example.gov.xx"})
                self.assertTrue(any(row.get("added") for row in accepted["discoveries"]))

        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with (
                patch("navin.tenders.fetchers.get_json", side_effect=OSError("down")),
                patch("navin.tenders.fetchers.post_json", side_effect=OSError("down")),
                patch("navin.tenders.fetchers.get_bytes", side_effect=OSError("down")),
            ):
                failed = collect(countries=["FR"], use_tools=False)
            self.assertEqual(failed["tenders"], [])
            self.assertTrue(failed["reports"])
            fetch_ids = {row["source_id"] for row in failed["reports"] if row["kind"] == "fetch"}
            self.assertEqual(fetch_ids, {"ted", "world-bank", "boamp"})
            self.assertTrue(all(not row["ok"] for row in failed["reports"] if row["kind"] == "fetch"))
            self.assertEqual({row["source_id"] for row in failed["reports"]}, {row["id"] for row in catalog()})
            self.assertTrue(any(row["kind"] == "covered" for row in failed["reports"]))
            self.assertTrue(any(row["kind"] == "search" for row in failed["reports"]))
            self.assertTrue(any(row["kind"] == "catalog" for row in failed["reports"]))

    def test_unknown_action_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with patch("navin.webui.tenders_api._store", return_value=store):
                with self.assertRaises(TenderError) as ctx:
                    handle_tenders_action("explode")
                self.assertEqual(ctx.exception.status, 400)

    def test_qualify_missing_id_is_400(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with patch("navin.webui.tenders_api._store", return_value=store):
                with self.assertRaises(TenderError) as ctx:
                    handle_tenders_action("qualify", {})
                self.assertEqual(ctx.exception.status, 400)
                with self.assertRaises(TenderError):
                    handle_tenders_action("stage", {"id": "tn-x", "stage": "not-a-stage"})

    def test_stage_go_and_nogo_set_the_go_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            notice = _notice()
            store.save_tenders([notice])
            with patch("navin.webui.tenders_api._store", return_value=store):
                gone = handle_tenders_action("stage", {"id": notice["id"], "stage": "go"})
                row = next(item for item in gone["tenders"] if item["id"] == notice["id"])
                self.assertEqual(row["stage"], "go")
                self.assertTrue(row["go"])
                nogo = handle_tenders_action("stage", {"id": notice["id"], "stage": "no-go"})
                row = next(item for item in nogo["tenders"] if item["id"] == notice["id"])
                self.assertEqual(row["stage"], "no-go")
                self.assertFalse(row["go"])

    def test_agent_tool_status_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with patch("navin.webui.tenders_api._store", return_value=store):
                tool = TendersTool()
                text = asyncio.run(tool.execute(action="status"))
                self.assertIn("Open", text)
                self.assertIn("TENDERS LOCAL BOOK", text)
                self.assertIn("LOOP OFF", text)
                self.assertIn("tenders action=start", text)
                self.assertTrue(tool.call_read_only({"action": "status"}))
                self.assertTrue(tool.call_read_only({"action": "search"}))
                self.assertTrue(tool.call_read_only({"action": "get"}))
                self.assertFalse(tool.call_read_only({"action": "collect"}))
                err = asyncio.run(tool.execute(action="qualify", id=""))
                self.assertTrue(getattr(err, "is_error", True) or "id is required" in str(err))

    def test_local_index_and_agent_can_read_every_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            notice = _notice()
            store.save_profile(
                {
                    "name": "Atelier Demo",
                    "specialty": "Cloud AI",
                    "country": "FR",
                    "currency": "EUR",
                    "crafts": ["AI"],
                    "countries": ["FR"],
                    "brief": "We keep the margin.",
                    "references": [{"title": "SI decisionnel DINUM", "client": "DINUM", "year": "2024"}],
                }
            )
            store.upsert_tenders([notice])
            with patch("navin.webui.tenders_api._store", return_value=store):
                snap = handle_tenders_action("snapshot")
                self.assertTrue((store.root / "index.json").is_file())
                self.assertTrue((store.root / "INDEX.md").is_file())
                self.assertTrue((store.root / "book.md").is_file())
                self.assertTrue((store.root / "dossier.md").is_file())
                self.assertTrue((store.root / "notices.md").is_file())
                book = (store.root / "book.md").read_text(encoding="utf-8")
                self.assertIn(f"Open {snap['kpis']['open']}", book)
                self.assertIn(f"new {snap['kpis']['new']}", book)
                self.assertIn(f"qualified {snap['kpis']['qualified']}", book)
                self.assertIn(f"Wizard ready: {bool(snap['wizard_ready'])}", book)
                self.assertIn("LOOP", book)
                self.assertIn("tenders action=start", book)
                self.assertIn("loop.json", book)
                self.assertIn("files", snap)
                self.assertIn("book", snap)
                self.assertIn(notice["id"], snap["book"])
                self.assertIn("We keep the margin.", snap["book"])
                hits = handle_tenders_action("search", {"query": "decisionnel"})
                self.assertGreaterEqual(hits["count"], 1)
                self.assertEqual(hits["hits"][0]["id"], notice["id"])
                listed = handle_tenders_action("list", {"country": "FR"})
                self.assertTrue(any(row["id"] == notice["id"] for row in listed["hits"]))
                got = handle_tenders_action("get", {"id": notice["id"]})
                self.assertEqual(got["notice"]["id"], notice["id"])
                self.assertEqual(got["card"]["title"], notice["title"])
                tool = TendersTool()
                book = asyncio.run(tool.execute(action="status"))
                self.assertIn(notice["id"], book)
                self.assertIn("Atelier Demo", book)
                found = asyncio.run(tool.execute(action="search", query="DINUM"))
                self.assertGreaterEqual(found["count"], 1)
                opened = asyncio.run(tool.execute(action="get", id=notice["id"]))
                self.assertEqual(opened["notice"]["buyer"], "DINUM")
                briefed = handle_tenders_action("profile", {"brief": "Only public AO."})
                self.assertEqual(briefed["profile"]["brief"], "Only public AO.")


class TendersParserTest(unittest.TestCase):
    def test_country_codes(self) -> None:
        self.assertEqual(_country_code("FRA"), "FR")
        self.assertEqual(_country_code("Senegal"), "SN")
        self.assertEqual(_country_code(["DEU"]), "DE")
        self.assertEqual(_country_code(""), "INTL")

    def test_ted_payload_uses_valid_fields(self) -> None:
        captured: dict[str, object] = {}

        def fake_post(url: str, payload: dict) -> dict:
            captured["url"] = url
            captured["payload"] = payload
            return {
                "notices": [
                    {
                        "publication-number": "123-2026",
                        "notice-title": {"eng": "Cloud data platform", "fra": "Plateforme data"},
                        "buyer-name": {"eng": ["Ville de Paris"]},
                        "buyer-country": "FRA",
                        "publication-date": "2026-08-01",
                        "deadline-receipt-tender-date-lot": "2026-09-30",
                    }
                ]
            }

        with patch("navin.tenders.fetchers.post_json", side_effect=fake_post):
            from navin.tenders.fetchers import from_ted

            rows, detail = from_ted()
        self.assertIn("form-type=competition", str(captured["payload"]["query"]))
        self.assertEqual(rows[0]["buyer"], "Ville de Paris")
        self.assertIn("buyer-name", captured["payload"]["fields"])
        self.assertNotIn("organisation-name-buyer", captured["payload"]["fields"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Cloud data platform")
        self.assertEqual(rows[0]["country"], "FR")
        self.assertIn("TED", detail)

    def test_world_bank_maps_country_and_deadline(self) -> None:
        def fake_get(url: str) -> dict:
            return {
                "procnotices": [
                    {
                        "id": "OP1",
                        "project_name": "Casamance digital",
                        "project_ctry_name": "Senegal",
                        "bid_description": "systeme d'information",
                        "submission_deadline_date": "2026-09-16T00:00:00Z",
                        "notice_type": "Request for Bids",
                        "contact_organization": "APIX",
                    }
                ]
            }

        with patch("navin.tenders.fetchers.get_json", side_effect=fake_get):
            from navin.tenders.fetchers import from_world_bank

            rows, detail = from_world_bank()
        self.assertEqual(rows[0]["country"], "SN")
        self.assertEqual(rows[0]["deadline"], "2026-09-16")
        self.assertIn("OP1", rows[0]["source_url"])
        self.assertIn("World Bank", detail)

    def test_boamp_maps_open_notices(self) -> None:
        def fake_get(url: str) -> dict:
            self.assertIn("datelimitereponse", url)
            return {
                "results": [
                    {
                        "idweb": "26-100",
                        "objet": "Plateforme data ministerielle",
                        "nomacheteur": "DINUM",
                        "datelimitereponse": "2026-10-01T12:00:00+00:00",
                        "dateparution": "2026-08-01",
                        "nature_libelle": "Avis de marche",
                    }
                ]
            }

        with patch("navin.tenders.fetchers.get_json", side_effect=fake_get):
            from navin.tenders.fetchers import from_boamp

            rows, detail = from_boamp()
        self.assertEqual(rows[0]["country"], "FR")
        self.assertEqual(rows[0]["buyer"], "DINUM")
        self.assertIn("26-100", rows[0]["source_url"])
        self.assertIn("BOAMP", detail)

    def test_uk_ocds_skips_cancelled(self) -> None:
        def fake_get(url: str) -> dict:
            return {
                "releases": [
                    {
                        "ocid": "ocds-h6vhtk-1",
                        "id": "1",
                        "date": "2026-08-01",
                        "buyer": {"name": "Cabinet Office"},
                        "tender": {
                            "title": "Cloud data platform",
                            "status": "active",
                            "tenderPeriod": {"endDate": "2026-11-01T12:00:00Z"},
                        },
                    },
                    {
                        "ocid": "ocds-h6vhtk-2",
                        "tender": {"title": "Cancelled chairs", "status": "cancelled"},
                    },
                ]
            }

        with patch("navin.tenders.fetchers.get_json", side_effect=fake_get):
            from navin.tenders.fetchers import from_find_a_tender

            rows, _detail = from_find_a_tender()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["country"], "GB")
        self.assertEqual(rows[0]["title"], "Cloud data platform")

    def test_canadabuys_reads_official_csv(self) -> None:
        csv_text = (
            "title-titre-eng,referenceNumber-numeroReference,tenderStatus-appelOffresStatut-eng,"
            "tenderClosingDate-appelOffresDateCloture,publicationDate-datePublication,"
            "contractingEntityName-nomEntitContractante-eng,noticeURL-URLavis-eng,"
            "tenderDescription-descriptionAppelOffres-eng\n"
            "AI platform,REF-1,Open,2026-10-01,2026-08-01,PSPC,https://canadabuys.canada.ca/t/1,cloud data\n"
            "Closed chairs,REF-2,Expired,2026-01-01,2025-01-01,PSPC,https://canadabuys.canada.ca/t/2,chairs\n"
        )

        with patch("navin.tenders.fetchers.get_bytes", return_value=csv_text.encode("utf-8")):
            from navin.tenders.fetchers import from_canadabuys

            rows, detail = from_canadabuys()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["country"], "CA")
        self.assertEqual(rows[0]["title"], "AI platform")
        self.assertIn("CanadaBuys", detail)


class TendersScrapeNetTest(unittest.TestCase):
    def test_parse_search_hits_and_skip_aggregators(self) -> None:
        text = (
            "Results for: tender\n\n"
            "1. Cloud data platform tender\n"
            "   https://tenders.etimad.sa/notice/42\n"
            "   Official listing\n"
            "2. Paid aggregator dump\n"
            "   https://www.marchesonline.com/ao/1\n"
        )
        hits = parse_search_hits(text)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["url"], "https://tenders.etimad.sa/notice/42")
        self.assertFalse(host_is_official("www.marchesonline.com"))

    def test_scrape_net_keeps_official_hits_and_skips_walls(self) -> None:
        def search(_query: str, _count: int) -> str:
            return (
                "Results for: q\n\n"
                "1. National cloud tender RFP\n"
                "   https://tenders.etimad.sa/notice/42\n"
                "2. Login wall portal\n"
                "   https://www.ungm.org/UNUser/Login\n"
                "3. Invented commercial page\n"
                "   https://example.com/tender/99\n"
            )

        def fetch(urls: list[str]) -> list[dict]:
            out = []
            for url in urls:
                if "Login" in url:
                    out.append(
                        {
                            "url": url,
                            "title": "Sign in",
                            "text": "Please log in to continue",
                            "status": 200,
                            "wall": {
                                "kind": "login",
                                "human": True,
                                "message": "Login wall",
                            },
                        }
                    )
                elif "etimad" in url:
                    out.append(
                        {
                            "url": url,
                            "title": "National cloud tender RFP",
                            "text": "Public procurement notice for a cloud platform.",
                            "status": 200,
                        }
                    )
                else:
                    out.append({"url": url, "title": "Nope", "text": "x", "status": 200})
            return out

        result = scrape_official_net(
            countries=["SA"],
            crafts=["cloud"],
            search_fn=search,
            fetch_fn=fetch,
        )
        self.assertEqual(len(result["tenders"]), 1)
        row = result["tenders"][0]
        self.assertEqual(row["source_id"], "etimad")
        self.assertEqual(row["title"], "National cloud tender RFP")
        self.assertEqual(row["source_url"], "https://tenders.etimad.sa/notice/42")
        self.assertNotEqual(row["title"], "Untitled notice")
        self.assertIn("login", result["by_source"]["ungm"]["walls"])
        self.assertEqual(result["by_source"]["ungm"]["count"], 0)

    def test_collect_merges_scrape_net_without_inventing(self) -> None:
        incoming = [
            {
                "id": "tn-scrape1",
                "source_id": "etimad",
                "country": "SA",
                "title": "National cloud tender RFP",
                "source_url": "https://tenders.etimad.sa/notice/42",
            }
        ]
        with (
            patch("navin.tenders.fetchers.get_json", side_effect=OSError("down")),
            patch("navin.tenders.fetchers.post_json", side_effect=OSError("down")),
            patch("navin.tenders.fetchers.get_bytes", side_effect=OSError("down")),
            patch(
                "navin.tenders.collect.scrape_official_net",
                return_value={
                    "tenders": incoming,
                    "by_source": {
                        "etimad": {
                            "count": 1,
                            "detail": "web_search + scrape on official hosts: 1 notice(s)",
                            "walls": [],
                        }
                    },
                },
            ),
        ):
            result = collect(countries=["SA"], crafts=["cloud"], use_tools=True)
        self.assertEqual(len(result["tenders"]), 1)
        self.assertEqual(result["tenders"][0]["title"], "National cloud tender RFP")
        etimad = next(row for row in result["reports"] if row["source_id"] == "etimad")
        self.assertEqual(etimad["kind"], "search")
        self.assertEqual(etimad["count"], 1)
        self.assertIn("scrape", etimad["detail"])
        self.assertEqual({row["source_id"] for row in result["reports"]}, {row["id"] for row in catalog()})


class TendersLiveCollectTest(unittest.TestCase):
    def test_official_apis_return_real_notices_or_honest_empty(self) -> None:
        countries = list(default_profile()["countries"])
        result = collect(
            countries=countries,
            crafts=list(default_profile()["crafts"]),
            use_tools=False,
        )
        self.assertEqual({row["source_id"] for row in result["reports"]}, {row["id"] for row in catalog()})
        fetch_ids = {row["source_id"] for row in result["reports"] if row["kind"] == "fetch"}
        self.assertTrue({"ted", "world-bank", "boamp", "find-a-tender", "contracts-finder", "canadabuys"} <= fetch_ids)
        self.assertIn("sam-gov", {row["source_id"] for row in result["reports"]})
        for row in result["tenders"]:
            self.assertTrue(str(row["id"]).startswith("tn-"))
            self.assertIn(
                row["source_id"],
                {"ted", "world-bank", "boamp", "find-a-tender", "contracts-finder", "canadabuys", "sam-gov"},
            )
            self.assertTrue(row["title"])
            self.assertNotEqual(row["title"], "Untitled notice")
            self.assertNotIn(EM_DASH, row["title"])
            self.assertNotIn(EN_DASH, row["title"])


if __name__ == "__main__":
    unittest.main()
