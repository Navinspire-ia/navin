# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""LinkedIn public listing reader: parsing, budget, walls. No network."""

from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from navin.career.linkedin import (
    DETAIL_URL,
    PAGE_SIZE,
    LinkedInWallError,
    parse_cards,
    parse_detail,
    search_linkedin_jobs,
    search_url,
    to_job,
)


def _card(job_id: str, title: str, company: str = "Neosoft", location: str = "Paris, France") -> str:
    return f"""
    <li>
      <div class="base-card relative w-full hover:no-underline focus:no-underline base-card--link base-search-card base-search-card--link job-search-card" data-entity-urn="urn:li:jobPosting:{job_id}">
        <a class="base-card__full-link absolute top-0 right-0 bottom-0 left-0 p-0 z-[2]" href="https://fr.linkedin.com/jobs/view/{title.lower().replace(' ', '-')}-at-{company.lower()}-{job_id}?refId=abc&amp;trackingId=xyz">
          <span class="sr-only">{title}</span>
        </a>
        <div class="base-search-card__info">
          <h3 class="base-search-card__title">
            {title}
          </h3>
          <h4 class="base-search-card__subtitle">
            <a class="hidden-nested-link" href="https://fr.linkedin.com/company/{company.lower()}">
              {company}
            </a>
          </h4>
          <div class="base-search-card__metadata">
            <span class="job-search-card__location">
              {location}
            </span>
            <time class="job-search-card__listdate" datetime="2026-08-31">3 days ago</time>
          </div>
        </div>
      </div>
    </li>
    """


def _page(*cards: str) -> str:
    return "<ul>" + "".join(cards) + "</ul>"


_DETAIL = """
<section class="show-more-less-html">
  <div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5 relative overflow-hidden">
    <p><strong>Mission</strong> Data Engineer pour une ESN.</p>
    <ul><li>Spark</li><li>Azure</li></ul>
  </div>
</section>
<ul class="description__job-criteria-list">
  <li class="description__job-criteria-item">
    <h3 class="description__job-criteria-subheader">Seniority level</h3>
    <span class="description__job-criteria-text description__job-criteria-text--criteria">Mid-Senior level</span>
  </li>
  <li class="description__job-criteria-item">
    <h3 class="description__job-criteria-subheader">Employment type</h3>
    <span class="description__job-criteria-text description__job-criteria-text--criteria">Contract</span>
  </li>
</ul>
"""


class LinkedInParseTest(unittest.TestCase):
    def test_cards_carry_id_title_company_location_date_and_clean_url(self) -> None:
        cards = parse_cards(_page(_card("111", "Data Engineer"), _card("222", "Data Engineer IA - H/F", "Sia", "Lyon, France")))
        self.assertEqual([card["job_id"] for card in cards], ["111", "222"])
        self.assertEqual(cards[0]["title"], "Data Engineer")
        self.assertEqual(cards[0]["company"], "Neosoft")
        self.assertEqual(cards[0]["location"], "Paris, France")
        self.assertEqual(cards[0]["posted_at"], "2026-08-31")
        self.assertEqual(cards[0]["url"], "https://fr.linkedin.com/jobs/view/data-engineer-at-neosoft-111")
        self.assertEqual(cards[1]["company"], "Sia")

    def test_detail_gives_text_description_and_criteria(self) -> None:
        detail = parse_detail(_DETAIL)
        self.assertIn("Data Engineer pour une ESN", detail["description"])
        self.assertIn("- Spark", detail["description"])
        self.assertNotIn("<", detail["description"])
        self.assertEqual(detail["seniority"], "Mid-Senior level")
        self.assertEqual(detail["employment_type"], "Contract")

    def test_job_row_is_a_linkedin_public_opportunity(self) -> None:
        card = parse_cards(_page(_card("111", "Data Engineer")))[0]
        job = to_job(card, parse_detail(_DETAIL), country="FR", track="freelance")
        self.assertEqual(job["source"], "linkedin")
        self.assertEqual(job["ingest"], "linkedin_public")
        self.assertEqual(job["country"], "FR")
        self.assertEqual(job["track"], "freelance")
        self.assertEqual(job["seniority"], "Mid-Senior level")
        self.assertEqual(job["linkedin_job_id"], "111")
        self.assertTrue(job["id"])

    def test_search_url_filters_recent_and_contract(self) -> None:
        url = search_url("Data Engineer", "France", track="freelance", start=25)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["keywords"], ["Data Engineer"])
        self.assertEqual(query["location"], ["France"])
        self.assertEqual(query["f_TPR"], ["r604800"])
        self.assertEqual(query["f_JT"], ["C"])
        self.assertEqual(query["start"], ["25"])


class LinkedInSearchTest(unittest.TestCase):
    def test_search_pages_details_and_dedupes_within_budget(self) -> None:
        calls: list[str] = []

        def fake_get(url: str) -> str:
            calls.append(url)
            if "/jobPosting/" in url:
                return _DETAIL
            start = int(parse_qs(urlparse(url).query).get("start", ["0"])[0])
            if start == 0:
                return _page(*[_card(str(1000 + i), "Data Engineer") for i in range(PAGE_SIZE)])
            return _page(_card("1000", "Data Engineer"), _card("2000", "Senior Data Engineer", "Sia"))

        out = search_linkedin_jobs(
            titles=["Data Engineer"],
            countries=["FR"],
            track="freelance",
            http_get=fake_get,
            max_requests=10,
            max_pages=2,
            max_details=3,
            sleep=lambda _s: None,
        )
        self.assertEqual(len(out["jobs"]), PAGE_SIZE + 1)
        self.assertEqual(out["walls"], [])
        # 2 search pages + 3 details, never more than the budget.
        self.assertEqual(out["requests"], 5)
        self.assertEqual(sum(1 for url in calls if "/jobPosting/" in url), 3)
        self.assertTrue(all(url.startswith(DETAIL_URL.split("{")[0]) for url in calls if "/jobPosting/" in url))
        with_desc = [job for job in out["jobs"] if job["description"]]
        self.assertEqual(len(with_desc), 3)

    def test_wall_stops_the_run_and_is_reported(self) -> None:
        def blocked(url: str) -> str:
            raise LinkedInWallError("HTTP 429")

        out = search_linkedin_jobs(
            titles=["Data Engineer", "Data Architect"],
            countries=["FR", "BE"],
            http_get=blocked,
            sleep=lambda _s: None,
        )
        self.assertEqual(out["jobs"], [])
        self.assertEqual(out["requests"], 1)
        self.assertEqual(len(out["walls"]), 1)
        self.assertIn("429", out["walls"][0])

    def test_budget_caps_requests_across_titles_and_countries(self) -> None:
        def always_full(url: str) -> str:
            if "/jobPosting/" in url:
                return _DETAIL
            start = int(parse_qs(urlparse(url).query).get("start", ["0"])[0])
            return _page(*[_card(f"{start}{i:03d}", "Data Engineer") for i in range(PAGE_SIZE)])

        out = search_linkedin_jobs(
            titles=["Data Engineer", "Data Architect", "ML Engineer"],
            countries=["FR", "BE", "CH", "GB"],
            http_get=always_full,
            max_requests=6,
            max_pages=2,
            max_details=8,
            sleep=lambda _s: None,
        )
        self.assertEqual(out["requests"], 6)
        self.assertTrue(out["jobs"])


if __name__ == "__main__":
    unittest.main()
