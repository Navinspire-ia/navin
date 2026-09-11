# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Collective.work public listing reader: Next payload, rows, budget, walls. No network."""

from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlparse

from navin.career.collective import (
    PAGE_SIZE,
    CollectiveWallError,
    locale_for,
    parse_search_page,
    search_collective_jobs,
    search_url,
    skill_label,
    supported_markets,
    to_job,
)


def _page(projects: list[dict], *, total: int | None = None, start: int = 0, query: str = "data engineer") -> str:
    payload = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": ["PublicPages_SearchJobs", {"data": {"query": query, "from": start}}],
                            "state": {
                                "data": {
                                    "results": {
                                        "projects": projects,
                                        "pagination": {"from": start, "total": total if total is not None else len(projects)},
                                    }
                                }
                            },
                        }
                    ]
                }
            }
        }
    }
    return f'<html><head><title>Collective</title><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></head></html>'


def _project(slug: str, name: str, **extra: object) -> dict:
    row = {
        "id": f"id-{slug}",
        "slug": slug,
        "name": name,
        "sumUp": name,
        "description": "<p>Mission Data Engineer 6 mois, Spark et Azure.</p>",
        "budgetBrief": "TJM 420-480",
        "workPreferences": ["HYBRID"],
        "isPermanentContract": False,
        "projectTypes": ["PYTHON", "GOOGLE_CLOUD_PLATFORM"],
        "publishedAt": "2026-09-11T08:09:36.496Z",
        "minSalary": None,
        "maxSalary": None,
        "salaryCurrency": None,
        "salaryFrequency": None,
        "company": {"name": "Celad"},
        "location": {"fullNameEnglish": "Paris, France", "fullNameFrench": "Paris, France"},
    }
    row.update(extra)
    return row


class CollectiveSearchUrlTest(unittest.TestCase):
    def test_search_and_contract_and_page(self) -> None:
        first = search_url("Data Engineer", locale="fr", track="freelance")
        parsed = urlparse(first)
        self.assertEqual(parsed.path, "/jobs/fr")
        qs = parse_qs(parsed.query)
        self.assertEqual(qs["search"], ["Data Engineer"])
        self.assertEqual(qs["contractType"], ["Freelance"])
        self.assertNotIn("page", qs)
        second = search_url("Data Engineer", locale="en", track="jobs", page=2)
        qs2 = parse_qs(urlparse(second).query)
        self.assertEqual(urlparse(second).path, "/jobs/en")
        self.assertEqual(qs2["contractType"], ["Permanent"])
        self.assertEqual(qs2["page"], ["2"])

    def test_supported_markets_and_locale(self) -> None:
        self.assertEqual(supported_markets(["FR", "US", "BE", "FR"]), ["FR", "BE"])
        self.assertEqual(supported_markets(["US", "AE"]), [])
        self.assertEqual(locale_for(["FR", "GB"]), "fr")
        self.assertEqual(locale_for(["GB"]), "en")
        self.assertEqual(skill_label("GOOGLE_CLOUD_PLATFORM"), "Google Cloud Platform")


class CollectiveParseTest(unittest.TestCase):
    def test_parse_next_payload(self) -> None:
        parsed = parse_search_page(_page([_project("data-engineer-6rny", "Data Engineer")], total=1839, start=30))
        assert parsed is not None
        self.assertEqual(parsed["total"], 1839)
        self.assertEqual(parsed["from"], 30)
        self.assertEqual(parsed["jobs"][0]["slug"], "data-engineer-6rny")

    def test_parse_missing_payload(self) -> None:
        self.assertIsNone(parse_search_page("<html>Just a moment...</html>"))

    def test_job_row_is_a_collective_public_opportunity(self) -> None:
        job = to_job(_project("data-engineer-gcp-freelance-w65r", "Data Engineer GCP - Freelance"), locale="fr", track="freelance")
        assert job is not None
        self.assertEqual(job["source"], "collective")
        self.assertEqual(job["ingest"], "collective_public")
        self.assertEqual(job["url"], "https://www.collective.work/jobs/fr/data-engineer-gcp-freelance-w65r")
        self.assertEqual(job["company"], "Celad")
        self.assertEqual(job["country"], "FR")
        self.assertEqual(job["track"], "freelance")
        self.assertEqual(job["remote"], "hybrid")
        self.assertIn("Python", job["stack"])
        self.assertIn("Google Cloud Platform", job["stack"])
        self.assertEqual(job["daily_rate_max"], 480.0)
        self.assertEqual(job["compensation"], 480.0)
        self.assertEqual(job["collective_id"], "id-data-engineer-gcp-freelance-w65r")
        self.assertEqual(job["posted_at"], "2026-09-11")

    def test_permanent_row_stays_on_the_jobs_track(self) -> None:
        job = to_job(
            _project(
                "developpeur-back-end-confirme-hf-vzco",
                "Developpeur back end confirme (H/F)",
                isPermanentContract=True,
                budgetBrief="38000-44000",
            ),
            locale="fr",
            track="jobs",
        )
        assert job is not None
        self.assertEqual(job["track"], "jobs")
        self.assertTrue(to_job(_project("x", "X", isPermanentContract=True), locale="fr", track="freelance") is None)

    def test_belgium_location_maps_to_be(self) -> None:
        job = to_job(
            _project(
                "product-brussels",
                "Product Journey Manager",
                location={"fullNameEnglish": "Bruxelles, Belgique", "fullNameFrench": "Bruxelles, Belgique"},
            ),
            locale="fr",
            track="freelance",
        )
        assert job is not None
        self.assertEqual(job["country"], "BE")


class CollectiveCollectTest(unittest.TestCase):
    def test_reads_two_pages_then_stops(self) -> None:
        pages = {
            search_url("Data Engineer", locale="fr", track="freelance"): _page(
                [_project(f"p{i}", f"Data Engineer {i}") for i in range(PAGE_SIZE)],
                total=60,
                start=0,
            ),
            search_url("Data Engineer", locale="fr", track="freelance", page=2): _page(
                [_project("p-next", "Data Engineer next")],
                total=60,
                start=30,
            ),
        }

        def getter(url: str) -> str:
            return pages[url]

        out = search_collective_jobs(
            titles=["Data Engineer"],
            countries=["FR"],
            track="freelance",
            http_get=getter,
            sleep=lambda _s: None,
        )
        self.assertEqual(out["requests"], 2)
        self.assertEqual(out["total"], 60)
        self.assertEqual(len(out["jobs"]), PAGE_SIZE + 1)
        self.assertEqual(out["walls"], [])

    def test_wall_stops_the_run(self) -> None:
        def getter(_url: str) -> str:
            raise CollectiveWallError("HTTP 403")

        out = search_collective_jobs(
            titles=["Data Engineer"],
            countries=["FR"],
            http_get=getter,
            sleep=lambda _s: None,
        )
        self.assertEqual(out["jobs"], [])
        self.assertTrue(out["walls"][0].startswith("Collective.work HTTP 403"))

    def test_unsupported_markets_cost_no_request(self) -> None:
        def never(_url: str) -> str:
            raise AssertionError("no request")

        out = search_collective_jobs(titles=["Data Engineer"], countries=["US", "AE"], http_get=never, sleep=lambda _s: None)
        self.assertEqual(out["jobs"], [])
        self.assertEqual(out["requests"], 0)
        self.assertEqual(out["markets"], [])
