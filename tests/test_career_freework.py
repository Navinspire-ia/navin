# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Free-Work public listing reader: devalue payload, rows, budget, walls. No network."""

from __future__ import annotations

import json
import unittest
from typing import Any
from urllib.parse import parse_qs, urlparse

from navin.career.freework import (
    PAGE_SIZE,
    FreeWorkWallError,
    duration_label,
    duration_months,
    hydrate_devalue,
    parse_money,
    parse_search_page,
    search_freework_jobs,
    search_url,
    supported_markets,
    to_job,
)


def _devalue(value: Any) -> list[Any]:
    """Serialize a Python tree the way devalue / Nuxt `__NUXT_DATA__` does."""
    out: list[Any] = []

    def push(item: Any) -> int:
        index = len(out)
        if isinstance(item, dict):
            out.append(None)
            out[index] = {str(key): push(child) for key, child in item.items()}
        elif isinstance(item, list):
            out.append(None)
            out[index] = [push(child) for child in item]
        else:
            out.append(item)
        return index

    push(value)
    return out


def _record(job_id: int, title: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": job_id,
        "slug": f"{title.lower().replace(' ', '-')}-{job_id}",
        "title": title,
        "description": "<p>Mission <strong>Data</strong> pour une ESN.</p><ul><li>Spark</li><li>Azure</li></ul>",
        "publishedAt": "2026-09-04T10:15:00+02:00",
        "annualSalary": "40k-45k \u20ac",
        "dailySalary": "330-350 \u20ac",
        "contracts": ["contractor"],
        "jobPostingType": "contractor",
        "durationValue": 6,
        "durationPeriod": "month",
        "external": False,
        "remoteMode": "partial",
        "skills": [{"name": "Python", "slug": "python-4"}, {"name": "Spark", "slug": "spark-1"}, {"name": "python", "slug": "python-9"}],
        "location": {"label": "Paris, France"},
        "company": {"name": "Neosoft", "slug": "neosoft"},
        "job": {"slug": "data-engineer"},
    }
    base.update(extra)
    return base


def _page(records: list[dict[str, Any]], *, total: int | None = None, page: int = 1, locale: str = "fr") -> str:
    root = {
        "data": {
            f"jobs-page-init-/{locale}/tech-it/jobs": {"jobPostingCount": 8485},
            f"jobs-search-/{locale}/tech-it/jobs": {
                "jobs": records,
                "totalItems": total if total is not None else len(records),
                "filters": {"query": "data engineer", "contracts": ["contractor"]},
                "page": page,
            },
        },
        "state": {},
        "serverRendered": True,
    }
    payload = json.dumps(_devalue(root), ensure_ascii=False)
    return (
        "<!DOCTYPE html><html><head><title>Missions freelance IT | Free-Work</title></head><body>"
        '<div id="__nuxt"><a href="/fr/tech-it/job-mission/data-engineer/data-engineer-1">Data Engineer</a></div>'
        f'<script type="application/json" id="__NUXT_DATA__" data-ssr="true">{payload}</script>'
        "</body></html>"
    )


class DevalueTest(unittest.TestCase):
    def test_hydrates_objects_arrays_wrappers_and_sentinels(self) -> None:
        raw = [
            ["ShallowReactive", 1],
            {"data": 2, "when": 6, "missing": -1, "flag": 7},
            ["Reactive", 3],
            {"jobs": 4},
            [5, 5],
            {"id": 8, "title": 9},
            ["Date", "2026-09-04T10:15:00+02:00"],
            True,
            663488,
            "Data Engineer",
        ]
        tree = hydrate_devalue(raw)
        self.assertEqual(tree["when"], "2026-09-04T10:15:00+02:00")
        self.assertIsNone(tree["missing"])
        self.assertTrue(tree["flag"])
        self.assertEqual(tree["data"]["jobs"][0]["id"], 663488)
        self.assertIs(tree["data"]["jobs"][0], tree["data"]["jobs"][1])

    def test_unknown_tags_and_bad_indices_hydrate_to_none(self) -> None:
        self.assertIsNone(hydrate_devalue([]))
        self.assertIsNone(hydrate_devalue([["Whatever", 1], "x"]))
        self.assertEqual(hydrate_devalue([{"a": 99}]), {"a": None})
        self.assertEqual(hydrate_devalue([["Set", 1, 2], "a", "b"]), ["a", "b"])
        self.assertEqual(hydrate_devalue([["Map", 1, 2], "k", "v"]), {"k": "v"})
        self.assertEqual(hydrate_devalue([["EmptyRef", "\"0\""]]), "0")


class FreeWorkParseTest(unittest.TestCase):
    def test_search_page_gives_records_total_and_page(self) -> None:
        parsed = parse_search_page(_page([_record(1, "Data Engineer"), _record(2, "Lead Data Engineer")], total=177, page=2))
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["total"], 177)
        self.assertEqual(parsed["page"], 2)
        self.assertEqual([row["id"] for row in parsed["jobs"]], [1, 2])
        self.assertEqual(parsed["jobs"][0]["company"]["name"], "Neosoft")

    def test_page_without_payload_is_none(self) -> None:
        self.assertIsNone(parse_search_page("<html><body>Just a moment...</body></html>"))
        self.assertIsNone(parse_search_page('<script id="__NUXT_DATA__">not json</script>'))
        self.assertIsNone(parse_search_page(""))

    def test_job_row_is_a_freework_public_opportunity(self) -> None:
        record = parse_search_page(_page([_record(667124, "Data Engineer")]))["jobs"][0]  # type: ignore[index]
        job = to_job(record, country="FR", track="freelance")
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job["source"], "free-work")
        self.assertEqual(job["ingest"], "freework_public")
        self.assertEqual(job["url"], "https://www.free-work.com/fr/tech-it/job-mission/data-engineer/data-engineer-667124")
        self.assertEqual(job["country"], "FR")
        self.assertEqual(job["track"], "freelance")
        self.assertEqual(job["company"], "Neosoft")
        self.assertEqual(job["location"], "Paris, France")
        self.assertEqual(job["compensation"], 350.0)
        self.assertEqual(job["currency"], "EUR")
        self.assertEqual((job["daily_rate_min"], job["daily_rate_max"]), (330.0, 350.0))
        self.assertEqual((job["salary_min"], job["salary_max"]), (40000.0, 45000.0))
        self.assertEqual(job["contracts"], ["contractor"])
        self.assertEqual(job["duration"], "6 months")
        self.assertEqual(job["duration_months"], 6)
        self.assertEqual(job["remote"], "hybrid")
        self.assertEqual(job["posted_at"], "2026-09-04")
        self.assertEqual(job["stack"], ["Python", "Spark"])
        self.assertEqual(job["employment_type"], "contractor")
        self.assertEqual(job["freework_id"], "667124")
        self.assertIn("Mission Data pour une ESN", job["description"])
        self.assertIn("- Spark", job["description"])
        self.assertNotIn("<", job["description"])
        self.assertTrue(job["id"].startswith("job-"))

    def test_jobs_track_reads_the_annual_salary_and_permanent_contract(self) -> None:
        record = _record(1, "Data Engineer", contracts=["permanent", "fixed-term"], remoteMode="full")
        job = to_job(record, country="FR", track="jobs")
        assert job is not None
        self.assertEqual(job["track"], "jobs")
        self.assertEqual(job["compensation"], 45000.0)
        self.assertEqual(job["remote"], "remote")
        self.assertEqual(job["employment_type"], "permanent, fixed-term")
        self.assertEqual(job["contracts"], ["permanent", "fixed-term"])
        self.assertEqual(job["daily_rate_max"], 350.0)

    def test_mixed_contracts_follow_the_wanted_track(self) -> None:
        record = _record(1, "Data Engineer", contracts=["contractor", "permanent"])
        self.assertEqual((to_job(record, country="FR", track="jobs") or {})["track"], "jobs")
        self.assertEqual((to_job(record, country="FR", track="freelance") or {})["track"], "freelance")
        record = _record(2, "Data Engineer", contracts=["contractor"])
        self.assertEqual((to_job(record, country="FR", track="jobs") or {})["track"], "freelance")

    def test_gb_market_uses_the_english_locale_and_pounds(self) -> None:
        record = _record(1, "Data Engineer", dailySalary="\u00a3350-450", annualSalary=None, location={"label": "London, England"})
        job = to_job(record, country="GB", track="freelance")
        assert job is not None
        self.assertEqual(job["country"], "GB")
        self.assertTrue(job["url"].startswith("https://www.free-work.com/en-gb/tech-it/job-mission/"))
        self.assertEqual(job["compensation"], 450.0)
        self.assertEqual(job["currency"], "GBP")
        self.assertIsNone(job["salary_max"])
        self.assertEqual(job["daily_rate_min"], 350.0)

    def test_missing_pay_and_remote_stay_honest(self) -> None:
        record = _record(1, "Data Engineer", dailySalary=None, annualSalary=None)
        record.pop("remoteMode")
        record.pop("job")
        job = to_job(record, country="FR", track="freelance")
        assert job is not None
        self.assertIsNone(job["compensation"])
        self.assertIsNone(job["daily_rate_max"])
        self.assertEqual(job["currency"], "EUR")
        self.assertEqual(job["remote"], "")
        self.assertIn("query=data-engineer-1", job["url"])

    def test_rows_without_title_or_slug_are_dropped(self) -> None:
        self.assertIsNone(to_job(_record(1, ""), country="FR", track="freelance"))
        self.assertIsNone(to_job(_record(1, "Data Engineer", slug=""), country="FR", track="freelance"))

    def test_money_and_duration_helpers(self) -> None:
        self.assertEqual(parse_money("330-350 \u20ac"), (350.0, "EUR"))
        self.assertEqual(parse_money("40k-45k \u20ac"), (45000.0, "EUR"))
        self.assertEqual(parse_money("\u00a3350-450"), (450.0, "GBP"))
        self.assertEqual(parse_money("1 200 \u20ac"), (1200.0, "EUR"))
        self.assertEqual(parse_money("45 000 EUR"), (45000.0, "EUR"))
        self.assertEqual(parse_money("$700"), (700.0, "USD"))
        self.assertEqual(parse_money("800 CHF"), (800.0, "CHF"))
        self.assertEqual(parse_money(""), (None, ""))
        self.assertEqual(parse_money("selon profil"), (None, ""))
        self.assertEqual(duration_months(6, "month"), 6)
        self.assertEqual(duration_months(1, "year"), 12)
        self.assertEqual(duration_months(285, "day"), 14)
        self.assertEqual(duration_months(None, "month"), 0)
        self.assertEqual(duration_months(3, "eon"), 0)
        self.assertEqual(duration_label(1, "year"), "1 year")
        self.assertEqual(duration_label(4, "month"), "4 months")
        self.assertEqual(duration_label(0, "month"), "")

    def test_search_url_filters_by_locale_contract_and_page(self) -> None:
        url = search_url("Data Engineer", "FR", track="freelance", page=2)
        parts = urlparse(url)
        self.assertEqual(parts.path, "/fr/tech-it/jobs")
        query = parse_qs(parts.query)
        self.assertEqual(query["query"], ["Data Engineer"])
        self.assertEqual(query["contracts"], ["contractor"])
        self.assertEqual(query["page"], ["2"])
        url = search_url("Data Engineer", "GB", track="jobs")
        parts = urlparse(url)
        self.assertEqual(parts.path, "/en-gb/tech-it/jobs")
        query = parse_qs(parts.query)
        self.assertEqual(query["contracts"], ["permanent"])
        self.assertNotIn("page", query)
        query = parse_qs(urlparse(search_url("Data Engineer", "FR", track="both")).query)
        self.assertNotIn("contracts", query)

    def test_supported_markets_keep_profile_order_without_duplicates(self) -> None:
        self.assertEqual(supported_markets(["US", "gb", "FR", "GB", "AE"]), ["GB", "FR"])
        self.assertEqual(supported_markets(["US", "AE"]), [])


class FreeWorkSearchTest(unittest.TestCase):
    def test_search_pages_dedupes_and_stays_within_budget(self) -> None:
        calls: list[str] = []

        def fake_get(url: str) -> str:
            calls.append(url)
            page = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
            if page == 1:
                return _page([_record(1000 + i, "Data Engineer") for i in range(PAGE_SIZE)], total=PAGE_SIZE + 2)
            return _page([_record(1000, "Data Engineer"), _record(2000, "Senior Data Engineer")], total=PAGE_SIZE + 2, page=2)

        out = search_freework_jobs(
            titles=["Data Engineer"],
            countries=["FR", "US"],
            track="freelance",
            http_get=fake_get,
            max_requests=10,
            max_pages=2,
            sleep=lambda _s: None,
        )
        self.assertEqual(len(out["jobs"]), PAGE_SIZE + 1)
        self.assertEqual(out["walls"], [])
        self.assertEqual(out["requests"], 2)
        self.assertEqual(out["pairs"], 1)
        self.assertEqual(out["markets"], ["FR"])
        self.assertEqual(out["total"], PAGE_SIZE + 2)
        self.assertTrue(all("/fr/tech-it/jobs" in url for url in calls))
        self.assertTrue(all(job["description"] for job in out["jobs"]))

    def test_short_first_page_stops_paging(self) -> None:
        calls: list[str] = []

        def fake_get(url: str) -> str:
            calls.append(url)
            return _page([_record(1, "Data Engineer")], total=1)

        out = search_freework_jobs(
            titles=["Data Engineer", "Data Architect"],
            countries=["FR"],
            http_get=fake_get,
            max_pages=3,
            sleep=lambda _s: None,
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(out["requests"], 2)
        self.assertEqual(len(out["jobs"]), 1)

    def test_unsupported_markets_cost_no_request(self) -> None:
        def never(url: str) -> str:
            raise AssertionError(f"unexpected request {url}")

        out = search_freework_jobs(titles=["Data Engineer"], countries=["US", "AE"], http_get=never, sleep=lambda _s: None)
        self.assertEqual(out, {"jobs": [], "requests": 0, "walls": [], "pairs": 0, "markets": [], "total": 0})

    def test_wall_stops_the_run_and_is_reported(self) -> None:
        def blocked(url: str) -> str:
            raise FreeWorkWallError("HTTP 429")

        out = search_freework_jobs(
            titles=["Data Engineer", "Data Architect"],
            countries=["FR", "GB"],
            http_get=blocked,
            sleep=lambda _s: None,
        )
        self.assertEqual(out["jobs"], [])
        self.assertEqual(out["requests"], 1)
        self.assertEqual(len(out["walls"]), 1)
        self.assertIn("429", out["walls"][0])

    def test_layout_change_is_reported_as_a_wall(self) -> None:
        out = search_freework_jobs(
            titles=["Data Engineer"],
            countries=["FR"],
            http_get=lambda _url: "<html><body>new layout</body></html>",
            sleep=lambda _s: None,
        )
        self.assertEqual(out["jobs"], [])
        self.assertEqual(len(out["walls"]), 1)
        self.assertIn("payload missing", out["walls"][0])

    def test_budget_caps_requests_across_titles_and_markets(self) -> None:
        def always_full(url: str) -> str:
            page = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
            locale = "en-gb" if "/en-gb/" in url else "fr"
            return _page([_record(page * 10_000 + i, "Data Engineer") for i in range(PAGE_SIZE)], total=500, locale=locale)

        out = search_freework_jobs(
            titles=["Data Engineer", "Data Architect", "ML Engineer"],
            countries=["FR", "GB"],
            http_get=always_full,
            max_requests=5,
            max_pages=2,
            sleep=lambda _s: None,
        )
        self.assertEqual(out["requests"], 5)
        self.assertTrue(out["jobs"])
        self.assertEqual(out["markets"], ["FR", "GB"])


if __name__ == "__main__":
    unittest.main()
