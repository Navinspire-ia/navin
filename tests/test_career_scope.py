# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from navin.agent.tools.career import CareerTool
from navin.career.collect import collect
from navin.career.desk import snapshot
from navin.career.dossier import format_agent_status
from navin.career.errors import CareerError
from navin.career.prospecting import _match, _save, _state, handle_prospecting
from navin.career.scope import candidate_countries, offer_rejection, publication_day
from navin.career.store import CareerStore
from navin.webui.career_api import handle_career_action


def day(age=0):
    return (datetime.now(timezone.utc).date() - timedelta(days=age)).isoformat()


def mission(**extra):
    return {"id": "allowed", "title": "Data Engineer", "description": "Python SQL", "stack": ["Python", "SQL"],
            "country": "FR", "remote": "remote", "track": "freelance", "source": "linkedin", "posted_at": day(3),
            "daily_rate_min": 650, "daily_rate_max": 700, "currency": "EUR", "stage": "discovered",
            "url": "https://example.com/jobs/allowed", **extra}


@pytest.fixture
def company(tmp_path):
    store = CareerStore(tmp_path / "career")
    handle_prospecting(store, "prospecting_config", {"activate_company": True, "criteria": {
        "company": {"name": "Scope preview"}, "mode": "both", "roles": ["Data Engineer"], "skills": ["Python", "SQL"],
        "countries": ["FR", "BE", "CH", "LU"], "sale_rate": 650, "currency": "EUR", "track": "freelance",
        "work_mode": "remote", "max_age_days": 30, "sources": ["linkedin"], "platforms": ["linkedin", "malt"],
    }})
    return store


def test_empty_archive_requires_confirmation_and_keeps_active_data(company):
    company.save_opportunities([mission(), mission(id="archived", archived=True), mission(id="archived-favorite", archived=True, favorite=True)])
    company.save_applications([{"id": "receipt", "opportunity_id": "archived"}])
    state = _state(company)
    state["candidates"] = [{"id": "candidate", "name": "Morgan"}]
    state["matches"] = {oid: {"results": []} for oid in ("allowed", "archived", "archived-favorite")}
    _save(company, state)
    with patch("navin.webui.career_api._store", return_value=company):
        with pytest.raises(CareerError, match="confirmation"):
            handle_career_action("empty_archive")
        assert len(company.load_opportunities()) == 3
        result = handle_career_action("empty_archive", {"confirmed": True})
        assert {row["id"] for row in result["opportunities"]} == {"allowed"}
        assert set(result["prospecting"]["matches"]) == {"allowed"}
        assert len(result["prospecting"]["candidates"]) == 1
        assert company.load_applications()[0]["id"] == "receipt"
        assert len(handle_career_action("empty_archive", {"confirmed": True})["opportunities"]) == 1


@pytest.mark.parametrize("account_kind", ["company", "solo"])
def test_top_clear_keeps_company_or_solo_configuration_and_cv(company, account_kind):
    company.save_profile({"account_kind": account_kind, "company_prospecting": account_kind == "company",
                          "wizard_complete": True, "display_name": "Morgan", "master_cv": "Original CV Python SQL",
                          "talents": [{"id": "robin", "name": "Robin"}],
                          "mailbox": {"sender_email": "morgan@example.com", "smtp_host": "smtp.example.com"}})
    company.save_opportunities([mission(), mission(id="archived", archived=True)])
    company.save_applications([{"id": "application", "opportunity_id": "allowed"}])
    company.save_inbox([{"id": "reply"}])
    company.save_secret("CAREER_SERPAPI_KEY", "private")
    company.save_bytes("original-cv.pdf", b"original CV", file_id="master")
    schedule = {"kind": "weekly", "hour": 11, "minute": 30, "weekday": 2, "tz": "Europe/Paris"}
    company.save_loop({"enabled": True, "schedule": schedule})
    profile_bytes = company.profile_path.read_bytes()
    criteria = _state(company)["criteria"]
    with patch("navin.webui.career_api._store", return_value=company):
        result = handle_career_action("archive_reset", {"confirmed": True})
    assert company.profile_path.read_bytes() == profile_bytes
    assert result["profile"]["wizard_complete"] is True
    assert result["profile"]["account_kind"] == account_kind
    assert result["profile"]["master_cv"] == "Original CV Python SQL"
    assert result["prospecting"]["criteria"] == criteria
    assert len(result["prospecting"]["candidates"]) == 1
    assert result["opportunities"] == result["applications"] == result["inbox"] == []
    assert company.read_bytes("master")["size"] == len(b"original CV")
    assert company.has_secret("CAREER_SERPAPI_KEY")
    assert not company.load_loop()["enabled"]
    assert company.load_loop()["schedule"] == schedule
    assert result["archive_receipt"]["retained_configuration"] is True
    archived = company.root / "archives" / result["archive_receipt"]["id"]
    assert (archived / "profile.json").read_bytes() == profile_bytes
    assert (archived / "opportunities.json").is_file()


def test_failed_clear_restores_offers_and_keeps_configuration(company):
    from navin.career.store import _atomic_write

    company.save_opportunities([mission()])
    profile = company.profile_path.read_bytes()
    prospecting = (company.root / "prospecting.json").read_bytes()
    def fail_manifest(path, data):
        if path.name == "manifest.json":
            raise OSError("Archive unavailable")
        _atomic_write(path, data)
    with patch("navin.webui.career_api._store", return_value=company), \
         patch("navin.desk_archive._atomic_write", side_effect=fail_manifest):
        with pytest.raises(OSError, match="Archive unavailable"):
            handle_career_action("archive_reset", {"confirmed": True})
    assert company.profile_path.read_bytes() == profile
    assert (company.root / "prospecting.json").read_bytes() == prospecting
    assert company.load_opportunities()[0]["id"] == "allowed"


def test_delete_internal_candidate_clears_pool_matches_and_source_talent(company):
    company.save_profile({"talents": [{"id": "morgan", "name": "Morgan"}, {"id": "robin", "name": "Robin"}], "active_talent_id": "morgan"})
    state = _state(company)
    state["matches"] = {"mission": {"results": [{"candidate": row} for row in state["candidates"]]}}
    _save(company, state)
    with patch("navin.webui.career_api._store", return_value=company):
        with pytest.raises(CareerError, match="confirmation"):
            handle_career_action("prospecting_delete_candidate", {"id": "internal:morgan"})
        assert len(_state(company)["candidates"]) == 2
        handle_career_action("prospecting_delete_candidate", {"id": "internal:morgan", "confirmed": True})
    reloaded = _state(CareerStore(company.root))
    assert {row["id"] for row in reloaded["candidates"]} == {"internal:robin"}
    assert len(reloaded["matches"]["mission"]["results"]) == 1
    assert company.load_profile()["active_talent_id"] == "robin"
    assert {row["id"] for row in company.load_profile()["talents"]} == {"robin"}


def test_delete_candidate_removes_only_unshared_owned_documents(company):
    folder = company.root / "talent-files"
    folder.mkdir()
    unique, shared = "a" * 64 + ".pdf", "b" * 64 + ".pdf"
    (folder / unique).write_bytes(b"private cv")
    (folder / shared).write_bytes(b"shared document")
    state = _state(company)
    state["candidates"] = [
        {"id": "one", "name": "One", "cv": {"file": unique}, "dossier": {"file": shared}},
        {"id": "two", "name": "Two", "cv": {"file": shared}},
    ]
    state["matches"] = {"mission": {"results": [{"candidate": state["candidates"][0], "stage": "contacted"}]}}
    _save(company, state)
    handle_prospecting(company, "prospecting_delete_candidate", {"id": "one", "confirmed": True})
    assert not (folder / unique).exists()
    assert (folder / shared).read_bytes() == b"shared document"
    assert _state(company)["matches"]["mission"]["results"] == []
    with pytest.raises(CareerError, match="not found"):
        handle_prospecting(company, "prospecting_delete_candidate", {"id": "unknown", "confirmed": True})
    assert len(_state(company)["candidates"]) == 1


@pytest.mark.parametrize("link", [False, True])
def test_delete_candidate_never_deletes_files_outside_document_folder(company, link):
    protected = company.root / "keep.pdf"
    protected.write_bytes(b"keep")
    folder = company.root / "talent-files"
    folder.mkdir()
    filename = "c" * 64 + ".pdf" if link else "../keep.pdf"
    if link:
        (folder / filename).symlink_to(protected)
    state = _state(company)
    state["candidates"] = [{"id": "one", "name": "One", "cv": {"file": filename}}]
    _save(company, state)
    handle_prospecting(company, "prospecting_delete_candidate", {"id": "one", "confirmed": True})
    assert protected.read_bytes() == b"keep"


@pytest.mark.parametrize(("changes", "reason"), [
    ({"country": "OM"}, "country"), ({"country": "MA"}, "country"), ({"country": "TN"}, "country"), ({"country": "US"}, "country"),
    ({"country": "REMOTE", "location": "United States"}, "country"), ({"country": "", "location": ""}, "country"),
    ({"daily_rate_min": 649}, "rate"), ({"daily_rate_min": 600, "daily_rate_max": 800}, "rate"),
    ({"daily_rate_min": None, "daily_rate_max": None}, "rate_unknown"),
    ({"daily_rate_min": 650, "daily_rate_max": 700, "currency": "USD"}, "rate"),
    ({"remote": "hybrid"}, "work_mode"), ({"remote": "onsite"}, "work_mode"),
    ({"remote": "", "description": "Python SQL"}, "work_mode_unknown"),
    ({"remote": "remote", "description": "Hybrid: 2 days on site"}, "work_mode"),
    ({"remote": "remote", "description": "No remote allowed"}, "work_mode"),
    ({"posted_at": day(31)}, "date"), ({"posted_at": day(-1)}, "date"),
    ({"posted_at": "", "created_at": datetime.now(timezone.utc).timestamp()}, "date_unknown"),
    ({"posted_at": "2026-02-30"}, "date_unknown"), ({"title": "Sales executive"}, "role"),
])
def test_mandatory_constraints_reject_non_matching_offers(company, changes, reason):
    assert offer_rejection(mission(**changes), _state(company)["criteria"]) == reason


@pytest.mark.parametrize("country", ["FR", "BE", "CH", "LU"])
def test_all_selected_countries_and_exact_boundaries_are_accepted(company, country):
    assert not offer_rejection(mission(country=country, daily_rate_min=650, daily_rate_max=650, posted_at=day(30)), _state(company)["criteria"])


@pytest.mark.parametrize("mode", ["remote", "hybrid", "onsite", "any"])
def test_each_work_mode_is_persisted_and_enforced(company, mode):
    handle_prospecting(company, "prospecting_config", {"criteria": {"work_mode": mode}})
    criteria = _state(CareerStore(company.root))["criteria"]
    for actual in ("remote", "hybrid", "onsite"):
        assert bool(offer_rejection(mission(remote=actual), criteria)) == (mode not in {actual, "any"})


def test_publication_dates_are_evidence_not_import_time():
    assert publication_day("3 days ago") == day(3)
    assert publication_day("il y a 2 semaines") == day(14)
    assert publication_day("yesterday") == day(1)
    assert publication_day("2026-02-30") == ""
    assert publication_day("unknown") == ""


@pytest.mark.parametrize("saved_mode", ["missions", "profiles", "both"])
def test_search_collect_and_find_keep_company_scope_and_reject_noisy_provider_results(company, saved_mode):
    handle_prospecting(company, "prospecting_config", {"criteria": {"mode": saved_mode}})
    bad = [mission(id="bad-country", country="OM"), mission(id="bad-rate", daily_rate_min=500),
           mission(id="bad-mode", remote="hybrid"), mission(id="old", posted_at=day(31))]
    with patch("navin.webui.career_api._store", return_value=company), \
         patch("navin.career.prospecting._mission_source", return_value=[mission(), *bad]) as provider, \
         patch("navin.career.prospecting._candidates", side_effect=AssertionError("candidate search called")) as candidate_search, \
         patch("navin.career.collect._fetch_remotive", side_effect=AssertionError("solo collector called")):
        for action in ("search", "collect", "find"):
            result = handle_career_action(action, {"brief": "Python"})
            assert result["search"]["prospecting"]["criteria"]["countries"] == ["FR", "BE", "CH", "LU"]
            assert result["search"]["prospecting"]["criteria"]["mode"] == "missions"
            assert {s["source"].split(":")[0] for s in result["prospecting"]["last_run"]["sources"]} == {"missions"}
            assert result["prospecting"]["criteria"]["mode"] == saved_mode
    candidate_search.assert_not_called()
    assert {row["id"] for row in company.load_opportunities()} == {"allowed"}
    assert {call.args[1] for call in provider.call_args_list} == {"linkedin"}
    assert {call.args[2]["countries"][0] for call in provider.call_args_list} == {"FR", "BE", "CH", "LU"}
    assert any(status.get("rejected", {}).get("rate") for status in _state(company)["last_run"]["sources"])


def test_explicit_country_override_cannot_expand_company_scope(company):
    with pytest.raises(CareerError, match="company configuration"):
        collect(company, countries=["US"])


def test_candidate_search_uses_mission_and_selected_platforms_with_an_empty_pool(company):
    company.upsert_opportunities([mission(title="Cloud architect", stack=["Terraform"])])
    hits = [
        {"title": "Alex Cloud architect", "url": "https://linkedin.com/in/alex", "snippet": "Disponible Terraform France"},
        {"title": "Sam Cloud architect", "url": "https://malt.fr/profile/sam", "snippet": "Disponible Terraform France"},
        {"title": "Out of scope", "url": "https://linkedin.com/in/foreign", "snippet": "Available Terraform Oman"},
        {"title": "Not selected", "url": "https://freelancer.com/u/other", "snippet": "Available Terraform France"},
    ]
    with patch("navin.webui.career_api._store", return_value=company), patch("navin.career.prospecting._web", return_value=hits) as web, \
         patch("navin.career.prospecting._mission_source", side_effect=AssertionError("mission search called")):
        empty = handle_career_action("candidates", {"id": "allowed"})
        assert empty["total"] == 0 and empty["search_performed"] is False
        result = asyncio.run(CareerTool().execute(action="search_candidates", id="allowed"))
    assert web.call_count > 0
    assert all("Cloud architect" in call.args[2] and "France" in call.args[2] for call in web.call_args_list)
    assert any("site:linkedin.com/in/" in call.args[2] for call in web.call_args_list)
    assert any("site:malt.fr/profile/" in call.args[2] for call in web.call_args_list)
    assert all(call.args[2].count("site:") == 1 for call in web.call_args_list)
    matches = result["prospecting"]["matches"]["allowed"]["results"]
    assert {row["candidate"]["source"] for row in matches} == {"LinkedIn", "Malt"}
    assert len(matches) == 2
    assert result["prospecting"]["last_run"]["criteria"]["mode"] == "profiles"


@pytest.mark.parametrize("saved_mode", ["missions", "profiles", "both"])
def test_profile_search_actions_never_collect_offers(company, saved_mode):
    company.upsert_opportunities([mission()])
    handle_prospecting(company, "prospecting_config", {"criteria": {"mode": saved_mode}})
    with patch("navin.webui.career_api._store", return_value=company), \
         patch("navin.career.prospecting._mission_source", side_effect=AssertionError("offer source called")) as offers, \
         patch("navin.career.prospecting._candidates", return_value=[]) as profiles:
        for action, body in (("search_candidates", {}), ("prospecting_match", {"id": "allowed"})):
            result = handle_career_action(action, body)
            assert result["prospecting"]["last_run"]["criteria"]["mode"] == "profiles"
            assert {s["source"].split(":")[0] for s in result["prospecting"]["last_run"]["sources"]} == {"profiles"}
            assert result["prospecting"]["criteria"]["mode"] == saved_mode
    offers.assert_not_called()
    assert profiles.called


def test_candidate_country_fallback_never_uses_remote_as_a_country(company):
    criteria = _state(company)["criteria"]
    assert candidate_countries(criteria, mission(country="REMOTE")) == ["FR", "BE", "CH", "LU"]
    assert candidate_countries(criteria, mission(country="US")) == ["FR", "BE", "CH", "LU"]
    assert candidate_countries({**criteria, "profile_countries": ["BE"]}, mission()) == ["BE"]


def test_saved_matching_excludes_wrong_country_and_geography_only_matches(company):
    state = _state(company)
    state["candidates"] = [
        {"id": "foreign", "name": "Foreign", "headline": "Data Engineer", "skills": ["Python", "SQL"], "country": "OM"},
        {"id": "unrelated", "name": "Unrelated", "headline": "Sales", "skills": [], "country": "FR"},
    ]
    _match(company, state, mission())
    assert state["matches"]["allowed"]["results"] == []


def test_non_indexed_platform_is_reported_as_access_required(company):
    handle_prospecting(company, "prospecting_config", {"criteria": {"mode": "profiles", "platforms": ["apec"]}})
    with patch("navin.career.prospecting._web", side_effect=AssertionError("unconfigured search")):
        handle_prospecting(company, "prospecting_search", {})
    last = _state(company)["last_run"]
    assert last["status"] == "partial"
    assert last["sources"][0]["status"] == "access_required"


def test_existing_bad_offers_are_retained_but_not_recommended_or_alerted(company):
    from navin.career.watch import pending_alerts

    company.upsert_opportunities([mission(match_score=95), mission(id="old-bad", country="OM", match_score=99, url="https://example.com/jobs/old")])
    result = snapshot(company)
    rows = {row["id"]: row for row in result["opportunities"]}
    assert rows["old-bad"]["search_scope"] == {"eligible": False, "reason": "country"}
    assert result["kpis"]["opportunities"] == 1
    assert {row["id"] for row in pending_alerts(company)} == {"allowed"}
    status = format_agent_status(result)
    assert "outside search scope: country" in status and "search_candidates" in status and "650" in status


def test_imported_search_hits_cannot_bypass_company_constraints(company):
    with patch("navin.webui.career_api._store", return_value=company):
        result = handle_career_action("ingest", {"jobs": [mission(), mission(id="wrong", country="US")]})
    assert result["ingested"] == 1


@pytest.mark.parametrize("age", [0, 31, -1, True, 2.5])
def test_publication_limit_cannot_exceed_one_month(company, age):
    with pytest.raises(CareerError, match="1 and 30"):
        handle_prospecting(company, "prospecting_config", {"criteria": {"max_age_days": age}})
