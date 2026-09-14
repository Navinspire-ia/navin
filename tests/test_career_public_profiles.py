# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from unittest.mock import patch

import pytest

from navin.career.prospecting import _candidates, _state, handle_prospecting
from navin.career.prospecting_catalog import is_profile_url, platform_catalog
from navin.career.public_profiles import enrich_lu_profile, parse_lu_profiles
from navin.career.store import CareerStore


def card(identity, city, role="Data Engineer", amount=400):
    return f'''<a href="/fr/freelancers/{identity}" aria-label="Sam">
      <span>Disponible</span><h3>{role}</h3><span class="max-w-city">{city}</span>
      <span>{amount} €/jour</span><span class="li-pill">Python</span><span class="li-pill">SQL</span>
      <span class="li-pill">+16</span></a>'''


def test_lu_profiles_keep_public_price_skills_and_actual_residence():
    row = parse_lu_profiles(card("sam", "Paris, France"))[0]
    assert row["country"] == "FR"
    assert row["skills"] == ["Python", "SQL"]
    assert row["name"] == "Sam"
    detail = '<main><div><h1>Sam</h1><p>Data Engineer</p><p><span>Brussels, Belgium</span></p></div><h2>À propos</h2>Python SQL</main>'
    enriched = enrich_lu_profile(row, detail)
    assert enriched["country"] == "BE"
    assert "400 €/jour" in enriched["snippet"]


@pytest.mark.parametrize(("platform_id", "valid", "invalid"), [
    ("malt", "https://www.malt.fr/profile/sam", "https://malt.fr/profile/"),
    ("malt", "https://www.malt.com/profile/sam", "https://malt.fr.example.com/profile/sam"),
    ("upwork", "https://www.upwork.com/freelancers/~0123", "https://www.upwork.com/hire/data-engineers/"),
    ("freelancer", "https://www.freelancer.com.au/u/sam", "https://www.freelancer.com/projects/python/client-brief"),
    ("freelancermap", "https://www.freelancermap.com/profile/sam", "https://www.freelancermap.com/project/python-project"),
])
def test_mission_and_profile_urls_are_separate(platform_id, valid, invalid):
    platform = next(row for row in platform_catalog() if row["id"] == platform_id)
    assert is_profile_url(valid, platform)
    assert not is_profile_url(invalid, platform)


def test_lu_search_runs_without_search_api_and_keeps_country_evidence(tmp_path):
    store = CareerStore(tmp_path)
    handle_prospecting(store, "prospecting_config", {"criteria": {
        "mode": "profiles", "roles": ["Data Engineer"], "profile_roles": ["Data Engineer"],
        "skills": ["Python", "SQL"], "countries": ["FR"], "platforms": ["freelancers_lu"],
    }})
    with patch("navin.career.public_profiles._fetch", return_value=card("sam", "Paris, France") + card("outside", "Luxembourg")), \
         patch("navin.career.prospecting._web", side_effect=AssertionError("native public profiles must not need web search")):
        handle_prospecting(store, "prospecting_search", {})
    state = _state(store)
    assert len(state["candidates"]) == 1
    candidate = state["candidates"][0]
    assert candidate["source"] == "Freelancers.lu"
    assert candidate["country"] == "FR"
    assert candidate["daily_rate"] == 400
    assert candidate["email"] == ""
    assert state["last_run"]["sources"][0]["rejected"] == {"country": 1}


def test_upwork_and_malt_search_public_profiles_only_and_keep_unknown_availability(tmp_path):
    store = CareerStore(tmp_path)
    criteria = {**_state(store)["criteria"], "platforms": ["upwork", "malt"], "countries": ["FR"], "signal_only": False,
                "skills": ["Python", "SQL"], "roles": ["Data Engineer"]}
    with patch("navin.career.prospecting._web", return_value=[
        {"title": "Sam Data Engineer", "snippet": "Python SQL Paris France", "url": "https://www.upwork.com/freelancers/~0123"},
        {"title": "Alex Data Engineer", "snippet": "Python SQL Paris France", "url": "https://www.malt.com/profile/alex"},
        {"title": "Mission Data Engineer", "snippet": "Python SQL Paris France", "url": "https://www.upwork.com/jobs/~0123"},
    ]):
        rows = _candidates(store, "public_web", criteria)
    assert len(rows) == 2
    assert all(row["signal"] == "unknown" for row in rows)
    assert rows.rejected == {"platform": 1}
