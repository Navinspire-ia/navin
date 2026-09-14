# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from navin.career.desk import ingest_hits
from navin.career.mission_platforms import indexed_missions, platform_by_id
from navin.career.normalize import enrich_facts
from navin.career.prospecting import _state, handle_prospecting
from navin.career.scope import offer_rejection
from navin.career.store import CareerStore


def today():
    return datetime.now(timezone.utc).date().isoformat()


def criteria(**changes):
    return {"company": {"name": "RFP verification"}, "mode": "missions", "sources": ["freelancer_jobs"], "roles": ["Data Engineer"],
            "skills": ["Python", "SQL"], "countries": ["FR"], "track": "freelance", "work_mode": "any",
            "sale_rate_remote": 300, "sale_rate_onsite": 650, "min_project_budget": 10000,
            "currency": "EUR", "max_age_days": 30, "daily_search": False, **changes}


def project(**changes):
    return {"id": "consultation", "title": "RFP - Data Engineer", "track": "freelance", "country": "FR",
            "description": "Python SQL", "need_type": "rfp", "price_model": "fixed", "budget": 20000,
            "currency": "EUR", "remote": "", "posted_at": today(), **changes}


@pytest.mark.parametrize(("title", "kind"), [
    ("RFP - Data Engineer", "rfp"), ("Appel d'offres : Data Engineer", "rfp"),
    ("RFQ: Data Engineer", "rfq"), ("Request for information - Data Engineer", "rfi"),
    ("Expression of interest - Data Engineer", "eoi"), ("[SoW] Data Engineer", "sow"),
    ("Statement of work: Data Engineer", "sow"), ("Data Engineer with RFP experience", "consulting"),
])
def test_type_comes_from_the_need_not_incidental_experience(title, kind):
    row = enrich_facts({"title": title, "description": "Experience drafting RFP responses.",
                        "track": "freelance", "need_type": "consulting"})
    assert row["need_type"] == kind


@pytest.mark.parametrize("text", ["Fixed-price: EUR 20,000", "Budget total : 20 000 EUR", "Forfait : 20k EUR"])
def test_project_budget_is_not_a_day_rate_or_a_salary(text):
    row = enrich_facts({"title": "Data Engineer", "track": "freelance", "description": text})
    assert row["budget"] == 20000 and row["price_model"] == "fixed"
    assert row["daily_rate_max"] is row["salary_max"] is row["compensation"] is None


def test_project_floor_is_independent_of_day_rates_and_unknown_work_mode():
    assert not offer_rejection(project(), criteria())
    assert offer_rejection(project(budget=9999), criteria()) == "budget"
    assert offer_rejection(project(budget_min=9000, budget_max=20000), criteria()) == "budget"
    assert offer_rejection(project(budget=None), criteria()) == "budget_unknown"
    assert offer_rejection(project(currency=""), criteria()) == "budget_unknown"
    assert not offer_rejection(project(budget=None), criteria(min_project_budget=0))
    assert offer_rejection(project(), criteria(work_mode="remote")) == "work_mode_unknown"
    assert offer_rejection(project(country="BE"), criteria()) == "country"
    assert offer_rejection(project(description="SQL"), criteria()) == "skills"
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    assert offer_rejection(project(deadline=yesterday), criteria()) == "expired"


def test_rfp_with_published_day_rate_still_uses_mode_floor():
    row = project(price_model="daily", budget=None, daily_rate_max=299, remote="remote")
    assert offer_rejection(row, criteria()) == "rate"
    row["daily_rate_max"] = 300
    assert not offer_rejection(row, criteria())
    row["remote"] = "hybrid"
    assert offer_rejection(row, criteria()) == "rate"
    parsed = enrich_facts(project(price_model="", budget=None, description="Python SQL. Budget: 500 EUR/day"))
    assert parsed.get("price_model") != "fixed" and parsed["daily_rate_max"] == 500
    structured = enrich_facts(project(price_model="", daily_rate_max=500, description="Python SQL. Project budget: EUR 20000"))
    assert structured["daily_rate_max"] == 500 and structured.get("price_model") != "fixed"


def test_public_marketplace_consultations_reach_career_with_budget_filter(tmp_path):
    store = CareerStore(tmp_path)
    handle_prospecting(store, "prospecting_config", {"activate_company": True, "criteria": criteria()})
    hits = [{"title": "RFP - Data Engineer", "url": "https://www.freelancer.com/projects/python/rfp-data-engineer",
             "snippet": "France. Python SQL. Fixed-price: EUR 20,000.", "date": today()},
            {"title": "SoW - Data Engineer", "url": "https://www.freelancer.com/projects/python/sow-data-engineer",
             "snippet": "France. Python SQL. Budget: EUR 9,000.", "date": today()}]
    with patch("navin.career.prospecting._mission_source", return_value=indexed_missions(platform_by_id("freelancer_jobs"), hits)):
        handle_prospecting(store, "prospecting_search", {})
    saved = store.load_opportunities()
    assert len(saved) == 1 and saved[0]["need_type"] == "rfp" and saved[0]["track"] == "freelance"
    assert saved[0]["budget"] == 20000 and saved[0]["daily_rate_max"] is None
    assert _state(store)["last_run"]["sources"][0]["rejected"] == {"budget": 1}
    assert _state(CareerStore(tmp_path))["criteria"]["min_project_budget"] == 10000


def test_imported_private_marketplace_rfp_retains_its_project_fields(tmp_path):
    store = CareerStore(tmp_path)
    handle_prospecting(store, "prospecting_config", {"activate_company": True, "criteria": criteria()})
    deadline = (datetime.now(timezone.utc).date() + timedelta(days=8)).isoformat()
    ingest_hits(store, {"opportunities": [project(source="malt_missions", deadline=deadline,
                                                  url="https://www.malt.fr/projects/example")]})
    saved = store.load_opportunities()[0]
    assert saved["need_type"] == "rfp" and saved["price_model"] == "fixed"
    assert saved["deadline"] == deadline and saved["budget"] == 20000 and saved["daily_rate_max"] is None
