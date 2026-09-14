# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from navin.career import mci
from navin.career.errors import CareerError
from navin.career.mission_platforms import (
    indexed_missions,
    mission_platform_catalog,
    platform_by_id,
)
from navin.career.normalize import enrich_facts
from navin.career.prospecting import _mission_source, _state, handle_prospecting
from navin.career.prospecting_catalog import default_mission_sources
from navin.career.public_missions import enrich_detail, parse_cards
from navin.career.scope import offer_rejection
from navin.career.store import CareerStore
from navin.tenders.sources import PRIORITY_MISSION_RFP_SOURCES, web_search_queries


def mci_record(**fields):
    return {"id": "recPublic1", "createdTime": "2026-09-14T08:00:00Z", "fields": {
        "Titre": "Consultant Data Engineer", "Etat": "Ouverte aux candidatures",
        "Description": "Mission Python et SQL", "TJM ": "1000", "TJM affichage Softr": "TJM HT max 750 €",
        "Lieu": "Bruxelles", "Affichage date de publication": "Publiée le : 14/09/2026",
        "Début mission affichage Softr": "Démarrage idéalement le : 01/10/2026",
        "Télétravail": "2j télétravail / semaine", "Durée": "6 mois", "Expérience min": "8",
        "Compétence principale attendue": "Python", "Critere_1": "SQL", **fields}}


def public_mci_page():
    block = {"id": "c" * 36, "collection": {"dataSource": {"id": "d" * 36, "airtable": {"tableName": "Annonces"}},
             "sortOptions": [{"field": "Date / heure de création"}], "mockData": [mci_record()]}}
    return f'<body data-appid="{"a" * 36}" data-pageid="{"b" * 36}"><script>var softrBlocks = {json.dumps([block])};</script>'


def test_mci_uses_published_consultant_ceiling_and_actual_geography():
    row, = mci.parse_records({"records": [mci_record()]})
    assert row["daily_rate_max"] == 750 and row["daily_rate_min"] is None
    assert row["country"] == "BE" and row["company"] == ""
    assert row["remote"] == "hybrid" and row["duration_months"] == 6
    assert row["experience_years_min"] == 8
    assert row["posted_at"] == "2026-09-14" and row["start_date"] == "2026-10-01" and row["deadline"] == ""
    assert row["stack"] == ["Python", "SQL"]
    assert "recordId=recPublic1" in row["url"]
    assert "TJM " not in row and "fields" not in row


def test_mci_discards_closed_missions_and_does_not_fill_unknown_facts():
    assert mci.parse_records({"records": [mci_record(Etat="Pourvue")]}) == []
    row, = mci.parse_records({"records": [mci_record(**{"TJM affichage Softr": "À discuter", "Lieu": "Confidentiel", "Télétravail": "Télétravail aménageable à discuter lors des entretiens"})]})
    assert row["daily_rate_max"] is None and row["country"] == "" and row["remote"] == ""
    with pytest.raises(CareerError):
        mci.parse_records({"message": "login"})
    with pytest.raises(CareerError):
        mci.listing_endpoint("<h1>Please sign in</h1>")


def test_mci_reads_public_pages_with_bounded_pagination_and_shared_cache(monkeypatch):
    monkeypatch.setattr(mci, "_cache", (0, []))
    record = mci_record()
    second = {**record, "id": "recPublic2"}
    with patch.object(mci, "_fetch", side_effect=[public_mci_page(), json.dumps({"records": [record], "offset": "next"}),
                                                json.dumps({"records": [second]})]) as fetch:
        rows = mci.search_missions(titles=[], countries=["BE"])
        assert len(rows) == 2 and fetch.call_count == 3
        assert fetch.call_args.args[1]["pagingOption"]["offset"] == "next"
        assert all(call.args[0].startswith(mci.BASE) for call in fetch.call_args_list)
        rows[0]["stack"].append("must not leak")
        assert "must not leak" not in mci.search_missions(titles=[], countries=[])[0]["stack"]
        assert mci.search_missions(titles=[], countries=["FR"]) == []
        assert fetch.call_count == 3
    assert mci.search_missions(titles=[], countries=[], track="jobs") == []


def test_prounity_preserves_published_date_and_client_separately_from_assignment_period():
    source = platform_by_id("prounity")
    url = "https://www.pro-unity.com/job/" + "a" * 36 + "/"
    cards = f'<div class="job"><h3 class="job__title">Data Engineer</h3><p class="job__pubdate">14/09/2026</p><span data-filter-company>Actual client</span><a href="{url}">Read more</a></div>'
    row, = parse_cards(cards, source)
    row = enrich_detail(row, '<article class="pji_job"><div class="job__infobar">01/10/2026 - 31/03/2027 Bruxelles, Belgium</div><p>Mission Python, 6 months</p></article>')
    assert row["company"] == "Actual client" and row["country"] == "BE"
    assert row["posted_at"] == "2026-09-14" and row["start_date"] == "2026-10-01"
    assert row["end_date"] == "2027-03-31" and row["deadline"] == ""
    with pytest.raises(CareerError):
        parse_cards('<a href="/freelance-missions/">Home</a>', source)


def test_freelancers_lu_reads_structured_detail_and_skips_permanent_contracts():
    source = platform_by_id("freelancers_lu")
    url = "https://freelancers.lu/fr/missions/" + "a" * 36
    row, = parse_cards(f'<h2><a href="{url}">Data Engineer</a></h2>', source)
    posting = {"@type": "JobPosting", "title": "Data Engineer", "description": "CDI Python",
               "datePosted": "2026-09-14", "employmentType": "FULL_TIME", "url": url,
               "hiringOrganization": {"name": "Acme"}, "jobLocation": {"address": {"addressCountry": "LU"}}}
    detail = enrich_detail(row, f'<script type="application/ld+json">{json.dumps(posting)}</script>')
    assert detail["track"] == "jobs" and detail["country"] == "LU"


def test_indexed_missions_reject_directories_profiles_foreign_hosts_and_cdi():
    source = platform_by_id("freelancermap_jobs")
    base = {"title": "Data engineer", "snippet": "Freelance Python France"}
    hits = [{**base, "url": url} for url in ["https://www.freelancermap.com/project/data-engineer-123",
            "https://www.freelancermap.com/project/", "https://www.freelancermap.com/profile/123",
            "https://freelancermap.com.evil.test/project/123", "https://www.freelancermap.com/"]]
    hits.append({**base, "url": "https://www.freelancermap.com/project/data-engineer-cdi", "snippet": "Vos missions en CDI Python France"})
    row, = indexed_missions(source, hits)
    assert row["country"] == "" and row["posted_at"] == "" and row["company"] == ""
    assert row["ingest"] == "search_snippet" and row["opportunity_kind"] == "freelance"


def test_client_fixed_budget_never_becomes_a_day_rate():
    source = platform_by_id("freelancer_au")
    row, = indexed_missions(source, [{"title": "Python data dashboard", "url": "https://www.freelancer.com.au/projects/python/data-dashboard",
                                    "snippet": "Client in Australia. Fixed price: 1200 AUD. Build a dashboard."}])
    assert row["budget"] == 1200 and row["currency"] == "AUD" and row["price_model"] == "fixed"
    assert enrich_facts(row)["daily_rate_max"] is None and row["salary_max"] is None
    assert row["compensation"] is None


def test_public_discovery_uses_available_provider_without_api_key(tmp_path):
    store = CareerStore(tmp_path / "career")
    criteria = _state(store)["criteria"]
    with patch("navin.career.prospecting._web", return_value=[]) as search:
        _mission_source(store, "lehibou", criteria)
    assert search.call_args.args[1] == "public_web"
    assert "site:lehibou.com" in search.call_args.args[2]


def test_account_only_sources_do_not_consume_capacity_or_appear_connected(tmp_path):
    store = CareerStore(tmp_path / "career")
    accounts = [source["id"] for source in mission_platform_catalog() if source["mode"] == "account"]
    handle_prospecting(store, "prospecting_config", {"criteria": {"mode": "missions", "roles": ["Data Engineer"],
                       "countries": [], "sources": accounts, "track": "freelance"}})
    with patch("navin.career.prospecting._mission_source") as search:
        handle_prospecting(store, "prospecting_search", {})
    search.assert_not_called()
    assert all(row["status"] == "access_required" for row in _state(store)["last_run"]["sources"])
    assert not set(accounts).intersection(default_mission_sources("freelance"))


def test_curated_first_batch_and_freelance_scope():
    primary = {row["id"] for row in mission_platform_catalog() if row["priority"] == 1} | {"freework"}
    assert len(primary) + len(PRIORITY_MISSION_RFP_SOURCES) == 26
    assert "remotive" not in default_mission_sources("freelance")
    assert "freelancer_au" in default_mission_sources("freelance", ["AU"])
    assert "mon-consultant-independant" not in default_mission_sources("freelance", ["AU"])
    criteria = {"track": "freelance", "max_age_days": 30}
    today = datetime.now(timezone.utc).date().isoformat()
    assert offer_rejection({"title": "Data Engineer", "description": "Vos missions en CDI", "track": "freelance", "posted_at": today}, criteria) == "track"
    assert offer_rejection({"title": "Data Engineer", "track": "freelance", "posted_at": today, "deadline": "2020-01-01"}, criteria) == "expired"


def test_tender_queries_target_each_selected_official_source():
    selected = ["dubai-esupply", "abu-dhabi-adgpg", "sap-discovery"]
    queries = web_search_queries(countries=["AE"], crafts=["Data platform"], source_ids=selected)
    assert {row["source_id"] for row in queries} == set(selected)
    assert all("site:" in row["query"] and "Data platform" in row["query"] for row in queries)
    assert web_search_queries(countries=["AE"], crafts=["AI"], source_ids=["world-bank"]) == []
