# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Public API / RSS feed engine: parsers per source, budget, cache, attribution."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.career.feeds import (
    FEED_IDS,
    FeedCache,
    FeedWallError,
    collect_feeds,
    hn_story_id,
    iso_day,
    jobicy_url,
    parse_arbeitnow,
    parse_himalayas,
    parse_hn_hiring,
    parse_jobicy,
    parse_remoteok,
    parse_weworkremotely,
    plan_jobicy,
    title_matches,
)

JOBICY = {
    "jobs": [
        {
            "id": 152274,
            "url": "https://jobicy.com/jobs/152274-aws-data-engineer",
            "jobTitle": "AWS Data Engineer (Senior) (Freelancer)",
            "companyName": "Mactores",
            "jobIndustry": ["Data Science", "Software Engineering"],
            "jobType": ["contract"],
            "jobGeo": "France",
            "jobLevel": "Senior",
            "jobExcerpt": "Build pipelines.",
            "jobDescription": "<p>Build <b>Spark</b> pipelines on AWS.</p>",
            "pubDate": "2026-09-01T10:00:00+00:00",
            "salaryMin": 500,
            "salaryMax": 650,
            "salaryCurrency": "EUR",
            "salaryPeriod": "daily",
        },
        {
            "id": 1,
            "url": "https://jobicy.com/jobs/1-data-engineer",
            "jobTitle": "Data Engineer",
            "companyName": "Ruby Labs",
            "jobType": ["full-time"],
            "jobGeo": "Europe,  USA",
            "jobLevel": "Any",
            "jobDescription": "<p>Marketing systems.</p>",
            "pubDate": "2026-08-12T09:00:00+00:00",
            "salaryMin": 60000,
            "salaryMax": 80000,
            "salaryCurrency": "USD",
            "salaryPeriod": "yearly",
        },
        {
            "id": 2,
            "url": "https://jobicy.com/jobs/2-intern",
            "jobTitle": "Data Engineer (Intern)",
            "companyName": "Mactores",
            "jobType": ["internship"],
            "jobGeo": "Anywhere",
            "jobLevel": "Any",
            "pubDate": "2026-09-01T10:00:00+00:00",
        },
        {"id": 3, "jobTitle": "", "url": "https://jobicy.com/jobs/3"},
    ]
}

REMOTEOK = [
    {"legal": "API Terms of Service: Please link back to the job posting."},
    {
        "id": "1137224",
        "slug": "remote-ai-engineer-benzinga",
        "date": "2026-08-30T00:00:00+00:00",
        "company": "Benzinga",
        "position": "AI Engineer Data APIs",
        "tags": ["python", "ai", "contract"],
        "location": "United States",
        "salary_min": "90000",
        "salary_max": "150000",
        "description": "<p>Build data APIs.</p>",
        "url": "https://remoteOK.com/remote-jobs/remote-ai-engineer-benzinga-1137224",
    },
    {
        "id": "2",
        "date": "2026-08-30T00:00:00+00:00",
        "company": "Other",
        "position": "Social Media Manager",
        "tags": ["marketing"],
        "location": "",
        "url": "https://remoteok.com/remote-jobs/2",
    },
]

HIMALAYAS = {
    "jobs": [
        {
            "title": "Senior Data Engineer",
            "companyName": "Webflow",
            "employmentType": "Full Time",
            "minSalary": "143000",
            "maxSalary": "210000",
            "salaryPeriod": "annual",
            "seniority": ["Senior"],
            "currency": "USD",
            "locationRestrictions": ["United States"],
            "categories": ["Data-Engineer"],
            "description": "<p>Own the warehouse.</p>",
            "pubDate": "1789022623",
            "applicationLink": "https://himalayas.app/companies/webflow/jobs/senior-data-engineer",
        },
        {
            "title": "MEP Engineer",
            "companyName": "mercor",
            "employmentType": "Contract",
            "minSalary": "90",
            "maxSalary": "140",
            "salaryPeriod": "hourly",
            "currency": "USD",
            "locationRestrictions": ["Singapore", "Malaysia"],
            "categories": [],
            "pubDate": "1789022623",
            "applicationLink": "https://himalayas.app/companies/mercor/jobs/mep",
        },
    ]
}

WWR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>WWR</title>
<item>
  <title>A.Team: Senior Independent AI Engineer / Architect</title>
  <region>Anywhere in the World</region>
  <country></country>
  <skills>Python, LLM</skills>
  <category>Programming</category>
  <type>Contract</type>
  <description>&lt;p&gt;Headquarters: USA. Rate: $90 - $120 per hour.&lt;/p&gt;</description>
  <link>https://weworkremotely.com/remote-jobs/a-team-senior-independent-ai-engineer-architect</link>
  <pubDate>Sun, 16 Jun 2024 20:42:08 +0000</pubDate>
</item>
<item>
  <title>Wrike: Account Executive</title>
  <region>USA Only</region>
  <country>United States</country>
  <category>Sales and Marketing</category>
  <type>Full-Time</type>
  <description>Sales.</description>
  <link>https://weworkremotely.com/remote-jobs/wrike-ae</link>
  <pubDate>Mon, 08 Sep 2026 10:00:00 +0000</pubDate>
</item>
</channel></rss>"""

ARBEITNOW = {
    "data": [
        {
            "slug": "senior-data-engineer-london",
            "company_name": "LiveScore Group",
            "title": "Senior Data Engineer",
            "description": "<p>Snowflake and dbt.</p>",
            "remote": False,
            "url": "https://www.arbeitnow.com/jobs/companies/livescore-group/senior-data-engineer-london-496047",
            "tags": ["Engineering"],
            "job_types": ["Full time"],
            "location": "London, England, United Kingdom",
            "created_at": 1789030870,
        },
        {
            "slug": "servicetechniker",
            "company_name": "Remmert GmbH",
            "title": "Servicetechniker im Aussendienst",
            "description": "<p>Lager.</p>",
            "remote": False,
            "url": "https://www.arbeitnow.com/jobs/companies/remmert/servicetechniker",
            "tags": [],
            "job_types": [],
            "location": "Braunschweig",
            "created_at": 1789030870,
        },
    ],
    "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=2"},
}

HN_STORY = {"hits": [{"objectID": "49522897", "title": "Ask HN: Who is hiring? (September 2026)"}]}
HN_COMMENTS = {
    "hits": [
        {
            "objectID": "49613617",
            "parent_id": 49522897,
            "created_at": "2026-09-08T18:00:00Z",
            "comment_text": "Matterhaul | Founding Applied AI Engineer | San Francisco, CA | ONSITE | $200-260k<p>We build routing.",
        },
        {
            "objectID": "49615973",
            "parent_id": 49522897,
            "created_at": "2026-09-08T19:00:00Z",
            "comment_text": "Close | Senior Data Engineer | REMOTE (Europe) | Full-time | 6 month contract to start<p>Python, dbt.",
        },
        {
            "objectID": "49639128",
            "parent_id": 49613617,
            "created_at": "2026-09-10T06:14:15Z",
            "comment_text": "Is this open to Data Engineer profiles from Canada?",
        },
    ]
}


class FeedHelpersTest(unittest.TestCase):
    def test_title_matches_needs_every_meaningful_token(self) -> None:
        self.assertTrue(title_matches("Senior Data Platform Engineer", ["Data Engineer"]))
        self.assertTrue(title_matches("AI Engineer Data APIs", ["AI Engineer"]))
        self.assertFalse(title_matches("Analytics Engineer", ["Data Engineer"]))
        self.assertFalse(title_matches("Maintainer", ["AI"]))
        self.assertTrue(title_matches("Anything", []))

    def test_iso_day_reads_iso_epoch_and_rfc2822(self) -> None:
        self.assertEqual(iso_day("2026-09-01T10:00:00+00:00"), "2026-09-01")
        self.assertEqual(iso_day(1789022623), "2026-09-10")
        self.assertEqual(iso_day("1789022623"), "2026-09-10")
        self.assertEqual(iso_day("Mon, 08 Sep 2026 10:00:00 +0000"), "2026-09-08")
        self.assertEqual(iso_day(""), "")

    def test_jobicy_plan_covers_markets_and_titles_within_budget(self) -> None:
        plan = plan_jobicy(["Data Engineer", "AI Engineer", "LLM", "RAG", "Machine Learning"], ["FR", "GB", "US"])
        urls = [url for url, _market in plan]
        self.assertEqual(len(plan), 6)  # 3 tags x 2 geos
        self.assertTrue(all("geo=france" in url or "geo=uk" in url for url in urls))
        self.assertIn(("https://jobicy.com/api/v2/remote-jobs?count=100&geo=france&tag=Data+Engineer", "FR"), plan)
        self.assertEqual(jobicy_url(tag="ai", geo="usa"), "https://jobicy.com/api/v2/remote-jobs?count=100&geo=usa")
        self.assertEqual(plan_jobicy([], [])[0][0], "https://jobicy.com/api/v2/remote-jobs?count=100&geo=anywhere")


class FeedParsersTest(unittest.TestCase):
    def test_jobicy_rows_carry_pay_contracts_level_and_market_currency(self) -> None:
        rows = parse_jobicy(json.dumps(JOBICY), market="FR", track="freelance")
        self.assertEqual([row["title"] for row in rows], ["AWS Data Engineer (Senior) (Freelancer)", "Data Engineer", "Data Engineer (Intern)"])
        senior = rows[0]
        self.assertEqual(senior["source"], "jobicy")
        self.assertEqual(senior["ingest"], "public_api")
        self.assertEqual(senior["attribution"], "Jobicy")
        self.assertEqual(senior["country"], "FR")
        self.assertEqual(senior["contracts"], ["contractor"])
        self.assertEqual(senior["track"], "freelance")
        self.assertEqual((senior["daily_rate_min"], senior["daily_rate_max"]), (500.0, 650.0))
        self.assertEqual(senior["compensation"], 650.0)
        self.assertEqual(senior["currency"], "EUR")
        self.assertEqual(senior["experience_level"], "senior")
        self.assertEqual(senior["remote"], "remote")
        self.assertEqual(senior["posted_at"], "2026-09-01")
        self.assertEqual(senior["stack"], ["Data Science", "Software Engineering"])
        self.assertNotIn("<", senior["description"])
        self.assertEqual(senior["url"], "https://jobicy.com/jobs/152274-aws-data-engineer")
        europe = rows[1]
        self.assertEqual(europe["country"], "FR")  # eligible in the searched market
        self.assertEqual((europe["salary_min"], europe["salary_max"]), (60000.0, 80000.0))
        self.assertEqual(europe["currency"], "USD")
        self.assertEqual(europe["contracts"], ["permanent"])
        self.assertEqual(europe["track"], "jobs")
        self.assertIsNone(europe["compensation"])  # yearly pay does not pose as a day rate
        intern = rows[2]
        self.assertEqual(intern["country"], "REMOTE")
        self.assertEqual(intern["currency"], "EUR")  # searched market currency when nothing is posted
        self.assertEqual(intern["experience_level"], "junior")

    def test_remoteok_skips_the_legal_notice_filters_titles_and_credits_the_source(self) -> None:
        rows = parse_remoteok(json.dumps(REMOTEOK), titles=["AI Engineer"], track="freelance")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "remoteok")
        self.assertIn("Remote OK", row["attribution"])
        self.assertEqual(row["url"], "https://remoteOK.com/remote-jobs/remote-ai-engineer-benzinga-1137224")
        self.assertEqual(row["country"], "US")
        self.assertEqual((row["salary_min"], row["salary_max"]), (90000.0, 150000.0))
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(row["contracts"], ["contractor"])
        self.assertEqual(row["posted_at"], "2026-08-30")
        self.assertEqual(len(parse_remoteok(json.dumps(REMOTEOK), titles=[])), 2)

    def test_himalayas_rows_convert_hourly_pay_to_day_and_year(self) -> None:
        rows = parse_himalayas(json.dumps(HIMALAYAS), titles=["Data Engineer", "MEP Engineer"], track="freelance")
        self.assertEqual(len(rows), 2)
        senior, mep = rows
        self.assertEqual(senior["country"], "US")
        self.assertEqual(senior["contracts"], ["permanent"])
        self.assertEqual((senior["salary_min"], senior["salary_max"]), (143000.0, 210000.0))
        self.assertEqual(senior["experience_level"], "senior")
        self.assertEqual(senior["stack"], ["Data Engineer"])
        self.assertEqual(mep["country"], "REMOTE")
        self.assertEqual(mep["location"], "Singapore, Malaysia")
        self.assertEqual((mep["daily_rate_min"], mep["daily_rate_max"]), (720.0, 1120.0))
        self.assertEqual(mep["salary_max"], 140 * 8 * 216)
        self.assertEqual(mep["compensation"], 1120.0)
        self.assertEqual(mep["contracts"], ["contractor"])

    def test_weworkremotely_rss_splits_company_and_reads_pay_from_the_body(self) -> None:
        rows = parse_weworkremotely(WWR, titles=["AI Engineer"], track="freelance")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["company"], "A.Team")
        self.assertEqual(row["title"], "Senior Independent AI Engineer / Architect")
        self.assertEqual(row["ingest"], "public_rss")
        self.assertEqual(row["contracts"], ["contractor"])
        self.assertEqual(row["stack"], ["Python", "LLM"])
        self.assertEqual(row["posted_at"], "2024-06-16")
        self.assertEqual(row["currency"], "USD")
        self.assertEqual((row["daily_rate_min"], row["daily_rate_max"]), (720.0, 960.0))
        self.assertEqual(row["country"], "REMOTE")
        everyone = parse_weworkremotely(WWR, titles=[], track="jobs")
        self.assertEqual(everyone[1]["country"], "US")
        self.assertEqual(everyone[1]["contracts"], ["permanent"])

    def test_arbeitnow_rows_infer_the_country_and_keep_the_link_back(self) -> None:
        rows = parse_arbeitnow(json.dumps(ARBEITNOW), titles=["Data Engineer"], track="jobs")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["country"], "GB")
        self.assertEqual(row["currency"], "GBP")
        self.assertEqual(row["contracts"], ["permanent"])
        self.assertEqual(row["experience_level"], "senior")
        self.assertEqual(row["attribution"], "Arbeitnow")
        self.assertEqual(row["posted_at"], "2026-09-10")
        self.assertEqual(parse_arbeitnow(json.dumps(ARBEITNOW), titles=[], track="jobs")[1]["country"], "DE")

    def test_hn_hiring_reads_the_header_line_and_skips_replies(self) -> None:
        self.assertEqual(hn_story_id(json.dumps(HN_STORY)), "49522897")
        rows = parse_hn_hiring(json.dumps(HN_COMMENTS), titles=["AI Engineer", "Data Engineer"], track="freelance", story="49522897")
        self.assertEqual(len(rows), 2)
        founding, close = rows
        self.assertEqual(founding["company"], "Matterhaul")
        self.assertEqual(founding["title"], "Founding Applied AI Engineer")
        self.assertEqual(founding["location"], "San Francisco, CA")
        self.assertEqual(founding["country"], "US")
        self.assertEqual(founding["remote"], "onsite")
        self.assertEqual((founding["salary_min"], founding["salary_max"]), (200000.0, 260000.0))
        self.assertEqual(founding["currency"], "USD")
        self.assertEqual(founding["url"], "https://news.ycombinator.com/item?id=49613617")
        self.assertEqual(close["remote"], "remote")
        self.assertEqual(sorted(close["contracts"]), ["contractor", "permanent"])
        self.assertEqual(close["duration_months"], 6)
        self.assertEqual(close["posted_at"], "2026-09-08")


class FeedEngineTest(unittest.TestCase):
    def _fake_http(self, log: list[str], *, wall: set[str] | None = None):
        def http_get(url: str) -> str:
            log.append(url)
            if wall and any(token in url for token in wall):
                raise FeedWallError("HTTP 429")
            if "jobicy.com" in url:
                return json.dumps(JOBICY)
            if "remoteok.com" in url:
                return json.dumps(REMOTEOK)
            if "himalayas.app" in url:
                return json.dumps(HIMALAYAS)
            if "weworkremotely.com" in url:
                return WWR
            if "arbeitnow.com" in url:
                return json.dumps(ARBEITNOW)
            if "tags=story" in url:
                return json.dumps(HN_STORY)
            if "tags=comment" in url:
                return json.dumps(HN_COMMENTS)
            raise AssertionError(url)

        return http_get

    def test_every_feed_runs_once_dedupes_and_reports_per_source(self) -> None:
        log: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            cache = FeedCache(Path(tmp) / "feeds.json")
            out = collect_feeds(
                titles=["Data Engineer", "AI Engineer"],
                countries=["FR", "GB"],
                track="freelance",
                http_get=self._fake_http(log),
                cache=cache,
            )
            self.assertEqual(sorted(out["sources"]), sorted(FEED_IDS))
            self.assertEqual(out["walls"], [])
            # 4 Jobicy (2 tags x 2 geos) + 1 + 1 + 2 WWR + 1 + 1 story + 2 comment pages
            self.assertEqual(out["requests"], 12)
            self.assertEqual(out["requests"], len(log))
            by_source = out["by_source"]
            self.assertEqual(by_source["jobicy"], 3)
            self.assertEqual(by_source["remoteok"], 1)
            self.assertEqual(by_source["himalayas"], 1)
            self.assertEqual(by_source["weworkremotely"], 1)
            self.assertEqual(by_source["arbeitnow"], 1)
            self.assertEqual(by_source["hn-hiring"], 2)
            urls = [row["url"] for row in out["jobs"]]
            self.assertEqual(len(urls), len(set(urls)))
            self.assertTrue(all(row["currency"] for row in out["jobs"] if row["compensation"] is not None))
            # Second run: everything comes out of the one hour cache, no request spent.
            again = collect_feeds(
                titles=["Data Engineer", "AI Engineer"],
                countries=["FR", "GB"],
                track="freelance",
                http_get=self._fake_http(log),
                cache=cache,
            )
            self.assertEqual(again["requests"], 0)
            self.assertEqual(len(again["jobs"]), len(out["jobs"]))

    def test_sources_subset_and_wall_reporting(self) -> None:
        log: list[str] = []
        out = collect_feeds(
            titles=["Data Engineer"],
            countries=["FR"],
            track="jobs",
            sources=["remoteok", "arbeitnow"],
            http_get=self._fake_http(log, wall={"remoteok.com"}),
            cache=FeedCache(None),
        )
        self.assertEqual(out["sources"], ["remoteok", "arbeitnow"])
        self.assertEqual(out["walls"], ["Remote OK: HTTP 429"])
        self.assertEqual(out["by_source"], {"remoteok": 0, "arbeitnow": 1})
        self.assertEqual(out["requests"], 2)

    def test_request_budget_caps_the_run(self) -> None:
        log: list[str] = []
        out = collect_feeds(
            titles=["Data Engineer", "AI Engineer", "LLM"],
            countries=["FR", "GB"],
            track="freelance",
            http_get=self._fake_http(log),
            cache=FeedCache(None),
            max_requests=3,
        )
        self.assertEqual(out["requests"], 3)
        self.assertTrue(any("budget" in wall for wall in out["walls"]))

    def test_cache_expires_after_the_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = FeedCache(Path(tmp) / "feeds.json", ttl_s=10.0)
            cache.put("https://example.test/feed", "body", now=1000.0)
            self.assertEqual(cache.get("https://example.test/feed", now=1005.0), "body")
            self.assertIsNone(cache.get("https://example.test/feed", now=1011.0))
            fresh = FeedCache(Path(tmp) / "feeds.json", ttl_s=10.0)
            self.assertEqual(fresh.get("https://example.test/feed", now=1005.0), "body")


if __name__ == "__main__":
    unittest.main()
