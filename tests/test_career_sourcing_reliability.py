# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from navin.career.collect import _search_ddgs
from navin.career.errors import CareerError
from navin.career.prospecting import (
    _candidates,
    _match,
    _mission_source,
    _state,
    handle_prospecting,
)
from navin.career.store import CareerStore


@pytest.fixture
def store(tmp_path):
    store = CareerStore(tmp_path / "career")
    handle_prospecting(store, "prospecting_config", {"activate_company": True, "criteria": {
        "company": {"name": "Sourcing test"},
        "roles": ["Data Engineer"], "skills": ["Python", "SQL"], "countries": ["FR"], "track": "freelance",
        "sources": ["google_jobs", "brave_jobs", "linkedin", "freework", "collective"],
        "platforms": ["linkedin", "malt"], "mode": "missions", "sale_rate": 650,
        "max_age_days": 30, "work_mode": "remote", "signal_only": True,
    }})
    return store


def offer():
    return {"id": "mission", "title": "Directeur de projet en intelligence artificielle (IA) F/H",
            "stack": ["Python"], "country": "FR", "remote": "remote", "track": "freelance",
            "daily_rate_min": 650, "currency": "EUR", "posted_at": datetime.now(timezone.utc).date().isoformat(),
            "url": "https://example.com/mission", "source": "freework"}


def test_public_search_uses_metasearch_before_the_blocked_html_endpoint():
    with patch("ddgs.DDGS") as ddgs, patch("navin.career.collect._search_ddg_html") as html:
        ddgs.return_value.text.return_value = [{"title": "Sam", "href": "https://malt.fr/profile/sam", "body": "Disponible France"}]
        result = _search_ddgs("site:malt.fr/profile/ Python France", 20, strict=True)
    assert result == [{"title": "Sam", "url": "https://malt.fr/profile/sam", "snippet": "Disponible France"}]
    html.assert_not_called()


@pytest.mark.parametrize("failure", [False, True])
def test_public_search_fallback_preserves_failure_instead_of_claiming_no_profiles(failure):
    with patch("ddgs.DDGS") as ddgs, patch("navin.career.collect._search_ddg_html", side_effect=CareerError("limited by the provider")) as html:
        ddgs.return_value.text.return_value = []
        if failure:
            ddgs.return_value.text.side_effect = RuntimeError("unavailable")
        with pytest.raises(CareerError, match="limited by the provider"):
            _search_ddgs("query", 20, strict=True)
    html.assert_called_once_with("query", 20, strict=True)


def test_unconfigured_sources_do_not_consume_mission_search_capacity(store):
    original = _state(store)["criteria"]
    with patch("navin.career.prospecting.MAX_TASKS", 2), patch("navin.career.prospecting._mission_source", return_value=[]) as search:
        handle_prospecting(store, "prospecting_search", {})
    assert {call.args[1] for call in search.call_args_list} == {"freework", "collective"}
    state = _state(store)
    assert {s["source"] for s in state["last_run"]["sources"] if s["status"] == "not_configured"} == {"missions:google_jobs", "missions:brave_jobs"}
    assert state["last_run"]["deferred"] == 1
    assert state["criteria"] == original
    assert all(call.args[2]["sale_rate"] == 650 and call.args[2]["countries"] == ["FR"] for call in search.call_args_list)


def test_a_failed_platform_keeps_other_platform_results_and_reports_rejections(store):
    store.upsert_opportunities([offer()])
    def web(_, provider, query):
        assert provider == "public_web"
        assert query.count("site:") == (1 if "site:linkedin" in query else 2) and "F/H" not in query
        assert "France" in query and "intelligence artificielle" in query
        if "site:linkedin" in query:
            raise CareerError("Web search was limited by the provider.")
        return [
            {"title": "Sam directeur de projet IA", "url": "https://malt.fr/profile/sam", "snippet": "Disponible Python France"},
            {"title": "Foreign profile", "url": "https://malt.fr/profile/foreign", "snippet": "Disponible Python Oman"},
            {"title": "Unknown availability", "url": "https://malt.fr/profile/unknown", "snippet": "Python France"},
        ]
    with patch("navin.career.prospecting._web", side_effect=web):
        handle_prospecting(store, "prospecting_match", {"id": "mission"})
    state = _state(store)
    assert len(state["matches"]["mission"]["results"]) == 1
    assert state["candidates"][0]["url"] == "https://malt.fr/profile/sam"
    sources = {s["platform"]: s for s in state["last_run"]["sources"]}
    assert sources["linkedin"]["error_code"] == "rate_limited"
    assert sources["malt"]["rejected"] == {"country": 1, "availability_unknown": 1}
    assert sources["malt"]["count"] == 1


def test_cooling_source_releases_capacity_for_another_selected_source(store):
    with patch("navin.career.prospecting.MAX_TASKS", 1), patch("navin.career.prospecting._mission_source", side_effect=CareerError("blocked")):
        handle_prospecting(store, "prospecting_search", {})
    with patch("navin.career.prospecting.MAX_TASKS", 1), patch("navin.career.prospecting._mission_source", return_value=[]) as search:
        handle_prospecting(store, "prospecting_search", {})
    assert search.call_count == 1 and search.call_args.args[1] != "freework"
    assert any(s["status"] == "cooldown" for s in _state(store)["last_run"]["sources"])


def test_optional_availability_keeps_unknown_but_rejects_declared_unavailable_and_mixed_evidence(store):
    criteria = {**_state(store)["criteria"], "signal_only": False, "platforms": ["malt"]}
    hit = {"title": "Sam Data Engineer", "url": "https://malt.fr/profile/sam", "snippet": "Python SQL France"}
    with patch("navin.career.prospecting._web", return_value=[hit,
        {**hit, "url": "https://malt.fr/profile/busy", "snippet": "Python SQL France, non disponible"},
        {**hit, "url": "https://malt.fr/profile/mixed", "title": "Several concatenated profiles " * 20} ]):
        rows = _candidates(store, "public_web", criteria)
    assert len(rows) == 1 and rows[0]["signal"] == "unknown" and rows[0]["country"] == "FR"
    assert rows.rejected == {"unavailable": 1, "evidence": 1}


def test_matching_requires_more_than_the_word_intelligence_for_an_ai_project_director(store):
    state = _state(store)
    state["candidates"] = [{"id": str(index), "name": title, "headline": title, "snippet": "France", "country": "FR"}
                           for index, title in enumerate(["Business Intelligence Development", "Strategy and Intelligence Expert", "Directeur du Pôle IA", "Direction de programmes DATA/IA, directeur de projets"])]
    _match(store, state, offer())
    assert {row["candidate"]["id"] for row in state["matches"]["mission"]["results"]} == {"2", "3"}


@pytest.mark.parametrize("source,module,function", [("freework", "freework", "search_freework_jobs"), ("collective", "collective", "search_collective_jobs")])
def test_freelance_listings_can_read_two_pages(store, source, module, function):
    with patch(f"navin.career.{module}.{function}", return_value={"jobs": []}) as search:
        _mission_source(store, source, _state(store)["criteria"])
    assert search.call_args.kwargs["max_pages"] == search.call_args.kwargs["max_requests"] == 2


def test_parallel_role_searches_share_the_public_site_interval():
    from navin.career.http_pacing import pace_public_request

    with patch("navin.career.http_pacing._last", {}), patch("navin.career.http_pacing.time.monotonic", return_value=100), patch("navin.career.http_pacing.time.sleep") as sleep:
        pace_public_request("freework", 1)
        pace_public_request("collective", 1)
        pace_public_request("freework", 1)
        pace_public_request("freework", 1)
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]
