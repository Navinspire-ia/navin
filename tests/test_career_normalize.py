# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared job vocabulary: pay ranges per currency, contracts, remote, experience,
JSON-LD JobPosting, Workable boards, the keyed Job Opportunities API and the
currency-aware match score."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from navin.career.collect import _normalize_ats
from navin.career.jsonld import extract_jobpostings, jobposting_facts, jobposting_rows
from navin.career.matching import score_opportunity
from navin.career.normalize import (
    contracts_track,
    convert_money,
    duration_from_text,
    enrich_facts,
    market_currency,
    normalize_contracts,
    normalize_experience,
    normalize_remote,
    parse_pay_range,
    pay_from_description,
    pay_from_numbers,
    pay_from_text,
)
from navin.career.official import fetch_jobopportunities

JOBPOSTING_HTML = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"WebPage","name":"x"},{"@type":"JobPosting",
"title":"Data Engineer Senior (H/F)","description":"<p>Mission freelance de 6 mois. 3 ans d'exp minimum.</p>",
"datePosted":"2026-09-01","validThrough":"2026-10-01","employmentType":["CONTRACTOR"],
"hiringOrganization":{"@type":"Organization","name":"Acme"},
"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Paris","addressCountry":"FR"}},
"jobLocationType":"TELECOMMUTE",
"baseSalary":{"@type":"MonetaryAmount","currency":"EUR","value":{"@type":"QuantitativeValue","minValue":500,"maxValue":600,"unitText":"DAY"}},
"experienceRequirements":{"@type":"OccupationalExperienceRequirements","monthsOfExperience":36},
"jobStartDate":"2026-10-01","url":"https://careers.acme.example/jobs/42"}]}
</script>
<script type="application/ld+json">
{"@type":"JobPosting","title":"Platform Engineer","hiringOrganization":{"name":"Beta"},
"employmentType":"FULL_TIME","jobLocation":[{"address":{"addressLocality":"London","addressCountry":"United Kingdom"}}],
"baseSalary":{"currency":"GBP","value":{"minValue":70000,"maxValue":90000,"unitText":"YEAR"}},
"description":"Kubernetes. Hybrid, 2 days on site.","url":"https://www.linkedin.com/jobs/view/1"}
</script>
</head></html>"""


class PayRangeTest(unittest.TestCase):
    def test_parse_pay_range_reads_amounts_currency_and_period(self) -> None:
        self.assertEqual(parse_pay_range("330-350 \u20ac"), {"min": 330.0, "max": 350.0, "currency": "EUR", "period": "day"})
        self.assertEqual(parse_pay_range("40k-45k \u20ac")["max"], 45000.0)
        self.assertEqual(parse_pay_range("\u00a3350-450")["currency"], "GBP")
        self.assertEqual(parse_pay_range("600 \u20ac/j")["period"], "day")
        self.assertEqual(parse_pay_range("45 000 \u20ac brut annuel")["period"], "year")
        self.assertEqual(parse_pay_range("$200-260k"), {"min": 200000.0, "max": 260000.0, "currency": "USD", "period": "year"})
        self.assertEqual(parse_pay_range("USD 70-80 per hour")["period"], "hour")
        self.assertEqual(parse_pay_range("3 ans d'exp\u00e9rience")["max"], None)
        self.assertEqual(parse_pay_range("800 CHF")["currency"], "CHF")
        self.assertEqual(parse_pay_range("1 500 AED / day")["currency"], "AED")

    def test_parse_pay_range_handles_thousands_separators_and_glued_numbers(self) -> None:
        # US thousands: "$105,000-$130,000" is a yearly salary, not a 105-130 day rate.
        self.assertEqual(parse_pay_range("$105,000 - $130,000"), {"min": 105000.0, "max": 130000.0, "currency": "USD", "period": "year"})
        self.assertEqual(parse_pay_range("45.000 \u20ac brut annuel")["min"], 45000.0)
        self.assertEqual(parse_pay_range("$1,000-1,500 per month"), {"min": 1000.0, "max": 1500.0, "currency": "USD", "period": "month"})
        self.assertEqual(parse_pay_range("1,5k \u20ac/j")["max"], 1500.0)
        # IR35, k8s, 40h/week and 0.75 FTE are not amounts.
        self.assertEqual(parse_pay_range("Up to \u00a3471 per day + Outside IR35")["min"], 471.0)
        self.assertEqual(parse_pay_range("Rate: $90 - $120 per hour, 40h/week")["min"], 90.0)
        self.assertEqual(parse_pay_range("k8s 600 \u20ac/j")["min"], 600.0)
        self.assertEqual(parse_pay_range("0.75 FTE 60k \u20ac")["min"], 60000.0)

    def test_pay_from_text_fills_day_or_year_side_and_the_score_figure(self) -> None:
        day = pay_from_text("400-600 \u20ac/j", country="FR", track="freelance")
        self.assertEqual((day["daily_rate_min"], day["daily_rate_max"]), (400.0, 600.0))
        self.assertIsNone(day["salary_max"])
        self.assertEqual(day["compensation"], 600.0)
        year = pay_from_text("40k-45k \u20ac/an", country="FR", track="jobs")
        self.assertEqual((year["salary_min"], year["salary_max"]), (40000.0, 45000.0))
        self.assertEqual(year["compensation"], 45000.0)
        hourly = pay_from_text("$90-120 per hour", country="US", track="freelance")
        self.assertEqual((hourly["daily_rate_min"], hourly["daily_rate_max"]), (720.0, 960.0))
        self.assertEqual(hourly["salary_max"], 120 * 8 * 216)

    def test_pay_from_numbers_uses_the_market_currency_when_none_is_posted(self) -> None:
        pay = pay_from_numbers(350, 450, country="GB", track="freelance")
        self.assertEqual(pay["currency"], "GBP")
        self.assertEqual(pay["daily_rate_max"], 450.0)
        pay = pay_from_numbers(60000, 80000, period="yearly", country="CH", track="jobs")
        self.assertEqual(pay["currency"], "CHF")
        self.assertEqual(pay["compensation"], 80000.0)
        pay = pay_from_numbers(4000, 5000, period="monthly", currency="EUR", track="jobs")
        self.assertEqual((pay["salary_min"], pay["salary_max"]), (48000.0, 60000.0))
        self.assertEqual(pay_from_numbers(None, None, country="FR")["compensation"], None)
        self.assertEqual(pay_from_numbers("abc", "", country="FR")["daily_rate_max"], None)

    def test_market_currency_and_indicative_conversion(self) -> None:
        self.assertEqual(market_currency("FR"), "EUR")
        self.assertEqual(market_currency("gb"), "GBP")
        self.assertEqual(market_currency("AE"), "AED")
        self.assertEqual(market_currency("REMOTE"), "")
        self.assertEqual(convert_money(100, "EUR", "EUR"), 100.0)
        self.assertAlmostEqual(convert_money(100, "GBP", "EUR") or 0, 117.0)
        self.assertIsNone(convert_money(100, "XXX", "EUR"))
        self.assertIsNone(convert_money(None, "USD", "EUR"))

    def test_pay_from_description_needs_a_pay_word_and_a_currency(self) -> None:
        text = "Great team.\nTJM : 500-550 \u20ac/j selon profil\n3 ans d'exp\u00e9rience"
        pay = pay_from_description(text, country="FR", track="freelance")
        assert pay is not None
        self.assertEqual(pay["daily_rate_max"], 550.0)
        self.assertIsNone(pay_from_description("We have 40 engineers and 3 offices.", country="FR"))
        self.assertIsNone(pay_from_description("Salary: competitive", country="FR"))

    def test_pay_from_description_skips_company_figures_and_implausible_amounts(self) -> None:
        # Funding, ARR and customer counts sit next to "compensation" in many pitches.
        self.assertIsNone(pay_from_description("Decent compensation? We raised $11M in funding and serve 2,000 customers.", country="US"))
        self.assertIsNone(pay_from_description("Founded in 2006 with $1B in payments. Compensation is competitive.", country="GB"))
        self.assertIsNone(pay_from_description("Salary from $30 to $100 depending on the day.", country="US"))
        # Only the words around the amount are read: IR35 is not a floor.
        posted = pay_from_description("Up to \u00a3471 per day + Outside IR35 (Harvey Nash Plc). Day Rate: \u00a3471.", country="GB", track="freelance")
        assert posted is not None
        self.assertEqual((posted["daily_rate_min"], posted["daily_rate_max"], posted["currency"]), (471.0, 471.0, "GBP"))
        header = pay_from_description("Foray | Software Engineer | On-Site | Full Time | Salary $105,000-$130,000 + equity", country="US", track="jobs")
        assert header is not None
        self.assertEqual((header["salary_min"], header["salary_max"], header["compensation"]), (105000.0, 130000.0, 130000.0))
        hourly = pay_from_description("Rate: $90 - $120 per hour, 40h/week", country="US", track="freelance")
        assert hourly is not None
        self.assertEqual((hourly["daily_rate_min"], hourly["daily_rate_max"]), (720.0, 960.0))
        # The range stops at the bracket: "(39h/semaine + 12 RTT/an)" is not a 12k floor.
        perks = pay_from_description(
            "R\u00e9mun\u00e9ration : entre 45 K\u20ac et 50 K\u20ac selon profil (39h/semaine + 12 RTT/ an) + t\u00e9l\u00e9travail",
            country="FR",
            track="jobs",
        )
        assert perks is not None
        self.assertEqual((perks["salary_min"], perks["salary_max"]), (45000.0, 50000.0))


class VocabularyTest(unittest.TestCase):
    def test_contracts_from_labels_codes_and_free_text(self) -> None:
        self.assertEqual(normalize_contracts(["Full-Time", "Contract"]), ["permanent", "contractor"])
        self.assertEqual(normalize_contracts("FULL_TIME"), ["permanent"])
        self.assertEqual(normalize_contracts("CDI / freelance"), ["contractor", "permanent"])
        self.assertEqual(normalize_contracts("Mission freelance 6 mois"), ["contractor"])
        self.assertEqual(normalize_contracts("CDD 12 mois"), ["fixed-term"])
        self.assertEqual(normalize_contracts("Alternance data"), ["apprenticeship"])
        self.assertEqual(normalize_contracts("Stage de fin d'\u00e9tudes"), ["internship"])
        self.assertEqual(normalize_contracts("Outside IR35 contract"), ["contractor"])
        self.assertEqual(normalize_contracts(None, "", []), [])

    def test_contracts_track(self) -> None:
        self.assertEqual(contracts_track(["contractor"], "jobs"), "freelance")
        self.assertEqual(contracts_track(["permanent"], "freelance"), "jobs")
        self.assertEqual(contracts_track(["contractor", "permanent"], "jobs"), "jobs")
        self.assertEqual(contracts_track([], "freelance"), "freelance")
        self.assertEqual(contracts_track([], ""), "jobs")

    def test_remote_modes(self) -> None:
        self.assertEqual(normalize_remote("T\u00e9l\u00e9travail partiel"), "hybrid")
        self.assertEqual(normalize_remote("Full remote"), "remote")
        self.assertEqual(normalize_remote(True), "remote")
        self.assertEqual(normalize_remote("Paris (2 jours de t\u00e9l\u00e9travail)"), "hybrid")
        self.assertEqual(normalize_remote("On-site"), "onsite")
        self.assertEqual(normalize_remote("Anywhere in the World"), "remote")
        self.assertEqual(normalize_remote(None, "", "Paris"), "")

    def test_experience_levels_and_years(self) -> None:
        self.assertEqual(normalize_experience("Senior"), ("senior", None))
        self.assertEqual(normalize_experience("Mid-Senior level"), ("senior", None))
        self.assertEqual(normalize_experience("5+ years of experience"), ("mid", 5))
        self.assertEqual(normalize_experience(["Entry"]), ("junior", None))
        self.assertEqual(normalize_experience("Staff Engineer"), ("expert", None))
        self.assertEqual(normalize_experience("3 \u00e0 5 ans"), ("mid", 3))
        self.assertEqual(normalize_experience("12 years leading teams"), ("expert", 12))
        self.assertEqual(normalize_experience(""), ("", None))

    def test_duration_from_text(self) -> None:
        self.assertEqual(duration_from_text("Mission de 6 mois renouvelable"), (6, "6 months"))
        self.assertEqual(duration_from_text("12 months contract"), (12, "12 months"))
        self.assertEqual(duration_from_text("1 an"), (12, "1 year"))
        self.assertEqual(duration_from_text("No dates here"), (0, ""))

    def test_enrich_facts_only_fills_what_is_missing(self) -> None:
        row = {
            "title": "Senior Data Engineer freelance",
            "description": "Mission de 6 mois, 2 jours de t\u00e9l\u00e9travail.\nTJM : 550 \u20ac/j\n5 ans d'exp\u00e9rience",
            "country": "FR",
            "compensation": None,
            "currency": "",
        }
        enrich_facts(row, track="freelance")
        self.assertEqual(row["contracts"], ["contractor"])
        self.assertEqual(row["remote"], "hybrid")
        self.assertEqual(row["experience_level"], "senior")
        self.assertEqual(row["experience_years_min"], 5)
        self.assertEqual(row["duration_months"], 6)
        self.assertEqual(row["daily_rate_max"], 550.0)
        self.assertEqual(row["compensation"], 550.0)
        self.assertEqual(row["currency"], "EUR")
        # Structured values already on the row win over the text.
        fixed = {"title": "Data Engineer", "description": "Full remote", "remote": "onsite", "contracts": ["permanent"], "country": "GB", "compensation": 70000, "currency": ""}
        enrich_facts(fixed, track="jobs")
        self.assertEqual(fixed["remote"], "onsite")
        self.assertEqual(fixed["contracts"], ["permanent"])
        self.assertEqual(fixed["salary_max"], 70000.0)
        self.assertEqual(fixed["currency"], "GBP")


class JobPostingTest(unittest.TestCase):
    def test_extract_reads_graph_and_plain_blocks(self) -> None:
        items = extract_jobpostings(JOBPOSTING_HTML)
        self.assertEqual([item["title"] for item in items], ["Data Engineer Senior (H/F)", "Platform Engineer"])
        self.assertEqual(extract_jobpostings("<html>no data</html>"), [])
        self.assertEqual(extract_jobpostings('<script type="application/ld+json">not json</script>'), [])

    def test_facts_cover_pay_place_remote_experience_and_dates(self) -> None:
        facts = jobposting_facts(extract_jobpostings(JOBPOSTING_HTML)[0], track="freelance")
        self.assertEqual(facts["company"], "Acme")
        self.assertEqual(facts["location"], "Paris, FR")
        self.assertEqual(facts["country"], "FR")
        self.assertEqual(facts["contracts"], ["contractor"])
        self.assertEqual(facts["track"], "freelance")
        self.assertEqual(facts["remote"], "remote")
        self.assertEqual((facts["daily_rate_min"], facts["daily_rate_max"]), (500.0, 600.0))
        self.assertEqual(facts["currency"], "EUR")
        self.assertEqual(facts["compensation"], 600.0)
        self.assertEqual(facts["experience_years_min"], 3)
        self.assertEqual(facts["start_date"], "2026-10-01")
        self.assertEqual(facts["valid_through"], "2026-10-01")
        self.assertEqual(facts["duration_months"], 6)
        self.assertNotIn("<", facts["description"])
        second = jobposting_facts(extract_jobpostings(JOBPOSTING_HTML)[1], track="jobs")
        self.assertEqual(second["country"], "GB")
        self.assertEqual(second["contracts"], ["permanent"])
        self.assertEqual((second["salary_min"], second["salary_max"]), (70000.0, 90000.0))
        self.assertEqual(second["currency"], "GBP")
        self.assertEqual(second["remote"], "hybrid")

    def test_rows_drop_closed_hosts_and_name_the_source(self) -> None:
        rows = jobposting_rows(JOBPOSTING_HTML, page_url="https://careers.acme.example/jobs", track="freelance")
        self.assertEqual(len(rows), 1)  # the LinkedIn URL is a closed host
        self.assertEqual(rows[0]["source"], "jsonld")
        self.assertEqual(rows[0]["ingest"], "jsonld")
        self.assertEqual(rows[0]["attribution"], "Acme (JobPosting)")
        self.assertEqual(rows[0]["url"], "https://careers.acme.example/jobs/42")


class WorkableBoardTest(unittest.TestCase):
    def test_workable_widget_payload_becomes_rows(self) -> None:
        payload = {
            "jobs": [
                {
                    "title": "Data Engineer",
                    "shortcode": "AB12",
                    "url": "https://apply.workable.com/acme/j/AB12/",
                    "location": {"city": "Paris", "country": "France"},
                    "description": "<p>Spark</p>",
                },
                {"title": "Closed", "url": "https://www.indeed.com/viewjob?jk=1"},
            ]
        }
        rows = _normalize_ats("workable", "acme", payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "workable")
        self.assertEqual(rows[0]["location"], "Paris, France")
        self.assertEqual(rows[0]["url"], "https://apply.workable.com/acme/j/AB12/")


class JobOpportunitiesApiTest(unittest.TestCase):
    def test_keyed_collector_maps_fields_and_needs_a_key(self) -> None:
        payload = {
            "data": [
                {
                    "id": "1",
                    "title": "Data Engineer",
                    "company": "Vodafone",
                    "city": "Paris",
                    "country": "FR",
                    "remote": "hybrid",
                    "employment_type": "full_time",
                    "seniority": "senior",
                    "salary_min": 55000,
                    "salary_max": 65000,
                    "salary_currency": "EUR",
                    "salary_period": "year",
                    "posted_at": "2026-09-01T08:00:00Z",
                    "apply_url": "https://careers.vodafone.example/jobs/1",
                    "description": "dbt, Snowflake",
                    "source": "workday",
                },
                {"id": "2", "title": "Closed", "apply_url": "https://www.linkedin.com/jobs/view/2"},
            ]
        }
        with patch.dict("os.environ", {}, clear=False):
            self.assertEqual(fetch_jobopportunities("Data Engineer", ["FR"], "jobs"), [])
        seen: dict[str, str] = {}

        def fake_get(url: str, *, headers: dict[str, str] | None = None) -> dict:
            seen["url"] = url
            seen["auth"] = (headers or {}).get("Authorization", "")
            return payload

        with patch("navin.career.official._get_json", side_effect=fake_get):
            rows = fetch_jobopportunities("Data Engineer", ["FR", "BE"], "jobs", secrets={"JOBOPPORTUNITIES_API_KEY": "k-1"})
        self.assertEqual(seen["auth"], "Bearer k-1")
        self.assertIn("country=FR%2CBE", seen["url"])
        self.assertIn("include_description=true", seen["url"])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "jobopportunities")
        self.assertEqual(row["ingest"], "official_api")
        self.assertEqual(row["country"], "FR")
        self.assertEqual(row["remote"], "hybrid")
        self.assertEqual(row["contracts"], ["permanent"])
        self.assertEqual(row["track"], "jobs")
        self.assertEqual((row["salary_min"], row["salary_max"]), (55000.0, 65000.0))
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["experience_level"], "senior")
        self.assertIn("workday", row["attribution"])


class CurrencyAwareScoreTest(unittest.TestCase):
    def test_pay_is_compared_in_the_profile_currency(self) -> None:
        profile = {"titles": ["Data Engineer"], "track": "freelance", "min_rate": 500, "currency": "EUR", "stack": []}
        gbp = {"title": "Data Engineer", "compensation": 450, "currency": "GBP", "track": "freelance", "description": ""}
        usd = {"title": "Data Engineer", "compensation": 450, "currency": "USD", "track": "freelance", "description": ""}
        eur = {"title": "Data Engineer", "compensation": 450, "currency": "EUR", "track": "freelance", "description": ""}
        score_gbp = score_opportunity(gbp, profile)["match_score"]
        score_usd = score_opportunity(usd, profile)["match_score"]
        score_eur = score_opportunity(eur, profile)["match_score"]
        # 450 GBP is about 526 EUR: at or above the floor. 450 USD is about 414 EUR: under it.
        self.assertGreater(score_gbp, score_eur)
        self.assertGreater(score_gbp, score_usd)
        self.assertLessEqual(score_usd, score_eur)


if __name__ == "__main__":
    unittest.main()
