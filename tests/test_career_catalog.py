# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Every Career catalog source, tested one by one. No grouped shortcuts."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from navin.career.collect import _fetch_remotive, _normalize_ats
from navin.career.official import collect_official_apis, fetch_adzuna, fetch_jooble, fetch_usajobs
from navin.career.sources import (
    CATALOG,
    CLOSED_FETCH_HOSTS,
    MARKETS,
    OPEN_SCRAPE_HOSTS,
    OPEN_SCRAPE_QUERY_HOSTS,
    catalog,
    host_of,
    is_closed_job_url,
    is_linkedin_url,
    is_open_scrape_url,
    official_search_pack,
    scrape_search_queries,
    source_by_id,
    web_search_queries,
    web_search_run_pack,
)
from navin.career.stack import connector_status

ROOT_IDS = {row["id"] for row in CATALOG}

# One row per catalog id. If CATALOG grows, this map must grow or the test fails.
SOURCE_CONTRACT: dict[str, dict[str, str]] = {
    "adzuna": {"level": "1", "ingest": "official_api", "host": "developer.adzuna.com", "wire": "collector:adzuna"},
    "jooble": {"level": "1", "ingest": "official_api", "host": "jooble.org", "wire": "collector:jooble"},
    "france-travail": {"level": "1", "ingest": "partner_api", "host": "francetravail.io", "wire": "open:francetravail.fr"},
    "usajobs": {"level": "1", "ingest": "official_api", "host": "developer.usajobs.gov", "wire": "collector:usajobs"},
    "remotive": {"level": "1", "ingest": "official_api", "host": "remotive.com", "wire": "open:remotive.com"},
    "greenhouse": {"level": "2", "ingest": "ats_api", "host": "developers.greenhouse.io", "wire": "open:greenhouse.io"},
    "lever": {"level": "2", "ingest": "ats_api", "host": "github.com", "wire": "open:lever.co"},
    "ashby": {"level": "2", "ingest": "ats_api", "host": "developers.ashbyhq.com", "wire": "open:ashbyhq.com"},
    "employers": {"level": "2", "ingest": "employer_feed", "host": "api.smartrecruiters.com", "wire": "collector:employers"},
    "eures": {"level": "1", "ingest": "partner_only", "host": "eures.europa.eu", "wire": "partner"},
    "linkedin": {"level": "4", "ingest": "public_listing", "host": "linkedin.com", "wire": "linkedin"},
    "web-job-search": {"level": "3", "ingest": "search_snippet", "host": "html.duckduckgo.com", "wire": "websearch"},
    "web-search": {"level": "3", "ingest": "search_snippet", "host": "html.duckduckgo.com", "wire": "websearch"},
    "free-work": {"level": "3", "ingest": "web_agent", "host": "free-work.com", "wire": "site:free-work.com"},
    "apec": {"level": "3", "ingest": "web_agent", "host": "apec.fr", "wire": "site:apec.fr"},
    "malt": {"level": "4", "ingest": "open_manual", "host": "malt.fr", "wire": "closed:malt.fr"},
    "wttj": {"level": "3", "ingest": "web_agent", "host": "welcometothejungle.com", "wire": "site:welcometothejungle.com"},
    "ictjob": {"level": "3", "ingest": "web_agent", "host": "ictjob.be", "wire": "site:ictjob.be"},
    "jobs-ch": {"level": "3", "ingest": "web_agent", "host": "jobs.ch", "wire": "site:jobs.ch"},
    "jobserve": {"level": "3", "ingest": "web_agent", "host": "jobserve.com", "wire": "site:jobserve.com"},
    "cwjobs": {"level": "3", "ingest": "web_agent", "host": "cwjobs.co.uk", "wire": "site:cwjobs.co.uk"},
    "dice": {"level": "3", "ingest": "web_agent", "host": "dice.com", "wire": "site:dice.com"},
    "wellfound": {"level": "3", "ingest": "web_agent", "host": "wellfound.com", "wire": "site:wellfound.com"},
    "job-bank-ca": {"level": "3", "ingest": "web_agent", "host": "jobbank.gc.ca", "wire": "site:jobbank.gc.ca"},
    "indeed": {"level": "4", "ingest": "open_manual", "host": "indeed.com", "wire": "closed:indeed.com"},
    "chooseyourboss": {"level": "3", "ingest": "web_agent", "host": "chooseyourboss.com", "wire": "site:chooseyourboss.com"},
    "vdab": {"level": "3", "ingest": "web_agent", "host": "vdab.be", "wire": "site:vdab.be"},
    "le-forem": {"level": "3", "ingest": "web_agent", "host": "leforem.be", "wire": "site:leforem.be"},
    "actiris": {"level": "3", "ingest": "web_agent", "host": "actiris.brussels", "wire": "site:actiris.brussels"},
    "jobat": {"level": "3", "ingest": "web_agent", "host": "jobat.be", "wire": "site:jobat.be"},
    "stepstone-be": {"level": "3", "ingest": "web_agent", "host": "stepstone.be", "wire": "site:stepstone.be"},
    "jobscout24": {"level": "3", "ingest": "web_agent", "host": "jobscout24.ch", "wire": "site:jobscout24.ch"},
    "swissdevjobs": {"level": "3", "ingest": "web_agent", "host": "swissdevjobs.ch", "wire": "site:swissdevjobs.ch"},
    "reed": {"level": "3", "ingest": "web_agent", "host": "reed.co.uk", "wire": "site:reed.co.uk"},
    "totaljobs": {"level": "3", "ingest": "web_agent", "host": "totaljobs.com", "wire": "site:totaljobs.com"},
    "technojobs": {"level": "3", "ingest": "web_agent", "host": "technojobs.co.uk", "wire": "site:technojobs.co.uk"},
    "govuk-find-a-job": {"level": "3", "ingest": "web_agent", "host": "gov.uk", "wire": "site:findajob.dwp.gov.uk"},
    "builtin": {"level": "3", "ingest": "web_agent", "host": "builtin.com", "wire": "site:builtin.com"},
    "ziprecruiter": {"level": "4", "ingest": "open_manual", "host": "ziprecruiter.com", "wire": "closed:ziprecruiter.com"},
    "jobillico": {"level": "3", "ingest": "web_agent", "host": "jobillico.com", "wire": "site:jobillico.com"},
    "jobboom": {"level": "3", "ingest": "web_agent", "host": "jobboom.com", "wire": "site:jobboom.com"},
    "eluta": {"level": "3", "ingest": "web_agent", "host": "eluta.ca", "wire": "site:eluta.ca"},
    "arbeitsagentur": {"level": "3", "ingest": "web_agent", "host": "arbeitsagentur.de", "wire": "site:arbeitsagentur.de"},
    "stepstone-de": {"level": "3", "ingest": "web_agent", "host": "stepstone.de", "wire": "site:stepstone.de"},
    "werk-nl": {"level": "3", "ingest": "web_agent", "host": "werk.nl", "wire": "site:werk.nl"},
    "bayt": {"level": "4", "ingest": "open_manual", "host": "bayt.com", "wire": "closed:bayt.com"},
    "gulftalent": {"level": "4", "ingest": "open_manual", "host": "gulftalent.com", "wire": "closed:gulftalent.com"},
    "jadarat": {"level": "4", "ingest": "open_manual", "host": "jadarat.sa", "wire": "closed:jadarat.sa"},
}

REQUIRED_FIELDS = ("id", "name", "level", "zone", "ingest", "auto_search", "auto_apply", "priority", "url", "notes")
ALLOWED_INGEST = {
    "official_api",
    "partner_api",
    "partner_only",
    "ats_api",
    "web_agent",
    "search_snippet",
    "open_manual",
    "employer_feed",
    "public_listing",
}


def _host(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


class CareerCatalogCompletenessTest(unittest.TestCase):
    def test_contract_covers_every_catalog_id(self) -> None:
        self.assertEqual(set(SOURCE_CONTRACT), ROOT_IDS)

    def test_catalog_ids_are_unique(self) -> None:
        ids = [row["id"] for row in CATALOG]
        self.assertEqual(len(ids), len(set(ids)))


class CareerCatalogRowTest(unittest.TestCase):
    def test_each_source_has_a_complete_contract(self) -> None:
        for row in catalog():
            sid = row["id"]
            with self.subTest(source=sid):
                spec = SOURCE_CONTRACT[sid]
                for field in REQUIRED_FIELDS:
                    self.assertIn(field, row)
                    self.assertTrue(str(row[field]).strip(), field)
                self.assertEqual(str(row["level"]), spec["level"])
                self.assertEqual(row["ingest"], spec["ingest"])
                self.assertIn(row["ingest"], ALLOWED_INGEST)
                self.assertFalse(row["auto_apply"], sid)
                self.assertTrue(str(row["url"]).startswith("https://"), sid)
                self.assertEqual(_host(row["url"]), spec["host"])
                looked = source_by_id(sid)
                self.assertIsNotNone(looked)
                self.assertEqual(looked["name"], row["name"])
                if row["ingest"] == "partner_only":
                    self.assertFalse(row["auto_search"])
                if row["ingest"] == "open_manual":
                    self.assertFalse(row["auto_search"])
                    self.assertFalse(row["auto_apply"])

    def test_each_source_is_wired(self) -> None:
        zone_iso = {
            "FR": "FR",
            "BE": "BE",
            "CH": "CH",
            "GB": "GB",
            "US": "US",
            "CA": "CA",
            "DE": "DE",
            "NL": "NL",
            "SA": "SA",
            "Gulf": "AE",
            "FR EU": "FR",
            "FR EU US": "FR",
            "World": "FR",
            "Remote": "US",
            "Tech": "US",
            "EU": "FR",
            "Markets": "FR",
        }
        scrape_blob = " ".join(
            row["query"] for row in scrape_search_queries(titles=["Data Engineer"], countries=["FR", "US"])
        )
        connectors = {row["id"] for row in connector_status()}
        for row in catalog():
            sid = row["id"]
            spec = SOURCE_CONTRACT[sid]
            wire = spec["wire"]
            iso = zone_iso[row["zone"]]
            pack = official_search_pack("Senior Data Engineer", [iso], track="freelance")
            pack_blob = " ".join(f"{item.get('id')} {item.get('url')}" for item in pack)
            queries = web_search_queries(titles=["Senior Data Engineer"], countries=[iso], track="freelance")
            query_blob = " ".join(item["query"] for item in queries)
            with self.subTest(source=sid, wire=wire, market=iso):
                if wire.startswith("collector:"):
                    self.assertIn(wire.split(":", 1)[1], connectors)
                elif wire.startswith("open:"):
                    host = wire.split(":", 1)[1]
                    self.assertTrue(is_open_scrape_url(f"https://{host}/jobs/1"), host)
                    self.assertIn(host, scrape_blob + pack_blob)
                elif wire.startswith("site:"):
                    host = wire.split(":", 1)[1]
                    self.assertIn(f"site:{host}", query_blob)
                    self.assertTrue(is_closed_job_url(f"https://{host}/jobs/1"), host)
                    self.assertFalse(is_open_scrape_url(f"https://{host}/jobs/1"), host)
                    self.assertIn(host, pack_blob)
                elif wire.startswith("closed:"):
                    host = wire.split(":", 1)[1]
                    self.assertTrue(is_closed_job_url(f"https://www.{host}/x"), host)
                    self.assertFalse(is_open_scrape_url(f"https://www.{host}/x"), host)
                elif wire == "linkedin":
                    self.assertTrue(is_linkedin_url("https://www.linkedin.com/jobs/view/1"))
                    self.assertFalse(is_open_scrape_url("https://www.linkedin.com/jobs/view/1"))
                    self.assertIn("linkedin.com/jobs/search", pack_blob)
                elif wire == "websearch":
                    self.assertTrue(any(item.get("kind") == "web" for item in queries))
                elif wire == "partner":
                    self.assertFalse(row["auto_search"])
                else:
                    self.fail(f"unknown wire {wire}")


class CareerCountryBoardTest(unittest.TestCase):
    def test_each_country_board_is_closed_and_in_the_pack(self) -> None:
        from navin.career.sources import _country_boards

        for iso in MARKETS:
            boards = _country_boards(iso, "Data Engineer")
            self.assertTrue(boards, f"{iso} must have at least one official board")
            pack = official_search_pack("Data Engineer", [iso], track="freelance")
            pack_urls = " ".join(row["url"] for row in pack)
            queries = web_search_queries(titles=["Data Engineer"], countries=[iso], track="freelance")
            query_blob = " ".join(row["query"] for row in queries)
            for board in boards:
                with self.subTest(country=iso, board=board["id"]):
                    url = board["url"]
                    host = host_of(url)
                    self.assertTrue(url.startswith("https://"), board["id"])
                    self.assertEqual(board["country"], iso)
                    self.assertIn(board["kind"], {"board", "api"})
                    self.assertTrue(is_closed_job_url(url) or is_open_scrape_url(url), board["id"])
                    if board["kind"] == "board":
                        self.assertTrue(is_closed_job_url(url), board["id"])
                        self.assertFalse(is_open_scrape_url(url), board["id"])
                    self.assertIn(host, pack_urls + " " + query_blob)
                    self.assertIn(f"li-{iso}", {row["id"] for row in pack})


class CareerOfficialCollectorTest(unittest.TestCase):
    def test_adzuna_collector(self) -> None:
        payload = {
            "results": [
                {
                    "title": "Data Engineer",
                    "company": {"display_name": "Adzuna Co"},
                    "location": {"display_name": "Paris"},
                    "redirect_url": "https://www.adzuna.fr/details/1",
                    "description": "Python",
                    "salary_max": 700,
                    "salary_currency": "EUR",
                    "created": "2026-08-01",
                },
                {
                    "title": "Closed",
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
        self.assertEqual(rows[0]["company"], "Adzuna Co")

    def test_jooble_collector(self) -> None:
        payload = {
            "jobs": [
                {
                    "title": "Data Engineer",
                    "company": "Jooble Co",
                    "location": "Paris",
                    "link": "https://jooble.org/j/1",
                    "snippet": "Python Spark",
                },
                {"title": "No", "link": "https://www.indeed.com/viewjob?jk=1"},
            ]
        }
        with patch.dict("os.environ", {"JOOBLE_API_KEY": "key"}):
            with patch("navin.career.official._post_json", return_value=payload):
                rows = fetch_jooble("Data Engineer", ["FR"], "freelance")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "jooble")
        self.assertEqual(rows[0]["ingest"], "official_api")

    def test_usajobs_collector(self) -> None:
        payload = {
            "SearchResult": {
                "SearchResultItems": [
                    {
                        "MatchedObjectDescriptor": {
                            "PositionTitle": "Data Engineer",
                            "OrganizationName": "GSA",
                            "PositionLocationDisplay": "DC",
                            "PositionURI": "https://www.usajobs.gov/job/1",
                            "QualificationSummary": "Python",
                            "PublicationStartDate": "2026-08-01",
                            "UserArea": {"Details": {"JobSummary": "Python Spark"}},
                        }
                    }
                ]
            }
        }
        with patch.dict("os.environ", {"USAJOBS_API_KEY": "k", "USAJOBS_USER_AGENT": "navin"}):
            with patch("navin.career.official._get_json", return_value=payload):
                rows = fetch_usajobs("Data Engineer", ["US"], "jobs")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "usajobs")
        self.assertEqual(rows[0]["country"], "US")
        empty = fetch_usajobs("Data Engineer", ["FR"], "jobs")
        self.assertEqual(empty, [])

    def test_collect_official_apis_is_silent_without_keys(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(collect_official_apis("Data Engineer", ["FR"], "freelance"), [])

    def test_collect_official_apis_honors_enabled_sources(self) -> None:
        with patch("navin.career.official.fetch_adzuna", return_value=[{"source": "adzuna"}]) as adzuna:
            with patch("navin.career.official.fetch_jooble", return_value=[{"source": "jooble"}]) as jooble:
                rows = collect_official_apis("Data Engineer", ["FR"], "freelance", sources=["jooble"])
        self.assertEqual(rows, [{"source": "jooble"}])
        adzuna.assert_not_called()
        jooble.assert_called_once()

    def test_adzuna_uses_store_secrets_without_env(self) -> None:
        payload = {
            "results": [
                {
                    "title": "Data Engineer",
                    "company": {"display_name": "Secret Co"},
                    "location": {"display_name": "Paris"},
                    "redirect_url": "https://www.adzuna.fr/details/2",
                    "description": "Python",
                }
            ]
        }
        with patch.dict("os.environ", {}, clear=True):
            with patch("navin.career.official._get_json", return_value=payload):
                rows = fetch_adzuna(
                    "Data Engineer",
                    ["FR"],
                    "freelance",
                    secrets={"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key"},
                )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Secret Co")

    def test_remotive_collector(self) -> None:
        payload = {
            "jobs": [
                {
                    "title": "Data Engineer",
                    "company_name": "Remotive Co",
                    "url": "https://remotive.com/remote-jobs/1",
                    "candidate_required_location": "Remote",
                    "description": "Python",
                    "tags": ["python"],
                }
            ]
        }
        with patch("navin.career.collect.json.loads", return_value=payload):
            with patch("urllib.request.urlopen") as opener:
                opener.return_value.__enter__.return_value.read.return_value = b"{}"
                rows = _fetch_remotive("Data Engineer")
        self.assertEqual(rows[0]["source"], "remotive")
        self.assertEqual(rows[0]["country"], "REMOTE")

    def test_greenhouse_board(self) -> None:
        rows = _normalize_ats(
            "greenhouse",
            "acme",
            {"jobs": [{"title": "Data Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "content": "Python", "location": {"name": "Paris"}}]},
        )
        self.assertEqual(rows[0]["source"], "greenhouse")
        self.assertTrue(is_open_scrape_url(rows[0]["url"]))

    def test_lever_board(self) -> None:
        rows = _normalize_ats(
            "lever",
            "acme",
            [{"text": "Data Engineer", "hostedUrl": "https://jobs.lever.co/acme/1", "descriptionPlain": "Python", "categories": {"location": "Remote"}}],
        )
        self.assertEqual(rows[0]["source"], "lever")
        self.assertTrue(is_open_scrape_url(rows[0]["url"]))

    def test_ashby_board(self) -> None:
        rows = _normalize_ats(
            "ashby",
            "acme",
            {"jobs": [{"title": "Data Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/1", "descriptionPlain": "Python", "location": "NY"}]},
        )
        self.assertEqual(rows[0]["source"], "ashby")
        self.assertTrue(is_open_scrape_url(rows[0]["url"]))

    def test_ats_drops_linkedin_urls(self) -> None:
        rows = _normalize_ats(
            "greenhouse",
            "acme",
            {"jobs": [{"title": "Secret", "absolute_url": "https://www.linkedin.com/jobs/view/1"}]},
        )
        self.assertEqual(rows, [])


class CareerConnectorLiveTest(unittest.TestCase):
    def test_wizard_ready_now_excludes_keyed_and_missing_fetchers(self) -> None:
        import os

        with patch.dict(
            os.environ,
            {
                "ADZUNA_APP_ID": "",
                "ADZUNA_APP_KEY": "",
                "JOOBLE_API_KEY": "",
                "USAJOBS_API_KEY": "",
                "USAJOBS_USER_AGENT": "",
            },
            clear=False,
        ):
            by_id = {row["id"]: row for row in connector_status()}
        self.assertTrue(by_id["remotive"]["live"])
        self.assertTrue(by_id["ats"]["live"])
        self.assertTrue(by_id["web-job-search"]["live"])
        self.assertFalse(by_id["adzuna"]["live"])
        self.assertFalse(by_id["jooble"]["live"])
        self.assertFalse(by_id["usajobs"]["live"])
        # Public guest listings need no key: LinkedIn is live out of the box.
        self.assertTrue(by_id["linkedin"]["live"])
        self.assertEqual(by_id["linkedin"]["ingest"], "public_listing")
        self.assertNotIn("france-travail", by_id)


class CareerFallbackCatalogTest(unittest.TestCase):
    def test_frontend_fallback_lists_every_catalog_id(self) -> None:
        from pathlib import Path

        text = (Path(__file__).resolve().parents[1] / "webui/src/lib/career-api.ts").read_text(encoding="utf-8")
        missing = [sid for sid in sorted(ROOT_IDS) if f'id: "{sid}"' not in text]
        self.assertEqual(missing, [], f"FALLBACK_CATALOG missing {missing}")
        self.assertIn('id: "web-job-search"', text)
        self.assertIn("live: true", text.split('id: "web-job-search"', 1)[1][:80])

    def test_wizard_presets_match_backend_markets(self) -> None:
        import re
        from pathlib import Path

        text = (
            Path(__file__).resolve().parents[1] / "webui/src/components/studio/career/career-ui.ts"
        ).read_text(encoding="utf-8")
        start = text.index("export const MARKET_PRESETS")
        end = text.index("] as const", start)
        presets = re.findall(r'"([A-Z]{2})"', text[start:end])
        self.assertEqual(presets, list(MARKETS))
        self.assertIn("MA", presets)
        self.assertIn("TN", presets)
        self.assertIn("ES", presets)
        self.assertIn("IT", presets)

    def test_tauri_fixture_covers_catalog_and_every_market_pack(self) -> None:
        import json
        from pathlib import Path

        rows = json.loads(
            (Path(__file__).resolve().parents[1] / "webui/src/lib/career-catalog-urls.json").read_text(
                encoding="utf-8"
            )
        )
        ids = {row["id"] for row in rows}
        urls = {row["url"] for row in rows}
        self.assertTrue(ROOT_IDS <= ids, f"fixture missing catalog ids {sorted(ROOT_IDS - ids)}")
        for iso in MARKETS:
            pack = official_search_pack("Data Engineer", [iso], track="freelance", work_mode="remote")
            for row in pack:
                self.assertIn(row["url"], urls, f"{iso} {row['id']}")
        for row in rows:
            self.assertTrue(str(row["url"]).startswith("http"), row["id"])

    def test_linkedin_mcp_stays_off_until_the_user_enables_it(self) -> None:
        from navin.webui.mcp_presets_api import MCP_PRESETS

        linkedin = next(row for row in MCP_PRESETS if row.name == "linkedin")
        self.assertFalse(linkedin.auto_enable)
        self.assertIn("career", linkedin.modules)

    def test_opener_capability_covers_linux_windows_macos(self) -> None:
        import json
        from pathlib import Path

        payload = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "desktop/src-tauri/capabilities/gateway-opener.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(sorted(payload["platforms"]), ["linux", "macOS", "windows"])
        self.assertIn("opener:allow-open-url", payload["permissions"])


class CareerCompanyCareerSearchTest(unittest.TestCase):
    def test_web_queries_cover_company_careers_and_public_ats(self) -> None:
        queries = web_search_queries(titles=["Data Engineer"], countries=["FR", "US"], track="jobs")
        blob = " ".join(row["query"] for row in queries)
        self.assertIn("job openings", blob)
        self.assertIn("nous recrutons", blob)
        self.assertIn("offre d'emploi", blob)
        self.assertIn("join our team", blob)
        self.assertIn("site:boards.greenhouse.io", blob)
        self.assertIn("site:jobs.lever.co", blob)
        self.assertIn("site:jobs.ashbyhq.com", blob)
        self.assertIn("site:jobs.workable.com", blob)
        self.assertIn("site:jobs.smartrecruiters.com", blob)
        self.assertTrue(any(row.get("kind") == "linkedin_open" for row in queries))
        pack = web_search_run_pack(titles=["Data Engineer"], countries=["FR", "US"], track="jobs")
        pack_blob = " ".join(row["query"] for row in pack)
        self.assertIn("boards.greenhouse.io", pack_blob)
        self.assertIn("job openings", pack_blob)
        self.assertTrue(all(row.get("kind") == "web" for row in pack))

    def test_scrape_queries_cover_open_hosts_never_closed_boards(self) -> None:
        rows = scrape_search_queries(titles=["Data Engineer"], countries=["FR", "US"], track="jobs")
        blob = " ".join(row["query"] for row in rows)
        sites = [str(row.get("site") or "") for row in rows]
        for host in OPEN_SCRAPE_HOSTS:
            mapped = OPEN_SCRAPE_QUERY_HOSTS.get(host, host)
            self.assertTrue(any(mapped == site or mapped in site for site in sites), host)
            self.assertTrue(is_open_scrape_url(f"https://{mapped}/jobs/1"), mapped)
            self.assertNotIn(host, CLOSED_FETCH_HOSTS)
        for blocked in ("linkedin.com", "indeed.com", "bayt.com", "welcometothejungle.com"):
            self.assertNotIn(f"site:{blocked}", blob)
            self.assertTrue(is_closed_job_url(f"https://www.{blocked}/jobs/1"), blocked)
            self.assertFalse(is_open_scrape_url(f"https://www.{blocked}/jobs/1"), blocked)
        self.assertTrue(is_open_scrape_url("https://jobs.workable.com/view/1"))
        self.assertTrue(is_open_scrape_url("https://jobs.smartrecruiters.com/acme/1"))
        self.assertFalse(is_closed_job_url("https://jobs.workable.com/view/1"))
        self.assertFalse(is_closed_job_url("https://jobs.smartrecruiters.com/acme/1"))


if __name__ == "__main__":
    unittest.main()
