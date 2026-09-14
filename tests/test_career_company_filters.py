# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from navin.career.errors import CareerError
from navin.career.linkedin import to_job
from navin.career.normalize import normalize_remote
from navin.career.prospecting import _state, handle_prospecting
from navin.career.scope import offer_rejection, selling_floor
from navin.career.sourcing_mail import proposal_rate
from navin.career.store import CareerStore


def offer(identity="remote", **changes):
    return {"id": identity, "title": "Data Engineer", "description": "Python SQL", "stack": ["Python", "SQL"],
            "country": "FR", "location": "Paris, France", "remote": "remote", "track": "freelance",
            "daily_rate_min": 300, "daily_rate_max": 500, "currency": "EUR", "source": "example",
            "posted_at": datetime.now(timezone.utc).date().isoformat(), "stage": "discovered",
            "url": f"https://example.com/mission/{identity}", **changes}


def criteria(**changes):
    return {"mode": "missions", "roles": ["Data Engineer"], "skills": ["Python", "SQL"], "countries": ["FR"],
            "track": "freelance", "work_mode": "any", "max_age_days": 30, "currency": "EUR", "sale_rate": 650,
            "sale_rate_remote": 300, "sale_rate_onsite": 650, "daily_search": False, **changes}


@pytest.mark.parametrize(("source", "country"), [
    ("linkedin", "FR"), ("freework", "FR"), ("collective", "FR"), ("mon-consultant-independant", "FR"),
    ("lehibou", "FR"), ("freelanceinformatique", "FR"), ("freelancermap_jobs", "CH"), ("prounity", "BE"),
    ("freelancers_lu", "LU"), ("peopleperhour_jobs", "GB"), ("upwork_jobs", "CA"),
    ("freelancer_jobs", "AU"), ("freelancer_au", "AU"), ("public_jobs", "FR"),
])
def test_every_source_goes_through_the_same_business_filters(tmp_path, source, country):
    store = CareerStore(tmp_path / "career")
    handle_prospecting(store, "prospecting_config", {"activate_company": True, "criteria": criteria(
        company={"name": "Filter audit"}, sources=[source], countries=[country])})
    rows = [offer(country=country),
            offer("hybrid", country=country, remote="hybrid", daily_rate_min=650, daily_rate_max=750),
            offer("onsite", country=country, remote="onsite", daily_rate_min=650, daily_rate_max=750),
            offer("remote-low", country=country, daily_rate_min=299),
            offer("hybrid-low", country=country, remote="hybrid", daily_rate_min=649),
            offer("onsite-low", country=country, remote="onsite", daily_rate_min=649),
            offer("unknown-mode", country=country, remote="", description="Python SQL remote possible"),
            offer("wrong-role", country=country, title="Data Analyst"),
            offer("missing-skill", country=country, stack=["SQL"], description="SQL Server"),
            offer("wrong-country", country="OM", location="Muscat, Oman"),
            offer("unknown-pay", country=country, daily_rate_min=None, daily_rate_max=None),
            offer("old", country=country, posted_at=(datetime.now(timezone.utc).date() - timedelta(days=31)).isoformat()),
            offer("permanent", country=country, employment_type="CDI")]
    with patch("navin.career.prospecting._mission_source", return_value=rows):
        handle_prospecting(store, "prospecting_search", {})
    assert {row["id"] for row in store.load_opportunities()} == {"remote", "hybrid", "onsite"}
    rejected = _state(store)["last_run"]["sources"][0]["rejected"]
    assert rejected == {"rate": 3, "work_mode_unknown": 1, "role": 1, "skills": 1, "country": 1, "rate_unknown": 1, "date": 1, "track": 1}


def test_separate_floors_survive_reload_and_drive_client_proposals(tmp_path):
    store = CareerStore(tmp_path)
    handle_prospecting(store, "prospecting_config", {"criteria": criteria()})
    saved = _state(CareerStore(tmp_path))["criteria"]
    assert selling_floor(saved, "remote") == 300
    assert selling_floor(saved, "hybrid") == selling_floor(saved, "onsite") == 650
    assert proposal_rate(saved, {"purchase_rate": 200}, offer()) == 300
    assert proposal_rate(saved, {"purchase_rate": 200}, offer(remote="hybrid")) == 650
    assert proposal_rate({**saved, "margin_percent": 20}, {"purchase_rate": 400}, offer()) == 500
    assert selling_floor({"sale_rate": 650, "min_rate": 700}, "remote") == 700
    handle_prospecting(store, "prospecting_config", {"criteria": {"sale_rate_remote": 0}})
    assert selling_floor(_state(store)["criteria"], "remote") == 0
    assert selling_floor(_state(store)["criteria"], "onsite") == 650


@pytest.mark.parametrize("value", [-1, 100001, float("nan"), float("inf"), True, None, "bad"])
@pytest.mark.parametrize("field", ["sale_rate_remote", "sale_rate_onsite"])
def test_invalid_floors_cannot_change_saved_configuration(tmp_path, field, value):
    store = CareerStore(tmp_path)
    handle_prospecting(store, "prospecting_config", {"criteria": criteria()})
    with pytest.raises(CareerError):
        handle_prospecting(store, "prospecting_config", {"criteria": {field: value}})
    assert _state(store)["criteria"][field] == criteria()[field]


@pytest.mark.parametrize(("text", "expected"), [
    ("5 jours de télétravail", "remote"), ("Full remote", "remote"), ("Paris - Hybrid", "hybrid"),
    ("Remote: 2 days on site", "hybrid"), ("Télétravail aménageable", ""), ("Remote possible", ""),
    ("Up to 100% remote", ""), ("Full remote, monthly office attendance required", "hybrid"),
    ("No remote allowed", "onsite"), ("Hybrid search and cloud architecture", ""),
    ("Télétravail : 2 à 3 jours par semaine", "hybrid"), ("50% remote", "hybrid"),
    ("Remote Desktop infrastructure and remote access support", ""), ("Pas de télétravail", "onsite"),
])
def test_full_remote_requires_unambiguous_work_evidence(text, expected):
    assert normalize_remote(text) == expected
    assert bool(offer_rejection(offer(remote="", description=text), criteria(work_mode="remote", skills=[]))) == (expected != "remote")


def test_linkedin_does_not_turn_hybrid_or_search_country_into_offer_facts():
    row = to_job({"title": "Data Engineer", "location": "London, UK", "url": "https://www.linkedin.com/jobs/view/123"},
                 {"description": "Hybrid"}, country="FR", track="freelance")
    assert row["remote"] == "hybrid"
    assert row["country"] == "GB"
    assert offer_rejection(row, criteria(work_mode="remote")) == "country"
    assert to_job({"title": "Data Engineer", "location": ""}, {}, country="FR", track="freelance")["country"] == ""


def test_freework_and_collective_do_not_use_locale_as_geographic_evidence():
    from navin.career.collective import to_job as collective
    from navin.career.freework import to_job as freework

    record = {"title": "Data Engineer", "slug": "data-engineer", "location": {"label": "Brussels, Belgium"}, "remoteMode": "partial"}
    assert freework(record, country="FR", track="freelance")["country"] == "BE"
    record["location"] = {}
    assert freework(record, country="FR", track="freelance")["country"] == ""
    row = collective({"name": "Data Engineer", "slug": "data-engineer", "workPreferences": ["REMOTE", "ON_SITE"]}, locale="fr", track="freelance")
    assert row["country"] == ""
    assert row["remote"] == "hybrid"


def test_role_skill_groups_and_bilingual_evidence_do_not_require_other_roles():
    wanted = criteria(roles=["Data Engineer", "Frontend developer"], skills=["Python", "SQL", "React", "Qualité des données"],
                      role_skills={"Data Engineer": ["Python", "SQL", "Qualité des données"], "Frontend developer": ["React"]})
    assert not offer_rejection(offer(title="Ingénieur données", description="Python SQL data quality"), wanted)
    assert not offer_rejection(offer(title="Développeur frontend", description="React", stack=["React"]), wanted)
    assert offer_rejection(offer(description="Python SQL"), wanted) == "skills"
    assert offer_rejection(offer(description="JavaScript", stack=[]), criteria(skills=["Java"])) == "skills"
    assert offer_rejection(offer(title="Data analyst"), criteria()) == "role"
    assert not offer_rejection(offer(description="Spark ETL", stack=[]), criteria(skills=["Apache Spark", "ETL / ELT"]))
    assert offer_rejection(offer(location="Lyon, France"), criteria(city="Paris")) == "city"
