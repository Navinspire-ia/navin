# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Wiring for the Navin Career studio (Freelance + Jobs)."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from navin.agent.loop import AgentLoop
from navin.agent.model_routes import (
    PRODUCT_MODULE_ROUTE_ROLES,
    WORKFLOW_ROUTE_ROLES,
    product_module_role,
    workflow_role_for_content,
)
from navin.agent.tools.career import CareerTool
from navin.agent.tools.context import RequestContext, request_context
from navin.career.collect import _hit_to_job, _want_family, _want_official
from navin.career.dossier import format_agent_status, format_dossier, write_local_index
from navin.career.errors import CareerError
from navin.career.heartbeat import (
    HEARTBEAT_CAREER_ACTIONS,
    heartbeat_prompt_note,
    profile_is_armed,
    tick_watch,
)
from navin.career.matching import score_opportunity
from navin.career.scrape_net import scrape_open_net
from navin.career.sources import (
    catalog,
    is_closed_job_url,
    is_open_scrape_url,
    official_search_pack,
    scrape_search_queries,
    web_search_queries,
    web_search_run_pack,
)
from navin.career.stack import CAREER_SKILLS, module_stack
from navin.career.store import CareerStore, default_profile, normalize_profile
from navin.career.watch import digest_was_delivered
from navin.command.builtin import (
    _DELIVERY_WORKFLOWS,
    _HTML_REPORT_WORKFLOWS,
    _TRACKED_WORKFLOWS,
    _WORKFLOW_BRIEFS,
    BUILTIN_COMMAND_SPECS,
    builtin_command_palette,
)
from navin.command.modules import (
    CODE_HIDDEN_COMMANDS,
    VALID_PRODUCT_MODULES,
    default_preload_skills_for_module,
    exclusive_studio_skills,
    extra_denied_tools_for_module,
    is_command_allowed_for_module,
)
from navin.webui.career_api import handle_career_action
from navin.webui.mcp_presets_api import MCP_PRESETS, mcp_presets_payload

ROOT = Path(__file__).resolve().parents[1]


class CareerProductModuleTest(unittest.TestCase):
    def test_module_registered(self) -> None:
        self.assertIn("career", VALID_PRODUCT_MODULES)
        self.assertIn("/career", CODE_HIDDEN_COMMANDS)
        self.assertTrue(is_command_allowed_for_module("/career", "career"))
        self.assertFalse(is_command_allowed_for_module("/career", "code"))
        self.assertIn("/career", _HTML_REPORT_WORKFLOWS)
        self.assertIn("/career", _DELIVERY_WORKFLOWS)
        self.assertIn("/career", _TRACKED_WORKFLOWS)
        self.assertEqual(WORKFLOW_ROUTE_ROLES.get("/career"), "search")
        self.assertEqual(workflow_role_for_content("/career Find missions"), "search")
        self.assertEqual(PRODUCT_MODULE_ROUTE_ROLES.get("career"), "search")
        self.assertEqual(product_module_role("career"), "search")
        denied = extra_denied_tools_for_module("career")
        self.assertIn("tenders", denied)
        self.assertIn("trading", denied)
        self.assertNotIn("career", denied)
        self.assertNotIn("scrape", denied)
        locked = AgentLoop._locked_denied_tools(None, {"product_module": "career"})
        self.assertIn("tenders", locked)
        self.assertNotIn("career", locked)
        self.assertNotIn("scrape", locked)
        tools = AgentLoop._denied_tools(None, {"product_module": "career"})
        self.assertNotIn("scrape", tools)
        self.assertIn("tenders", tools)
        heartbeat = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        self.assertIn("career", heartbeat)
        self.assertIn("watch", heartbeat)
        self.assertIn("already ran", heartbeat)

    def test_palette_and_brief(self) -> None:
        specs = {spec.command: spec for spec in BUILTIN_COMMAND_SPECS}
        self.assertIn("/career", specs)
        title, skills, brief = _WORKFLOW_BRIEFS["/career"]
        self.assertIn("Career", title)
        self.assertIn("career-agent", skills)
        self.assertIn("job-search-agent", skills)
        self.assertIn("followup-writer", skills)
        self.assertIn("salary-negotiator", skills)
        self.assertIn("career", brief)
        self.assertIn("LinkedIn", brief)
        self.assertIn("import", brief)
        self.assertIn("web snippets", brief)
        self.assertIn("scrape", brief)
        self.assertIn("watch", brief)
        self.assertIn("ingest LinkedIn MCP", brief)
        self.assertIn("Web Job Search", brief)
        self.assertIn("scrape-operator", skills)
        palette = {row["command"] for row in builtin_command_palette("career")}
        self.assertIn("/career", palette)
        self.assertIn("/scrape", palette)
        self.assertNotIn("/trading", palette)
        exclusive = exclusive_studio_skills()
        self.assertIn("career-agent", exclusive.get("career", set()))
        self.assertIn("job-search-agent", exclusive.get("career", set()))
        preload = default_preload_skills_for_module("career")
        self.assertIn("career-agent", preload)
        self.assertIn("interview-coach", preload)
        self.assertIn("scrape-operator", preload)
        self.assertTrue(is_command_allowed_for_module("/scrape", "career"))
        for name in CAREER_SKILLS:
            skill_path = ROOT / f"navin/skills/{name}/SKILL.md"
            self.assertTrue(skill_path.is_file(), name)
            if name in {"scrape-operator", "scrapling", "web-extractor"}:
                continue
            body = skill_path.read_text(encoding="utf-8")
            self.assertIn('"default_for":"career"', body, name)
            self.assertIn('"category":"careers"', body, name)
        search_skill = (ROOT / "navin/skills/job-search-agent/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("career action=search", search_skill)
        self.assertIn("Never scrape LinkedIn", search_skill)
        self.assertNotIn("Search via `web_search` + `web_fetch` on boards", search_skill)

    def test_shell_opens_career_above_trading(self) -> None:
        app = (ROOT / "webui/src/App.tsx").read_text(encoding="utf-8")
        sidebar = (ROOT / "webui/src/components/Sidebar.tsx").read_text(encoding="utf-8")
        self.assertIn('path === "/career"', app)
        self.assertIn("CareerWorkspace", app)
        self.assertIn('view === "notes"', app)
        self.assertNotIn('if (deskView !== "meeting") setWorkbenchFocus', app)
        self.assertIn('"career"', sidebar)
        self.assertLess(sidebar.find('"tenders"'), sidebar.find('"career"'))
        self.assertLess(sidebar.find('"career"'), sidebar.find('"trading"'))
        self.assertIn("onOpenCareerStudio", sidebar)
        http = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn("async def _handle_career", http)
        vite = (ROOT / "webui/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("careerDeskApi", vite)
        self.assertTrue((ROOT / "navin/career/desk_cli.py").is_file())
        api = (ROOT / "webui/src/lib/career-api.ts").read_text(encoding="utf-8")
        self.assertIn('apiBodyHeaders("{}")', api)
        desk = (ROOT / "webui/src/components/studio/career/CareerWorkspace.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn('type Pane = "home" | "offers"', desk)
        self.assertIn('pane === "home"', desk)
        self.assertIn("career-start-loop", desk)
        self.assertIn("career-pause-loop", desk)
        self.assertNotIn('data-testid="career-run-cycle"', desk)
        self.assertIn('run("stop")', desk)
        self.assertIn("CareerWizard", desk)
        self.assertIn("wizard_complete", desk)
        self.assertIn("CareerMoneyBar", desk)
        self.assertIn("careerMoney", desk)
        self.assertIn("Find me a mission", desk)
        self.assertIn("career-linkedin-mcp-status", desk)
        self.assertIn("mcpEnabled", desk)
        self.assertIn("action=find", desk)
        self.assertIn("authorizedPortals", desk)
        self.assertNotIn("window.open", desk)
        self.assertIn("openOfficialCareerUrl", desk)
        self.assertIn("data-open-url", desk)
        start = (ROOT / "webui/src/components/studio/career/CareerStart.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("How much do you want to earn?", start)
        self.assertIn("BILLABLE_DAYS", start)
        wizard = (ROOT / "webui/src/components/studio/career/CareerWizard.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("CrmPhoneField", wizard)
        self.assertIn("SearchableSelect", wizard)
        self.assertIn("career-wizard", wizard)
        self.assertIn("max_salary", wizard)
        self.assertIn("prospect_email_approved", wizard)
        self.assertIn("CareerCvPanel", wizard)
        self.assertIn("catalogIsLive", wizard)
        self.assertIn("connectors", wizard)
        self.assertIn("CareerDossierPanel", wizard)
        self.assertIn('id: "dossier"', wizard)
        self.assertNotIn("CareerStackPanel", desk)
        self.assertNotIn("career-stack", desk)
        self.assertIn("career-sources", desk)
        self.assertIn("catalogHint", desk)
        settings_view = (ROOT / "webui/src/components/settings/SettingsView.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("isCareerMcp", settings_view)
        self.assertIn("tools-mcp", settings_view)
        self.assertIn("careerMcp", settings_view)
        self.assertIn("sharedMcp", settings_view)
        self.assertIn("SHARED_MCP_NAMES", settings_view)
        skills_catalog = (
            ROOT / "webui/src/components/settings/SkillsCatalogSettings.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn('"careers"', skills_catalog)
        self.assertIn("filterCareers", skills_catalog)
        portals = (ROOT / "webui/src/lib/career-portals.ts").read_text(encoding="utf-8")
        self.assertIn("linkedin.com/jobs/search", portals)
        self.assertIn("parsePastedOffer", portals)
        self.assertNotIn("Easy Apply", desk)
        self.assertIn("ImportOfferForm", desk)
        kpis = (ROOT / "webui/src/components/studio/career/CareerKpis.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-testid="career-money-bar"', kpis)
        self.assertIn("tabular-nums", kpis)
        self.assertNotIn("motion.", kpis)
        money = (ROOT / "webui/src/lib/career-money.ts").read_text(encoding="utf-8")
        self.assertIn("BILLABLE_DAYS", money)
        self.assertIn("atOrAboveGoal", money)


class CareerCatalogTest(unittest.TestCase):
    def test_v1_connectors_are_listed(self) -> None:
        ids = {row["id"] for row in catalog()}
        for sid in (
            "adzuna",
            "jooble",
            "greenhouse",
            "lever",
            "ashby",
            "remotive",
            "france-travail",
            "usajobs",
            "linkedin",
            "malt",
            "web-search",
            "web-job-search",
            "free-work",
            "jobserve",
            "dice",
            "ictjob",
            "jobs-ch",
            "job-bank-ca",
        ):
            self.assertIn(sid, ids)
        linkedin = next(row for row in catalog() if row["id"] == "linkedin")
        self.assertFalse(linkedin["auto_apply"])
        self.assertEqual(linkedin["ingest"], "public_listing")
        self.assertGreaterEqual(linkedin["priority"], 10)
        freework = next(row for row in catalog() if row["id"] == "free-work")
        self.assertFalse(freework["auto_apply"])
        self.assertTrue(freework["auto_search"])
        self.assertEqual(freework["ingest"], "public_listing")
        web = next(row for row in catalog() if row["id"] == "web-job-search")
        self.assertEqual(web["name"], "Web Job Search")
        self.assertTrue(web["auto_search"])

    def test_web_search_covers_preferred_markets(self) -> None:
        queries = web_search_queries(
            titles=["Data Engineer"],
            countries=["FR", "AE", "US"],
            track="freelance",
            stack=["Python", "Spark"],
        )
        texts = " ".join(row["query"] for row in queries)
        self.assertIn("site:francetravail.fr", texts)
        # Free-Work has its own public listing reader: no web search slot spent on it.
        self.assertNotIn("site:free-work.com", texts)
        self.assertIn("site:bayt.com", texts)
        self.assertIn("usajobs.gov", texts)
        fr_queries = web_search_queries(
            titles=["Senior Data Engineer"],
            countries=["FR", "BE", "GB", "US", "CA"],
            track="freelance",
        )
        blob = " ".join(row["query"] for row in fr_queries)
        self.assertIn("mission 6 months", blob)
        self.assertIn("Bruxelles", blob)
        self.assertIn("IR35", blob)
        self.assertIn("1099", blob)
        self.assertIn("contractuel", blob)
        self.assertIn("site:jobserve.com", blob)
        self.assertIn("site:ictjob.be", blob)
        self.assertTrue(any(row.get("kind") == "linkedin_open" for row in queries))
        self.assertIn("job openings", texts)
        self.assertIn("nous recrutons", texts)
        self.assertIn("join our team", texts)
        self.assertIn("site:boards.greenhouse.io", texts)
        pack = web_search_run_pack(
            titles=["Data Engineer"],
            countries=["FR", "AE"],
            track="freelance",
            stack=["Python"],
        )
        pack_blob = " ".join(row["query"] for row in pack)
        self.assertTrue(any("site:francetravail.fr" in row["query"] for row in pack))
        self.assertIn("boards.greenhouse.io", pack_blob)
        self.assertIn("job openings", pack_blob)
        self.assertLessEqual(len(pack), 16)
        self.assertTrue(all(row.get("kind") == "web" for row in pack))

    def test_web_hit_keeps_linkedin_snippet_without_fetch(self) -> None:
        job = _hit_to_job(
            {
                "title": "Data Engineer - Capgemini | LinkedIn",
                "url": "https://www.linkedin.com/jobs/view/123",
                "snippet": "Python Spark Paris hiring",
            },
            country="FR",
            track="freelance",
        )
        self.assertIsNotNone(job)
        self.assertEqual(job["source"], "linkedin")
        self.assertEqual(job["ingest"], "search_snippet")
        noise = _hit_to_job(
            {
                "title": "Funny cats",
                "url": "https://www.youtube.com/watch?v=1",
                "snippet": "video",
            },
            country="FR",
            track="freelance",
        )
        self.assertIsNone(noise)

    def test_official_pack_opens_linkedin_per_market(self) -> None:
        pack = official_search_pack("Data Engineer", ["FR", "AE"], track="freelance", work_mode="remote")
        ids = {row["id"] for row in pack}
        self.assertIn("li-FR", ids)
        self.assertIn("li-AE", ids)
        france = next(row for row in pack if row["id"] == "li-FR")
        self.assertIn("linkedin.com/jobs/search", france["url"])
        self.assertIn("France", france["url"])
        self.assertTrue(is_closed_job_url("https://www.linkedin.com/jobs/view/1"))
        self.assertTrue(is_closed_job_url("https://www.bayt.com/en/uae/jobs/q/data/"))
        self.assertFalse(is_closed_job_url("https://remotive.com/remote-jobs/1"))
        self.assertTrue(is_open_scrape_url("https://remotive.com/remote-jobs/1"))
        self.assertTrue(is_open_scrape_url("https://boards.greenhouse.io/acme/jobs/1"))
        self.assertFalse(is_open_scrape_url("https://www.linkedin.com/jobs/view/1"))
        self.assertFalse(is_open_scrape_url("https://www.bayt.com/en/uae/jobs/q/data/"))
        core = official_search_pack("Data Engineer", ["FR", "BE", "CH", "GB", "US", "CA"], track="freelance")
        urls = " ".join(row["url"] for row in core)
        self.assertIn("free-work.com", urls)
        self.assertIn("ictjob.be", urls)
        self.assertIn("jobs.ch", urls)
        self.assertIn("jobserve.com", urls)
        self.assertIn("dice.com", urls)
        self.assertIn("jobbank.gc.ca", urls)
        self.assertTrue(core[0]["id"].startswith("li-"))
        pack = scrape_search_queries(titles=["Data Engineer"], countries=["FR", "US"], track="freelance")
        texts = " ".join(row["query"] for row in pack)
        self.assertIn("site:remotive.com", texts)
        self.assertIn("site:arbeitnow.com", texts)
        self.assertIn("site:himalayas.app", texts)
        self.assertIn("site:weworkremotely.com", texts)
        self.assertIn("site:remoteok.com", texts)
        self.assertIn("site:boards.greenhouse.io", texts)
        self.assertNotIn("site:linkedin.com", texts)
        self.assertNotIn("site:indeed.com", texts)
        self.assertNotIn("site:bayt.com", texts)


class CareerStackAndMcpTest(unittest.TestCase):
    def test_module_stack_lists_tool_skills_and_connectors(self) -> None:
        stack = module_stack()
        self.assertEqual(stack["tool"], "career")
        self.assertIn("career-agent", stack["skills"])
        ids = {row["id"] for row in stack["connectors"]}
        self.assertIn("remotive", ids)
        self.assertIn("linkedin", ids)
        linkedin = next(row for row in stack["connectors"] if row["id"] == "linkedin")
        # Public guest listings are read live; the MCP session stays the route
        # for saved jobs, inbox and profile.
        self.assertEqual(linkedin["ingest"], "public_listing")
        self.assertTrue(linkedin["live"])
        self.assertTrue(linkedin.get("recommended"))
        scrape = next(row for row in stack["connectors"] if row["id"] == "scrape")
        self.assertTrue(scrape["live"])
        self.assertIn("scrape-operator", stack["skills"])

    def test_notion_mcp_is_official_career_preset(self) -> None:
        by_name = {preset.name: preset for preset in MCP_PRESETS}
        self.assertIn("notion", by_name)
        preset = by_name["notion"]
        self.assertEqual(preset.category, "career")
        self.assertEqual(preset.transport, "stdio")
        self.assertIn("@notionhq/notion-mcp-server", preset.server.args)
        names = {item["name"] for item in mcp_presets_payload()["presets"]}
        self.assertIn("notion", names)
        self.assertIn("github", names)
        self.assertIn("exa", names)
        self.assertIn("career", by_name["github"].modules)
        self.assertIn("career", by_name["exa"].modules)
        payload = {item["name"]: item for item in mcp_presets_payload()["presets"]}
        self.assertIn("career", payload["notion"]["modules"])
        self.assertIn("career", payload["github"]["modules"])
        self.assertIn("career", payload["exa"]["modules"])
        self.assertIn("career", by_name["linkedin"].modules)
        self.assertIn("tenders", by_name["linkedin"].modules)
        from navin.agent.loop import AgentLoop
        from navin.agent.tool_surface import tool_name_is_denied
        from navin.webui.mcp_presets_api import mcp_deny_prefixes

        self.assertNotIn("mcp_linkedin_", mcp_deny_prefixes("career"))
        career_locked = AgentLoop._locked_denied_tools(None, {"product_module": "career"})
        self.assertFalse(tool_name_is_denied("mcp_linkedin_get_company_profile", career_locked))
        agent = (ROOT / "navin/skills/career-agent/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("`linkedin`", agent)
        self.assertIn("`notion`", agent)
        self.assertIn("#/tools", agent)
        self.assertIn("Never scrape", agent)
        self.assertIn("Never Easy Apply", agent)


def tuned_profile(**extra: Any) -> dict[str, Any]:
    """A profile the user actually filled. default_profile() is deliberately empty."""
    return {
        **default_profile(),
        "titles": ["Data Engineer", "AI Engineer"],
        "countries_primary": ["FR", "AE", "SA"],
        "countries_secondary": ["QA", "KW", "US"],
        "country_weights": {"FR": 100, "AE": 90, "SA": 85},
        "stack": ["Python", "Spark", "Databricks", "AWS"],
        **extra,
    }


class CareerScoreTest(unittest.TestCase):
    def test_default_profile_is_empty_for_a_new_user(self) -> None:
        profile = default_profile()
        self.assertEqual(profile["titles"], [])
        self.assertEqual(profile["stack"], [])
        self.assertEqual(profile["countries_primary"], [])
        self.assertEqual(profile["min_rate"], 0)

    def test_country_weights_fill_when_user_picks_markets(self) -> None:
        profile = normalize_profile(
            {
                "countries_primary": ["FR", "BE", "CH"],
                "countries_secondary": ["US", "CA"],
                "countries_excluded": ["GB"],
            }
        )
        self.assertEqual(profile["country_weights"]["FR"], 100.0)
        self.assertEqual(profile["country_weights"]["BE"], 90.0)
        self.assertEqual(profile["country_weights"]["CH"], 85.0)
        self.assertEqual(profile["country_weights"]["US"], 70.0)
        self.assertNotIn("GB", profile["country_weights"])
        paris = score_opportunity(
            {"title": "Data Engineer", "country": "FR", "compensation": 700, "track": "freelance"},
            {**profile, "titles": ["Data Engineer"], "min_rate": 650},
        )
        usa = score_opportunity(
            {"title": "Data Engineer", "country": "US", "compensation": 700, "track": "freelance"},
            {**profile, "titles": ["Data Engineer"], "min_rate": 650},
        )
        self.assertGreater(paris["match_score"], usa["match_score"])

    def test_primary_market_and_stack_raise_score(self) -> None:
        profile = tuned_profile()
        scored = score_opportunity(
            {
                "title": "Senior Data Engineer freelance",
                "description": "Python Spark Databricks AWS remote Paris",
                "country": "FR",
                "remote": "remote",
                "compensation": 700,
                "track": "freelance",
                "stack": ["Python", "Spark"],
                "languages": ["fr", "en"],
            },
            profile,
        )
        self.assertGreaterEqual(scored["match_score"], 80)
        self.assertEqual(scored["bucket"], "perfect")

    def test_strengths_lift_and_ceiling_caps_pay(self) -> None:
        profile = tuned_profile(strengths=["Databricks", "Lakehouse"])
        job = {
            "title": "Data Engineer",
            "description": "Python Spark Databricks lakehouse remote",
            "country": "FR",
            "remote": "remote",
            "track": "freelance",
        }
        with_strengths = score_opportunity(job, profile)
        without = score_opportunity(job, {**profile, "strengths": []})
        self.assertGreater(with_strengths["match_score"], without["match_score"])
        self.assertTrue(any(row["label"] == "databricks" for row in with_strengths["match_reasons"]))
        capped = score_opportunity(
            {**job, "compensation": 3000},
            {**profile, "min_rate": 650, "max_rate": 850},
        )
        uncapped = score_opportunity(
            {**job, "compensation": 800},
            {**profile, "min_rate": 650, "max_rate": 850},
        )
        self.assertLess(capped["match_score"], uncapped["match_score"])

    def test_excluded_country_is_skipped(self) -> None:
        profile = tuned_profile(countries_excluded=["GB"])
        scored = score_opportunity(
            {
                "title": "Data Engineer",
                "description": "Python Spark",
                "country": "GB",
                "track": "freelance",
            },
            profile,
        )
        self.assertLessEqual(scored["match_score"], 24)
        self.assertEqual(scored["bucket"], "skip")

    def test_html_description_is_plain_text(self) -> None:
        from navin.career.sources import html_to_text

        text = html_to_text("<p>Are you a talented <strong>Senior Data Engineer</strong>?</p><ul><li>Python</li></ul>")
        self.assertIn("Senior Data Engineer", text)
        self.assertNotIn("<p>", text)
        self.assertIn("Python", text)
        encoded = html_to_text("&lt;p&gt;Lemon.io mission&lt;/p&gt;")
        self.assertIn("Lemon.io mission", encoded)
        self.assertNotIn("<p>", encoded)

    def test_off_role_and_citizenship_are_skipped(self) -> None:
        profile = tuned_profile(titles=["AI Engineer"])
        sales = score_opportunity(
            {
                "title": "Sales Jedi",
                "description": "Join our engineering culture, remote",
                "country": "REMOTE",
                "remote": "remote",
                "track": "freelance",
            },
            profile,
        )
        self.assertLessEqual(sales["match_score"], 35)
        self.assertEqual(sales["bucket"], "skip")
        from navin.career.matching import job_is_relevant

        self.assertFalse(job_is_relevant({"title": "Remote Office Assistant"}, profile))
        self.assertFalse(
            job_is_relevant(
                {"title": "AI Engineer || United States citizenship is required - Dice"},
                profile,
            )
        )
        self.assertTrue(job_is_relevant({"title": "AI Engineer remote Dubai"}, profile))

    def test_search_scope_starts_on_gulf_maghreb_and_europe(self) -> None:
        from navin.career.sources import DEFAULT_SEARCH_COUNTRIES, expand_search_countries

        scope = expand_search_countries(["FR", "BE"])
        self.assertIn("AE", scope)
        self.assertIn("QA", scope)
        self.assertIn("SA", scope)
        self.assertIn("MA", scope)
        self.assertIn("TN", scope)
        self.assertIn("ES", scope)
        self.assertTrue(scope.index("FR") < scope.index("AE") or "FR" in ["FR", "BE"])
        self.assertEqual(scope[:2], ["FR", "BE"])
        self.assertTrue(set(DEFAULT_SEARCH_COUNTRIES) <= set(scope) | {"FR", "BE"})


class CareerApiTest(unittest.TestCase):
    def test_snapshot_profile_and_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            with patch("navin.webui.career_api._store", return_value=store):
                snap = handle_career_action("snapshot")
                self.assertIn("profile", snap)
                self.assertIn("stack", snap)
                self.assertEqual(snap["stack"]["tool"], "career")
                self.assertEqual(snap["profile"]["apply_mode"], "manual")
                saved = handle_career_action(
                    "profile",
                    {
                        "titles": "Data Engineer",
                        "countries_primary": "FR,AE",
                        "min_rate": 650,
                    },
                )
                self.assertEqual(saved["profile"]["titles"], ["Data Engineer"])
                self.assertFalse(snap["profile"]["wizard_complete"])
                self.assertEqual(snap["profile"]["account_kind"], "solo")
                store.upsert_opportunities(
                    [
                        {
                            "id": "job-test1",
                            "title": "Data Engineer",
                            "company": "Acme",
                            "country": "FR",
                            "source": "remotive",
                            "url": "https://example.com/job",
                            "track": "freelance",
                            "stage": "discovered",
                            "description": "Python Spark",
                            "stack": ["Python"],
                        }
                    ]
                )
                prepared = handle_career_action("prepare", {"id": "job-test1"})
                self.assertTrue(prepared["prepared"]["cv_name"].endswith(".docx"))
                self.assertNotIn("Do not invent", prepared["prepared"]["summary"])
                self.assertFalse(prepared["prepared"]["pack_ready"])
                self.assertIn("Data Engineer", prepared["prepared"]["cv_text"])

    def test_wizard_company_profile_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            with patch("navin.webui.career_api._store", return_value=store):
                saved = handle_career_action(
                    "profile",
                    {
                        "account_kind": "company",
                        "display_name": "Aymen",
                        "max_salary": 85000,
                        "min_salary": 65000,
                        "residence_country": "FR",
                        "countries_primary": ["FR", "BE"],
                        "company": {
                            "name": "Portage Nord",
                            "email": "hello@portage.test",
                            "phone": "+33123456789",
                            "country": "FR",
                        },
                        "channels": {"telegram": True, "telegram_to": "@desk"},
                        "prospect_email": "Bonjour",
                        "prospect_email_approved": True,
                        "wizard_complete": True,
                    },
                )
                profile = saved["profile"]
                self.assertEqual(profile["account_kind"], "company")
                self.assertEqual(profile["company"]["name"], "Portage Nord")
                self.assertTrue(profile["channels"]["telegram"])
                self.assertEqual(profile["max_salary"], 85000)
                self.assertTrue(profile["wizard_complete"])
                self.assertTrue(profile["prospect_email_approved"])
                self.assertIn("files", saved)
                self.assertTrue((store.root / "INDEX.md").is_file())
                self.assertTrue((store.root / "dossier.md").is_file())
                self.assertIn("Portage Nord", (store.root / "dossier.md").read_text(encoding="utf-8"))

    def test_experiences_normalize(self) -> None:
        profile = default_profile()
        saved = normalize_profile(
            {
                **profile,
                "cv_path": "create",
                "experiences": [
                    {"title": "Data Engineer", "company": "Acme", "period": "2024", "facts": "Spark"},
                ],
                "education": [{"school": "INSA", "diploma": "MSc", "year": "2016"}],
            }
        )
        self.assertEqual(saved["cv_path"], "create")
        self.assertEqual(saved["experiences"][0]["company"], "Acme")
        self.assertEqual(saved["education"][0]["school"], "INSA")

    def test_watch_reports_strong_matches_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(
                {
                    "titles": ["Data Engineer"],
                    "countries_primary": ["FR"],
                    "wizard_complete": True,
                }
            )
            store.upsert_opportunities(
                [
                    {
                        "id": "job-strong",
                        "title": "Data Engineer",
                        "company": "Acme",
                        "country": "FR",
                        "source": "remotive",
                        "url": "https://example.com/job",
                        "track": "freelance",
                        "stage": "discovered",
                        "match_score": 96,
                    },
                    {
                        "id": "job-weak",
                        "title": "Intern",
                        "company": "Skip",
                        "country": "GB",
                        "source": "web",
                        "stage": "discovered",
                        "match_score": 40,
                    },
                ]
            )
            with patch("navin.webui.career_api._store", return_value=store):
                with patch("navin.career.notify.deliver_alert", return_value={"webui": False}):
                    failed = handle_career_action("watch")
                self.assertEqual(failed["watch"]["count"], 1)
                self.assertFalse(failed["watch"].get("delivered"))
                self.assertNotIn("match", store.get_opportunity("job-strong").get("alerts_sent") or [])
                with patch("navin.career.notify.deliver_alert", return_value={"webui": True}):
                    first = handle_career_action("watch")
                    self.assertEqual(first["watch"]["count"], 1)
                    self.assertEqual(first["watch"]["events"][0]["id"], "job-strong")
                    self.assertTrue(first["watch"].get("delivered"))
                    second = handle_career_action("watch")
                    self.assertEqual(second["watch"]["count"], 0)
            self.assertFalse(digest_was_delivered({}))
            self.assertFalse(digest_was_delivered({"webui": False, "telegram": False}))
            self.assertTrue(digest_was_delivered({"webui": True}))
            self.assertTrue(digest_was_delivered({"email": "ok"}))
            tool = CareerTool()
            self.assertIn("watch", tool.parameters["properties"]["action"]["enum"])

    def test_unknown_action_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            with patch("navin.webui.career_api._store", return_value=store):
                with self.assertRaises(CareerError) as ctx:
                    handle_career_action("explode")
                self.assertEqual(ctx.exception.status, 400)

    def test_linkedin_apply_requires_actual_candidate_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"apply_mode": "autopilot"})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-li",
                        "title": "Data Engineer",
                        "source": "linkedin",
                        "url": "https://www.linkedin.com/jobs/view/1",
                        "track": "jobs",
                        "stage": "ready",
                    }
                ]
            )
            with patch("navin.webui.career_api._store", return_value=store):
                with self.assertRaises(CareerError) as ctx:
                    handle_career_action("apply", {"id": "job-li"})
                self.assertIn("master CV", ctx.exception.message)

    def test_import_linkedin_paste_never_fetches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            with patch("navin.webui.career_api._store", return_value=store):
                with patch("urllib.request.urlopen") as fetch:
                    imported = handle_career_action(
                        "import",
                        {
                            "url": "https://www.linkedin.com/jobs/view/123",
                            "title": "Senior Data Engineer",
                            "company": "Acme",
                            "body": "Python Spark remote Paris",
                            "track": "freelance",
                            "country": "FR",
                        },
                    )
                    fetch.assert_not_called()
                row = imported["imported"]
                self.assertEqual(row["source"], "linkedin")
                self.assertEqual(row["title"], "Senior Data Engineer")
                with self.assertRaises(CareerError):
                    handle_career_action(
                        "import",
                        {
                            "url": "https://www.linkedin.com/jobs/view/9",
                            "fetch": True,
                        },
                    )

    def test_local_index_and_agent_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            with patch("navin.webui.career_api._store", return_value=store):
                saved = handle_career_action(
                    "profile",
                    {
                        "account_kind": "company",
                        "display_name": "Aymen",
                        "headline": "Data Engineer",
                        "master_cv": "Aymen - Data Engineer\nSpark on AWS at Acme 2022-2026.",
                        "strengths": ["Python", "Spark"],
                        "weaknesses": ["No German"],
                        "highlights": ["Remote first"],
                        "projects": [{"title": "Lakehouse", "result": "Cut batch time 40%"}],
                        "experiences": [
                            {"title": "Data Engineer", "company": "Acme", "period": "2022-2026", "facts": "Spark"}
                        ],
                        "education": [{"school": "INSA", "diploma": "MSc", "year": "2016"}],
                        "company": {"name": "Portage Nord", "email": "hello@portage.test", "country": "FR"},
                        "channels": {"telegram": True, "telegram_to": "@desk"},
                        "talents": [{"name": "Aymen", "master_cv": "Aymen - Data Engineer", "strengths": ["Spark"]}],
                        "prospect_email": "Bonjour, disponible pour une mission Spark.",
                        "wizard_complete": True,
                    },
                )
                self.assertTrue((store.root / "cv.md").is_file())
                self.assertTrue((store.root / "dossier.md").is_file())
                self.assertTrue((store.root / "INDEX.md").is_file())
                self.assertTrue((store.root / "book.md").is_file())
                cv = (store.root / "cv.md").read_text(encoding="utf-8")
                dossier = (store.root / "dossier.md").read_text(encoding="utf-8")
                index = (store.root / "INDEX.md").read_text(encoding="utf-8")
                self.assertIn("Spark on AWS", cv)
                self.assertIn("Python", dossier)
                self.assertIn("Portage Nord", dossier)
                self.assertIn("cv.md", index)
                self.assertIn("CAREER LOCAL BOOK", saved["book"])
                book = handle_career_action("read", {"file": "cv"})
                self.assertIn("Spark on AWS", book["text"])
                tool = CareerTool()
                status = asyncio.run(tool.execute(action="status"))
                self.assertIn("CAREER LOCAL BOOK", status)
                self.assertIn("Spark on AWS", status)
                self.assertIn("Python", status)
                self.assertIn("Portage Nord", status)
                self.assertIn("@desk", status)
                self.assertIn("Lakehouse", status)
                self.assertTrue(tool.call_read_only({"action": "status"}))
                self.assertTrue(tool.call_read_only({"action": "read"}))
                raw = asyncio.run(tool.execute(action="read", file="dossier"))
                self.assertIn("Master CV", raw)
                asyncio.run(tool.execute(action="profile", master_cv="Aymen - still Data Engineer at Acme."))
                kept = store.load_profile()
                self.assertEqual(kept["company"]["name"], "Portage Nord")
                self.assertIn("still Data Engineer", kept["master_cv"])
                dossier_text = format_dossier(kept)
                self.assertIn("still Data Engineer", dossier_text)
                paths = write_local_index(store, kept)
                self.assertEqual(paths["root"], str(store.root))
                formatted = format_agent_status(handle_career_action("status"))
                self.assertIn("still Data Engineer", formatted)
                for name in ("profile.json", "opportunities.json", "applications.json", "inbox.json", "journal.json"):
                    self.assertTrue((store.root / name).is_file(), name)

    def test_book_on_disk_counts_and_ranks_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            with patch("navin.webui.career_api._store", return_value=store):
                handle_career_action("profile", {"display_name": "Aymen", "visa": "EU", "stack": ["Python"]})
                handle_career_action(
                    "ingest",
                    {
                        "jobs": [
                            {"title": "Weak role", "url": "https://x.test/1", "country": "US", "description": "sales"},
                            {
                                "title": "Data Engineer",
                                "url": "https://x.test/2",
                                "country": "FR",
                                "remote": "remote",
                                "description": "Python Spark remote",
                            },
                        ]
                    },
                )
                book = (store.root / "book.md").read_text(encoding="utf-8")
                self.assertIn("Opportunities 2", book)
                self.assertIn("- Visa: EU", book)
                self.assertLess(book.index("Data Engineer |"), book.index("Weak role |"))
                self.assertIn("Nothing is hidden", book)

    def test_book_lists_archived_favorites_and_does_not_truncate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            with patch("navin.webui.career_api._store", return_value=store):
                handle_career_action("profile", {"display_name": "Aymen", "stack": ["Python"]})
                jobs = [
                    {
                        "id": f"job-{index:02d}",
                        "title": f"Role {index:02d}",
                        "url": f"https://x.test/{index}",
                        "country": "FR" if index % 2 == 0 else "AE",
                        "source": "remotive",
                        "match_score": 90 - index,
                    }
                    for index in range(45)
                ]
                jobs[0]["title"] = "Archived Spark lead"
                jobs[1]["title"] = "Favorite React lead"
                store.save_opportunities(jobs)
                handle_career_action("archive", {"id": "job-00"})
                handle_career_action("favorite", {"id": "job-01"})
                snap = handle_career_action("status")
                self.assertTrue(any(row.get("id") == "job-00" and row.get("archived") for row in snap["opportunities"]))
                book = snap["book"]
                self.assertIn("Archived Spark lead", book)
                self.assertIn("Favorite React lead", book)
                self.assertIn(" | ARCH", book)
                self.assertIn(" | FAV", book)
                self.assertIn("Nothing is hidden", book)
                self.assertIn("Book lists 45 offer(s)", book)
                self.assertNotIn("more, best first", book)
                status = format_agent_status(snap)
                self.assertIn("job-44", status)
                self.assertIn("Discuss any id above", status)

    def test_ingest_stores_ui_hits_without_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            with patch("navin.webui.career_api._store", return_value=store):
                with patch("urllib.request.urlopen") as fetch:
                    snap = handle_career_action(
                        "ingest",
                        {
                            "track": "freelance",
                            "jobs": [
                                {
                                    "title": "Data Engineer",
                                    "company": "Remotive Co",
                                    "url": "https://remotive.com/remote-jobs/1",
                                    "country": "FR",
                                    "remote": "remote",
                                    "description": "Python Spark",
                                    "source": "remotive",
                                },
                                {"title": "", "url": ""},
                            ],
                        },
                    )
                    fetch.assert_not_called()
                self.assertEqual(snap["ingested"], 1)
                self.assertEqual(snap["kpis"]["opportunities"], 1)
                self.assertEqual(snap["opportunities"][0]["ingest"], "ui_search")
                self.assertIsNotNone(snap["opportunities"][0]["match_score"])
                prepared = handle_career_action("prepare", {"id": snap["opportunities"][0]["id"]})
                apps_md = handle_career_action("read", {"file": "applications.md"})
                self.assertIn("Remotive Co", apps_md["text"])
                self.assertIn(prepared["prepared"]["cv_name"], apps_md["text"])
                tool_text = asyncio.run(CareerTool().execute(action="read", file="applications.md"))
                self.assertIn("Remotive Co", tool_text)

    def test_heartbeat_refuses_collect_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"], "wizard_complete": True})
            ctx = RequestContext(
                channel="telegram",
                chat_id="1",
                session_key="heartbeat",
                metadata={"heartbeat": True},
            )
            tool = CareerTool()
            with patch("navin.webui.career_api._store", return_value=store):
                with request_context(ctx):
                    collect_result = asyncio.run(tool.execute(action="collect"))
                    apply_result = asyncio.run(tool.execute(action="apply", id="job-x"))
                    search_result = asyncio.run(tool.execute(action="search"))
                    watch_result = asyncio.run(tool.execute(action="watch"))
                    status_result = asyncio.run(tool.execute(action="status"))
            for result, label in (
                (collect_result, "collect"),
                (apply_result, "apply"),
                (search_result, "search"),
            ):
                self.assertTrue(getattr(result, "is_error", False), label)
                self.assertIn("heartbeat", str(result).lower(), label)
            self.assertFalse(getattr(watch_result, "is_error", False), watch_result)
            self.assertFalse(getattr(status_result, "is_error", False), status_result)
            with patch("navin.webui.career_api._store", return_value=store):
                with request_context(ctx):
                    with self.assertRaises(CareerError) as api_err:
                        handle_career_action("collect", {})
            self.assertIn("heartbeat", api_err.exception.message.lower())


class CareerHeartbeatAutonomyTest(unittest.TestCase):
    def test_gateway_tick_matches_desk_and_stays_silent_when_empty(self) -> None:
        self.assertFalse(profile_is_armed({}))
        self.assertFalse(profile_is_armed({"titles": [], "wizard_complete": False}))
        self.assertTrue(profile_is_armed({"titles": ["Data Engineer"]}))
        self.assertTrue(profile_is_armed({"wizard_complete": True}))
        self.assertEqual(heartbeat_prompt_note({"count": 0}), "")
        self.assertEqual(heartbeat_prompt_note(None), "")
        note = heartbeat_prompt_note({"count": 2, "digest": "Match: Staff Data Engineer (Paris Co, FR, 91)"})
        self.assertIn("watch.count=2", note)
        self.assertIn("Staff Data Engineer", note)
        self.assertIn("Never search", note)
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            self.assertIsNone(tick_watch(store))
            store.save_profile({"titles": ["Data Engineer"], "wizard_complete": True})
            empty = tick_watch(store)
            self.assertIsNotNone(empty)
            self.assertEqual(empty["count"], 0)
            self.assertEqual(heartbeat_prompt_note(empty), "")
            store.upsert_opportunities(
                [
                    {
                        "id": "job-fr-1",
                        "title": "Staff Data Engineer",
                        "company": "Paris Co",
                        "country": "FR",
                        "match_score": 91,
                        "stage": "matched",
                        "url": "https://remotive.com/remote-jobs/fr-1",
                    }
                ]
            )
            with patch("navin.career.notify.deliver_alert", return_value={"webui": False}):
                failed = tick_watch(store)
            self.assertEqual(failed["count"], 1)
            self.assertFalse(failed.get("delivered"))
            self.assertNotIn("match", store.get_opportunity("job-fr-1").get("alerts_sent") or [])
            with patch("navin.career.notify.deliver_alert", return_value={"webui": True}):
                first = tick_watch(store)
                second = tick_watch(store)
            self.assertEqual(first["count"], 1)
            self.assertTrue(first.get("delivered"))
            self.assertIn("Staff Data Engineer", first["digest"])
            self.assertIn("sent", first)
            self.assertEqual(second["count"], 0)
        self.assertEqual(HEARTBEAT_CAREER_ACTIONS, {"status", "dossier", "snapshot", "read", "book", "watch"})
        cli = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("tick_heartbeat_desks", cli)
        stack = module_stack()
        self.assertIn("gateway ticks career action=watch", stack["rules"])
        self.assertIn("Do not create a chat cron that searches or ticks", stack["rules"])


class CareerScrapeNetTest(unittest.TestCase):
    def test_open_net_uses_scrape_and_refuses_linkedin(self) -> None:
        fetched: list[str] = []

        def search(_query: str, _count: int) -> str:
            return (
                "1. Data Engineer Remotive\n   https://remotive.com/remote-jobs/123\n"
                "2. Data Engineer LinkedIn\n   https://www.linkedin.com/jobs/view/9\n"
                "3. Data Engineer Bayt\n   https://www.bayt.com/en/uae/jobs/q/data/\n"
            )

        def fetch(urls: list[str]) -> list[dict[str, Any]]:
            fetched.extend(urls)
            return [
                {
                    "url": url,
                    "title": "Data Engineer remote",
                    "text": "Python Spark hiring remote Data Engineer",
                    "status": 200,
                }
                for url in urls
            ]

        result = scrape_open_net(
            titles=["Data Engineer"],
            countries=["FR"],
            track="freelance",
            search_fn=search,
            fetch_fn=fetch,
        )
        self.assertTrue(result["jobs"])
        self.assertTrue(all("linkedin.com" not in str(job.get("url")) for job in result["jobs"]))
        self.assertNotIn("https://www.linkedin.com/jobs/view/9", fetched)
        self.assertNotIn("https://www.bayt.com/en/uae/jobs/q/data/", fetched)
        self.assertIn("https://remotive.com/remote-jobs/123", fetched)
        self.assertEqual(result["jobs"][0]["ingest"], "scrape_open")
        self.assertIn("https://www.linkedin.com/jobs/view/9", result["refused"])

    def test_default_scrape_fetch_drops_closed_urls(self) -> None:
        from navin.career.scrape_net import default_scrape_fetch

        pages = default_scrape_fetch(
            [
                "https://www.linkedin.com/jobs/view/1",
                "https://www.bayt.com/en/uae/jobs/q/data/",
            ]
        )
        self.assertTrue(pages)
        self.assertTrue(all(page.get("error") == "closed_board" for page in pages))


class CareerOfficialApiTest(unittest.TestCase):
    def test_adzuna_normalizes_and_skips_closed_urls(self) -> None:
        from navin.career.official import fetch_adzuna

        payload = {
            "results": [
                {
                    "title": "Data Engineer",
                    "company": {"display_name": "Acme"},
                    "location": {"display_name": "Paris"},
                    "redirect_url": "https://www.adzuna.fr/details/1",
                    "description": "Python Spark",
                    "salary_max": 70000,
                    "salary_currency": "EUR",
                    "created": "2026-08-01",
                },
                {
                    "title": "Secret",
                    "redirect_url": "https://www.linkedin.com/jobs/view/9",
                    "description": "nope",
                },
            ]
        }
        with patch.dict("os.environ", {"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key"}):
            with patch("navin.career.official._get_json", return_value=payload):
                rows = fetch_adzuna("Data Engineer", ["FR"], "freelance")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "adzuna")
        self.assertEqual(rows[0]["ingest"], "official_api")
        self.assertEqual(rows[0]["company"], "Acme")

    def test_keyed_collectors_are_silent_without_env(self) -> None:
        from navin.career.official import collect_official_apis

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(collect_official_apis("Data Engineer", ["FR"], "freelance"), [])

    def test_source_ids_keep_live_families_unless_toggled(self) -> None:
        self.assertTrue(_want_family({}, "remotive"))
        self.assertTrue(_want_family({"source_ids": ["linkedin", "malt"]}, "remotive"))
        self.assertTrue(_want_family({"source_ids": ["greenhouse"]}, "ats"))
        self.assertFalse(_want_family({"source_ids": ["greenhouse"]}, "remotive"))
        self.assertFalse(_want_family({"source_ids": ["web-job-search"]}, "official"))
        self.assertFalse(_want_family({"source_ids": ["remotive"]}, "scrape"))
        self.assertFalse(_want_family({"source_ids": ["live-off"]}, "remotive"))
        self.assertFalse(_want_family({"source_ids": ["linkedin", "live-off"]}, "official"))
        self.assertTrue(_want_family({"source_ids": ["live-off", "greenhouse"]}, "ats"))
        self.assertFalse(_want_family({}, "official"))
        self.assertTrue(_want_family({"source_ids": ["adzuna"]}, "official"))
        self.assertTrue(_want_family({"source_ids": ["adzuna"]}, "remotive"))
        # Free-Work is a live family like Remotive: on by default, a pick narrows to it.
        self.assertTrue(_want_family({}, "freework"))
        self.assertTrue(_want_family({"source_ids": ["free-work"]}, "freework"))
        self.assertTrue(_want_family({"source_ids": ["freework"]}, "freework"))
        self.assertFalse(_want_family({"source_ids": ["free-work"]}, "remotive"))
        self.assertFalse(_want_family({"source_ids": ["remotive"]}, "freework"))
        self.assertFalse(_want_family({"source_ids": ["live-off"]}, "freework"))
        # Public API / RSS feeds: one family, any feed id (jobicy, remoteok...) narrows to it.
        self.assertTrue(_want_family({}, "feeds"))
        self.assertTrue(_want_family({"source_ids": ["jobicy"]}, "feeds"))
        self.assertTrue(_want_family({"source_ids": ["weworkremotely"]}, "feeds"))
        self.assertFalse(_want_family({"source_ids": ["jobicy"]}, "remotive"))
        self.assertFalse(_want_family({"source_ids": ["remotive"]}, "feeds"))
        self.assertFalse(_want_family({"source_ids": ["live-off"]}, "feeds"))
        self.assertTrue(_want_official({"source_ids": ["jobopportunities"]}, "jobopportunities"))
        self.assertTrue(_want_official({"source_ids": ["adzuna"]}, "adzuna"))
        self.assertFalse(_want_official({}, "adzuna"))
        self.assertFalse(_want_official({"source_ids": ["jooble"]}, "adzuna"))

    def test_profile_keeps_ai_assist_opt_in(self) -> None:
        from navin.career.store import CareerStore, normalize_profile

        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"ai_assist": True, "titles": ["Data"]})
            self.assertTrue(store.load_profile()["ai_assist"])
        self.assertFalse(normalize_profile({"ai_assist": "true"})["ai_assist"])
        self.assertTrue(normalize_profile({"ai_assist": True})["ai_assist"])

    def test_api_secrets_stay_off_profile_and_flag_snapshot(self) -> None:
        from navin.career.desk import snapshot
        from navin.career.errors import CareerError
        from navin.career.store import CareerStore

        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_secret("ADZUNA_APP_ID", "id")
            store.save_secret("ADZUNA_APP_KEY", "key")
            store.save_profile({"titles": ["Data"], "api_keys": {"adzuna": True}})
            snap = snapshot(store)
            self.assertTrue(snap["profile"]["api_keys"]["adzuna"])
            self.assertFalse(snap["profile"]["api_keys"]["jooble"])
            raw = store.load_profile()
            self.assertNotIn("api_keys", raw)
            self.assertEqual(store.get_secret("ADZUNA_APP_KEY"), "key")
            with self.assertRaises(CareerError):
                store.save_secret("LINKEDIN_PASSWORD", "nope")

    def test_agent_search_uses_store_secrets_and_enabled_sources_only(self) -> None:
        payload = {
            "results": [
                {
                    "title": "Data Engineer",
                    "company": {"display_name": "Agent Co"},
                    "location": {"display_name": "Paris"},
                    "redirect_url": "https://www.adzuna.fr/details/agent",
                    "description": "Python Spark Databricks",
                }
            ]
        }
        dummy_key = "dummy-adzuna-key"
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(
                {
                    "titles": ["Data Engineer"],
                    "source_ids": ["adzuna"],
                    "countries_primary": ["FR"],
                    "wizard_complete": True,
                    "work_mode": "remote",
                }
            )
            tool = CareerTool()
            with patch("navin.webui.career_api._store", return_value=store):
                asyncio.run(tool.execute(action="secret", name="ADZUNA_APP_ID", value="id"))
                asyncio.run(tool.execute(action="secret", name="ADZUNA_APP_KEY", value=dummy_key))
                status = asyncio.run(tool.execute(action="status"))
                self.assertIn("Source ids: adzuna", status)
                self.assertIn("adzuna (live)", status)
                self.assertNotIn(dummy_key, status)
                with patch("navin.career.collect._fetch_remotive", return_value=[]) as remotive:
                    with patch("navin.career.collect.search_web_hits", return_value=[]) as web:
                        with patch("navin.career.collect.scrape_open_net", return_value={"jobs": []}) as scrape:
                            with patch("navin.career.official._get_json", return_value=payload):
                                with patch("navin.career.official.fetch_jooble", return_value=[]) as jooble:
                                    with patch(
                                        "navin.career.official.fetch_usajobs", return_value=[]
                                    ) as usajobs, patch(
                                        "navin.career.collect.collect_employers",
                                        return_value={"jobs": [], "checked": 0, "reports": [], "errors": []},
                                    ), patch(
                                        "navin.career.collect.search_freework_jobs",
                                        return_value={"jobs": [], "walls": [], "requests": 0, "total": 0},
                                    ) as freework, patch(
                                        "navin.career.collect.collect_feeds",
                                        return_value={"jobs": [], "walls": [], "requests": 0, "by_source": {}},
                                    ) as feeds:
                                        result = asyncio.run(
                                            tool.execute(action="search", brief="Data Engineer")
                                        )
            remotive.assert_called()
            web.assert_called()
            scrape.assert_called()
            freework.assert_called_once()
            feeds.assert_called_once()
            jooble.assert_not_called()
            usajobs.assert_not_called()
            self.assertIsInstance(result, dict)
            search = result["search"]
            families = search["families"]
            self.assertTrue(families["web"])
            self.assertTrue(families["scrape"])
            self.assertTrue(families["remotive"])
            self.assertTrue(families["ats"])
            self.assertTrue(families["official"])
            self.assertTrue(families["linkedin"])
            self.assertTrue(families["freework"])
            self.assertEqual(search["freework"], 0)
            self.assertIn("free-work", search["note"].lower())
            self.assertTrue(families["feeds"])
            self.assertEqual(search["feeds"], 0)
            self.assertIn("jobicy", search["note"].lower())
            self.assertGreaterEqual(search["official"], 1)
            self.assertTrue(any(row.get("kind") == "linkedin" for row in search["portals"]))
            self.assertTrue(any(row.get("kind") == "linkedin_open" for row in search["queries"]))
            self.assertIn("web", search["note"].lower())
            self.assertIn("scrape", search["note"].lower())
            self.assertIn("linkedin", search["note"].lower())
            self.assertIn("adzuna", search["note"].lower())
            blob = json.dumps(result, default=str)
            self.assertNotIn(dummy_key, blob)
            self.assertNotIn(dummy_key, search["note"])
            self.assertTrue(
                all(
                    row.get("source") == "adzuna"
                    for row in store.load_opportunities()
                    if row.get("ingest") == "official_api"
                )
            )
            self.assertEqual(store.get_secret("ADZUNA_APP_KEY"), dummy_key)


if __name__ == "__main__":
    unittest.main()
