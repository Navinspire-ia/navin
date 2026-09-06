"""End-to-end Career integration: desk, tool, loop, sandbox, heartbeat, MCP, API."""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.agent.loop import AgentLoop
from navin.agent.model_routes import product_module_role
from navin.agent.tools.career import CareerTool
from navin.agent.tools.loader import ToolLoader
from navin.agent.tools.sandbox import writable_host_paths
from navin.career.collect import collect
from navin.career.matching import score_opportunity
from navin.career.sources import (
    CLOSED_FETCH_HOSTS,
    MARKETS,
    OPEN_SCRAPE_HOSTS,
    SEARCH_LANG_TERMS,
    catalog,
    is_closed_job_url,
    is_linkedin_url,
    is_open_scrape_url,
    official_search_pack,
    web_search_queries,
)
from navin.career.stack import CAREER_MCP, CAREER_SKILLS, module_stack
from navin.career.store import CareerStore
from navin.command.builtin import _WORKFLOW_BRIEFS
from navin.command.modules import (
    default_preload_skills_for_module,
    extra_denied_tools_for_module,
    is_command_allowed_for_module,
)
from navin.config.paths import get_runtime_subdir
from navin.webui.career_api import handle_career_action
from navin.webui.mcp_presets_api import MCP_PRESETS

ROOT = Path(__file__).resolve().parents[1]
CORE_MARKETS = ("FR", "BE", "CH", "GB", "US", "CA")


def _empty_collectors():
    return {
        "navin.career.collect._fetch_remotive": [],
        "navin.career.collect._fetch_ats_boards": [],
        "navin.career.collect.collect_official_apis": [],
        "navin.career.collect.search_web_hits": [],
        "navin.career.collect.scrape_open_net": {"jobs": [], "walls": [], "refused": []},
        "navin.career.collect.search_linkedin_jobs": {"jobs": [], "walls": [], "requests": 0},
        "navin.career.collect.collect_employers": {"jobs": [], "checked": 0, "reports": [], "errors": []},
    }


class CareerSourceContractTest(unittest.TestCase):
    def test_four_levels_and_linkedin_is_first(self) -> None:
        rows = catalog()
        levels = {row["id"]: row["level"] for row in rows}
        self.assertEqual(levels["adzuna"], 1)
        self.assertEqual(levels["jooble"], 1)
        self.assertEqual(levels["france-travail"], 1)
        self.assertEqual(levels["usajobs"], 1)
        self.assertEqual(levels["remotive"], 1)
        self.assertEqual(levels["greenhouse"], 2)
        self.assertEqual(levels["lever"], 2)
        self.assertEqual(levels["ashby"], 2)
        self.assertEqual(levels["web-job-search"], 3)
        self.assertEqual(levels["free-work"], 3)
        self.assertEqual(levels["jobserve"], 3)
        self.assertEqual(levels["dice"], 3)
        self.assertEqual(levels["linkedin"], 4)
        self.assertEqual(levels["indeed"], 4)
        self.assertEqual(levels["malt"], 4)
        linkedin = next(row for row in rows if row["id"] == "linkedin")
        self.assertEqual(linkedin["priority"], 10)
        self.assertFalse(linkedin["auto_apply"])
        self.assertEqual(linkedin["ingest"], "public_listing")

    def test_closed_boards_are_never_open_scrape(self) -> None:
        for host in (
            "linkedin.com",
            "free-work.com",
            "jobserve.com",
            "dice.com",
            "ictjob.be",
            "jobs.ch",
            "jobbank.gc.ca",
            "indeed.com",
            "malt.fr",
        ):
            url = f"https://www.{host}/jobs/1"
            self.assertTrue(is_closed_job_url(url), host)
            self.assertFalse(is_open_scrape_url(url), host)
        self.assertTrue(is_linkedin_url("https://www.linkedin.com/jobs/view/1"))
        self.assertTrue(is_open_scrape_url("https://boards.greenhouse.io/acme/jobs/1"))
        self.assertTrue(is_open_scrape_url("https://jobs.lever.co/acme/1"))
        self.assertTrue(is_open_scrape_url("https://jobs.ashbyhq.com/acme"))
        self.assertTrue(is_open_scrape_url("https://jobs.workable.com/view/1"))
        self.assertTrue(is_open_scrape_url("https://jobs.smartrecruiters.com/acme/1"))
        self.assertTrue(is_open_scrape_url("https://www.arbeitnow.com/jobs/1"))
        self.assertTrue(is_open_scrape_url("https://himalayas.app/jobs/1"))
        self.assertTrue(all(host not in OPEN_SCRAPE_HOSTS for host in ("linkedin.com", "indeed.com", "bayt.com")))
        self.assertIn("linkedin.com", CLOSED_FETCH_HOSTS)

    def test_official_pack_starts_with_linkedin_for_core_markets(self) -> None:
        pack = official_search_pack(
            "Senior Data Engineer",
            list(CORE_MARKETS),
            track="freelance",
        )
        by_country = {}
        for row in pack:
            by_country.setdefault(row.get("country"), []).append(row)
        for iso in CORE_MARKETS:
            first = by_country[iso][0]
            self.assertEqual(first["kind"], "linkedin", iso)
            self.assertIn("linkedin.com/jobs", first["url"])
        urls = " ".join(row["url"] for row in pack)
        self.assertIn("free-work.com", urls)
        self.assertIn("ictjob.be", urls)
        self.assertIn("jobs.ch", urls)
        self.assertIn("jobserve.com", urls)
        self.assertIn("dice.com", urls)
        self.assertIn("jobbank.gc.ca", urls)

    def test_web_search_uses_market_languages_and_aliases(self) -> None:
        queries = web_search_queries(
            titles=["Senior Data Engineer"],
            countries=list(CORE_MARKETS) + ["AE", "DE"],
            track="freelance",
            stack=["Python"],
        )
        blob = " ".join(row["query"] for row in queries)
        self.assertIn(SEARCH_LANG_TERMS["fr"], blob)
        self.assertIn(SEARCH_LANG_TERMS["en"], blob)
        self.assertIn(SEARCH_LANG_TERMS["de"], blob)
        self.assertIn(SEARCH_LANG_TERMS["nl"], blob)
        self.assertIn(SEARCH_LANG_TERMS["ar"], blob)
        self.assertTrue({row["country"] for row in queries} >= {"FR", "BE", "CH", "GB", "US", "CA", "AE", "DE", "LI"})
        self.assertIn("mission 6 months", blob)
        self.assertIn("Bruxelles", blob)
        self.assertIn("Brussel", blob)
        self.assertIn("IR35", blob)
        self.assertIn("1099", blob)
        self.assertIn("contractuel", blob)
        self.assertIn("site:free-work.com", blob)
        self.assertIn("site:jobserve.com", blob)
        self.assertTrue(any(row.get("kind") == "linkedin_open" for row in queries))
        self.assertIn("job openings", blob)
        self.assertIn("site:boards.greenhouse.io", blob)
        self.assertEqual(MARKETS["FR"]["langs"], ("fr", "en"))
        self.assertEqual(MARKETS["BE"]["langs"], ("fr", "nl", "en"))
        self.assertEqual(MARKETS["CH"]["langs"], ("fr", "de", "en"))
        self.assertEqual(MARKETS["AE"]["langs"], ("en", "ar"))

    def test_collect_drops_excluded_markets_from_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(
                {
                    "titles": ["Data Engineer"],
                    "track": "freelance",
                    "countries_primary": ["FR", "BE"],
                    "countries_secondary": ["US"],
                    "countries_excluded": ["GB"],
                    "wizard_complete": True,
                }
            )
            patches = [
                patch(target, return_value=value)
                for target, value in _empty_collectors().items()
            ]
            for item in patches:
                item.start()
            try:
                result = collect(store, query="Data Engineer", track="freelance")
            finally:
                for item in patches:
                    item.stop()
            web_countries = {
                str(row.get("country"))
                for row in result["queries"]
                if row.get("kind") == "web"
            }
            portal_countries = {str(row.get("country")) for row in result["portals"]}
            self.assertIn("FR", web_countries)
            self.assertIn("BE", web_countries)
            self.assertIn("US", web_countries)
            self.assertNotIn("GB", web_countries)
            collect_src = (ROOT / "navin/career/collect.py").read_text(encoding="utf-8")
            self.assertNotIn("linkedin-mcp", collect_src)
            self.assertNotIn("mcp_session", collect_src)
            self.assertFalse(
                any(str(row.get("ingest") or "") == "linkedin_mcp" for row in store.load_opportunities())
            )
            self.assertNotIn("GB", portal_countries)
            self.assertTrue(any(row.get("kind") == "linkedin" for row in result["portals"]))

    def test_collect_drops_listing_pages_and_never_invents_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(
                {
                    "titles": ["Data Engineer"],
                    "track": "freelance",
                    "countries_primary": ["FR"],
                    "wizard_complete": True,
                }
            )
            remotive_rows = [
                {
                    "id": "job-keep",
                    "source": "remotive",
                    "title": "Senior Data Engineer",
                    "url": "https://remotive.com/remote-jobs/keep",
                    "company": "Keep Co",
                    "country": "REMOTE",
                    "description": "Python",
                },
                {
                    "id": "job-list",
                    "source": "remotive",
                    "title": "Fiche métier Data engineer",
                    "url": "https://www.apec.fr/tous-nos-metiers/data",
                    "company": "Noise",
                    "country": "FR",
                    "description": "listing",
                },
            ]
            patches = [
                patch(target, return_value=value)
                for target, value in {
                    **_empty_collectors(),
                    "navin.career.collect._fetch_remotive": remotive_rows,
                }.items()
            ]
            for item in patches:
                item.start()
            try:
                collect(store, query="Data Engineer", track="freelance")
            finally:
                for item in patches:
                    item.stop()
            rows = store.load_opportunities()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["company"], "Keep Co")
            self.assertFalse(any(row.get("company") == "Noise" for row in rows))
            self.assertFalse(any(str(row.get("ingest") or "") == "linkedin_mcp" for row in rows))


class CareerPreferredMarketsTest(unittest.TestCase):
    def test_identical_mission_scores_higher_in_primary_market(self) -> None:
        profile = {
            "titles": ["Data Engineer"],
            "track": "freelance",
            "stack": ["Python", "Spark", "AWS"],
            "languages": ["fr", "en"],
            "work_mode": "remote",
            "min_rate": 650,
            "countries_primary": ["FR"],
            "countries_secondary": ["US"],
            "countries_excluded": ["GB"],
            "country_weights": {"FR": 100.0, "US": 50.0},
        }
        job = {
            "title": "Senior Data Engineer freelance",
            "description": "Python Spark AWS remote Paris fr en",
            "remote": "remote",
            "compensation": 700,
            "track": "freelance",
            "stack": ["Python", "Spark", "AWS"],
            "languages": ["fr", "en"],
        }
        paris = score_opportunity({**job, "country": "FR"}, profile)
        usa = score_opportunity({**job, "country": "US"}, profile)
        uk = score_opportunity({**job, "country": "GB"}, profile)
        self.assertGreaterEqual(paris["match_score"], 90)
        self.assertGreater(paris["match_score"], usa["match_score"])
        self.assertLessEqual(uk["match_score"], 24)
        self.assertEqual(uk["bucket"], "skip")
        self.assertEqual(paris["bucket"], "perfect")


class CareerDeskPipelineTest(unittest.TestCase):
    def test_tool_api_cli_share_one_store_and_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            tool = CareerTool()
            with patch("navin.webui.career_api._store", return_value=store):
                profile = asyncio.run(
                    tool.execute(
                        action="profile",
                        titles="Data Engineer",
                        countries="FR,BE",
                        countries_secondary="US,CA",
                        countries_excluded="GB",
                        track="freelance",
                        min_rate="650",
                        stack="Python, Spark",
                        payload=json.dumps({"wizard_complete": True, "work_mode": "remote"}),
                    )
                )
                saved = profile["profile"]
                self.assertEqual(saved["countries_primary"], ["FR", "BE"])
                self.assertEqual(saved["countries_secondary"], ["US", "CA"])
                self.assertEqual(saved["countries_excluded"], ["GB"])
                self.assertEqual(saved["country_weights"]["FR"], 100.0)
                self.assertEqual(saved["country_weights"]["US"], 70.0)
                self.assertNotIn("GB", saved["country_weights"])

                ingested = asyncio.run(
                    tool.execute(
                        action="ingest",
                        hits=json.dumps(
                            [
                                {
                                    "title": "Senior Data Engineer",
                                    "company": "Paris Co",
                                    "country": "FR",
                                    "url": "https://remotive.com/remote-jobs/paris",
                                    "description": "Python Spark AWS remote",
                                    "compensation": 700,
                                    "track": "freelance",
                                    "stack": ["Python", "Spark"],
                                    "remote": "remote",
                                },
                                {
                                    "title": "Senior Data Engineer",
                                    "company": "Austin Co",
                                    "country": "US",
                                    "url": "https://remotive.com/remote-jobs/austin",
                                    "description": "Python Spark AWS remote",
                                    "compensation": 700,
                                    "track": "freelance",
                                    "stack": ["Python", "Spark"],
                                    "remote": "remote",
                                },
                                {
                                    "title": "Senior Data Engineer",
                                    "company": "London Co",
                                    "country": "GB",
                                    "url": "https://www.jobserve.com/gb/job/1",
                                    "description": "Python Spark AWS remote",
                                    "compensation": 700,
                                    "track": "freelance",
                                },
                            ]
                        ),
                    )
                )
                by_country = {row["country"]: row for row in ingested["opportunities"]}
                self.assertGreater(by_country["FR"]["match_score"], by_country["US"]["match_score"])
                self.assertLessEqual(by_country["GB"]["match_score"], 24)
                self.assertEqual(by_country["GB"]["bucket"], "skip")

                mcp_ingested = asyncio.run(
                    tool.execute(
                        action="ingest",
                        via="linkedin-mcp",
                        hits=json.dumps(
                            [
                                {
                                    "job_title": "Staff Data Engineer",
                                    "job_url": "https://www.linkedin.com/jobs/view/4242",
                                    "company_name": "Session Co",
                                    "location_name": "Paris",
                                    "snippet": "Python Spark AWS remote",
                                }
                            ]
                        ),
                    )
                )
                session_row = next(
                    item
                    for item in mcp_ingested["opportunities"]
                    if item.get("company") == "Session Co"
                )
                self.assertEqual(session_row["source"], "linkedin")
                self.assertEqual(session_row["ingest"], "linkedin_mcp")
                self.assertEqual(session_row["title"], "Staff Data Engineer")
                self.assertEqual(session_row["country"], "FR")
                http_again = handle_career_action(
                    "hits",
                    {
                        "via": "linkedin-mcp",
                        "jobs": [
                            {
                                "job_title": "Staff Data Engineer",
                                "job_url": "https://www.linkedin.com/jobs/view/4242",
                                "company_name": "Session Co",
                                "location_name": "Paris",
                                "snippet": "Python Spark AWS remote",
                            }
                        ],
                    },
                )
                http_row = next(
                    item for item in http_again["opportunities"] if item.get("company") == "Session Co"
                )
                self.assertEqual(http_row["id"], session_row["id"])
                self.assertTrue(http_row["id"].startswith("job-"))
                self.assertEqual(len(http_row["id"]), 16)
                self.assertRegex(http_row["id"], r"^job-[0-9a-f]{12}$")

                oid = by_country["FR"]["id"]
                prepared = asyncio.run(tool.execute(action="prepare", id=oid))
                self.assertIn("Do not invent", prepared["prepared"]["summary"])
                applied = asyncio.run(tool.execute(action="apply", id=oid))
                fr = next(row for row in applied["opportunities"] if row["id"] == oid)
                self.assertIn(fr["stage"], {"ready", "applied"})

                with patch("navin.career.notify.deliver_alert", return_value={"webui": True}):
                    first = handle_career_action("watch")
                    self.assertGreaterEqual(first["watch"]["count"], 1)
                    self.assertTrue(any(event["id"] == oid for event in first["watch"]["events"]))
                    second = handle_career_action("watch")
                    self.assertEqual(second["watch"]["count"], 0)

                    store.update_opportunity(oid, {"stage": "applied", "applied_at": time.time() - 8 * 86400})
                    wave3 = handle_career_action("watch")
                    self.assertEqual(wave3["watch"]["events"][0]["kind"], "followup")
                    self.assertEqual(wave3["watch"]["events"][0]["key"], "j3")
                    wave7 = handle_career_action("watch")
                    self.assertEqual(wave7["watch"]["events"][0]["key"], "j7")

                imported = asyncio.run(
                    tool.execute(
                        action="import",
                        url="https://www.linkedin.com/jobs/view/99",
                        title="Data Engineer LinkedIn",
                        company="Closed Co",
                        body="Python Spark Paris freelance",
                    )
                )
                li = imported["imported"]
                self.assertEqual(li["source"], "linkedin")
                self.assertEqual(li["ingest"], "open_manual")

                from navin.career.desk_cli import main

                buf = io.StringIO()
                with (
                    patch("sys.argv", ["navin.career.desk_cli", "watch"]),
                    patch("sys.stdin", io.StringIO("{}")),
                    patch("sys.stdout", buf),
                ):
                    code = main()
                self.assertEqual(code, 0)
                cli = json.loads(buf.getvalue())
                self.assertIn("watch", cli)
                self.assertEqual(cli["watch"]["count"], 0)

    def test_linkedin_autopilot_stays_blocked_on_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"apply_mode": "autopilot", "titles": ["Data Engineer"]})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-li",
                        "title": "Data Engineer",
                        "source": "linkedin",
                        "url": "https://www.linkedin.com/jobs/view/1",
                        "stage": "ready",
                    }
                ]
            )
            with patch("navin.webui.career_api._store", return_value=store):
                result = asyncio.run(CareerTool().execute(action="apply", id="job-li"))
            self.assertTrue(getattr(result, "is_error", False) or "error" in str(result).lower())


class CareerAgentWiringTest(unittest.TestCase):
    def test_loop_routes_and_keeps_career_tools(self) -> None:
        self.assertEqual(product_module_role("career"), "search")
        denied = extra_denied_tools_for_module("career")
        self.assertEqual(denied, frozenset({"tenders", "trading", "leads", "marketing"}))
        locked = AgentLoop._locked_denied_tools(None, {"product_module": "career"})
        self.assertIn("tenders", locked)
        self.assertIn("trading", locked)
        self.assertNotIn("career", locked)
        self.assertNotIn("scrape", locked)
        self.assertNotIn("web_search", locked)
        live = AgentLoop._denied_tools(None, {"product_module": "career"})
        self.assertNotIn("scrape", live)
        self.assertTrue(AgentLoop._is_heartbeat_metadata(None, {"heartbeat": True}))
        self.assertFalse(AgentLoop._is_heartbeat_metadata(None, {"product_module": "career"}))
        hb = AgentLoop._locked_denied_tools(
            None, {"product_module": "career", "heartbeat": True}
        )
        self.assertNotIn("career", hb)
        self.assertIn("scrape", hb)
        self.assertIn("web_search", hb)
        self.assertIn("browser", hb)
        self.assertIn("cron", hb)
        self.assertTrue(is_command_allowed_for_module("/career", "career"))
        self.assertTrue(is_command_allowed_for_module("/scrape", "career"))
        self.assertFalse(is_command_allowed_for_module("/trading", "career"))
        preload = default_preload_skills_for_module("career")
        for name in CAREER_SKILLS:
            self.assertIn(name, preload)
        title, skills, brief = _WORKFLOW_BRIEFS["/career"]
        self.assertIn("career-agent", skills)
        self.assertIn("watch", brief)
        self.assertIn("Web Job Search", brief)
        self.assertIn("career", title.lower())

    def test_tool_loader_registers_career(self) -> None:
        self.assertTrue(any(cls.__name__ == "CareerTool" for cls in ToolLoader().discover()))
        tool = CareerTool()
        self.assertTrue(tool.call_read_only({"action": "status"}))
        self.assertTrue(tool.call_read_only({"action": "snapshot"}))
        self.assertFalse(tool.call_read_only({"action": "search"}))
        self.assertFalse(tool.call_read_only({"action": "watch"}))
        self.assertIn("watch", tool.parameters["properties"]["action"]["enum"])
        for alias in (
            "snapshot",
            "hits",
            "rescore",
            "mission",
            "cv",
            "write",
            "download",
            "classify",
            "favorite",
            "archive",
            "delete",
            "start",
            "stop",
            "schedule",
            "tick",
            "secret",
        ):
            self.assertIn(alias, tool.parameters["properties"]["action"]["enum"], alias)
        self.assertIn("name", tool.parameters["properties"])
        self.assertIn("value", tool.parameters["properties"])
        self.assertIn("countries_secondary", tool.parameters["properties"])
        self.assertIn("source_ids", tool.parameters["properties"])
        self.assertIn("via", tool.parameters["properties"])

    def test_sandbox_can_write_career_runtime_dir(self) -> None:
        career_dir = get_runtime_subdir("career")
        paths = writable_host_paths(str(tempfile.gettempdir()))
        self.assertIn(str(career_dir), paths)
        self.assertTrue(career_dir.is_dir())

    def test_heartbeat_and_loop_seed_call_watch(self) -> None:
        heartbeat = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        template = (ROOT / "navin/templates/HEARTBEAT.md").read_text(encoding="utf-8")
        skill = (ROOT / "navin/skills/career-agent/SKILL.md").read_text(encoding="utf-8")
        en = (ROOT / "webui/src/i18n/locales/en/common.json").read_text(encoding="utf-8")
        fr = (ROOT / "webui/src/i18n/locales/fr/common.json").read_text(encoding="utf-8")
        for body in (heartbeat, template, skill):
            self.assertIn("career action=watch", body)
            self.assertIn("already ran", body)
        cli = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("tick_heartbeat_desks", cli)
        self.assertIn("Heartbeat: desk ticks failed", cli)
        self.assertIn("product_module=career", skill)
        self.assertIn("career action=start", en)
        self.assertIn("career action=stop", en)
        self.assertIn("career action=watch", en)
        self.assertIn("Never scrape LinkedIn", en)
        self.assertIn("Do not create a chat cron", en)
        self.assertIn("career action=start", fr)
        self.assertIn("career action=stop", fr)
        self.assertIn("career action=watch", fr)
        self.assertIn("Ne cree pas une cron de chat", fr)

    def test_mcp_and_http_and_stack_are_the_same_desk(self) -> None:
        stack = module_stack()
        self.assertEqual(stack["tool"], "career")
        self.assertEqual(stack["desk"], "#/career")
        mcp_ids = {row["id"] for row in CAREER_MCP}
        self.assertEqual(mcp_ids, {"linkedin", "notion", "github", "exa"})
        self.assertEqual(CAREER_MCP[0]["id"], "linkedin")
        self.assertTrue(CAREER_MCP[0]["recommended"])
        linkedin = next(row for row in stack["connectors"] if row["id"] == "linkedin")
        self.assertEqual(linkedin["ingest"], "public_listing")
        self.assertTrue(linkedin["live"])
        by_name = {preset.name: preset for preset in MCP_PRESETS}
        for name in ("linkedin", "notion", "github", "exa"):
            self.assertIn("career", by_name[name].modules)
        http = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn('r"^/api/career$"', http)
        self.assertIn("async def _handle_career", http)
        self.assertIn("handle_career_action", http)
        vite = (ROOT / "webui/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("careerDeskApi", vite)
        portals = (ROOT / "webui/src/lib/career-portals.ts").read_text(encoding="utf-8")
        self.assertIn("slice(0, 48)", portals)


if __name__ == "__main__":
    unittest.main()
