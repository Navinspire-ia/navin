# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Employer feeds: directory, ATS detection, feed parsing, resolution, collection. No network."""

from __future__ import annotations

import json
import urllib.error
from typing import Any

from navin.career import employers as emp
from navin.career.store import CareerStore


def _house(name: str = "Acme Conseil", site: str = "acme.example", markets: tuple[str, ...] = ("FR",), **extra: Any) -> dict[str, Any]:
    row = {"id": emp._slug(name), "name": name, "kind": "esn", "site": site, "markets": list(markets), "user": False}
    row.update(extra)
    return row


# --- directory -------------------------------------------------------------


def test_directory_covers_selected_markets_and_sorts_esn_first() -> None:
    rows = emp.directory(["FR"])
    assert len(rows) >= 40
    assert all("FR" in row["markets"] for row in rows)
    ids = [row["id"] for row in rows]
    assert "devoteam" in ids and "hays" in ids
    kinds = [row["kind"] for row in rows]
    assert kinds.index("agency") > kinds.index("esn")
    assert len({row["id"] for row in emp.EMPLOYERS}) == len(emp.EMPLOYERS)


def test_directory_seed_points_to_a_known_feed_kind() -> None:
    seeded = [row for row in emp.EMPLOYERS if row.get("seed")]
    assert len(seeded) >= 20
    for row in seeded:
        assert row["seed"]["ats"] in emp.FEEDS
        assert row["seed"]["slug"]
        if row["seed"]["ats"] == "workday":
            assert row["seed"]["host"].endswith(".myworkdayjobs.com") and row["seed"]["site"]


def test_user_employers_are_normalized_from_profile() -> None:
    profile = {
        "employers": [
            {"name": "Ma Boite", "url": "https://boards.greenhouse.io/maboite", "country": "FR"},
            {"url": "https://jobs.example.com/careers"},
            "garbage",
        ]
    }
    rows = emp.user_employers(profile)
    assert [row["name"] for row in rows] == ["Ma Boite", "jobs.example.com"]
    assert rows[0]["markets"] == ["FR"] and rows[0]["careers_url"].endswith("/maboite")
    assert rows[0]["user"] is True and rows[0]["kind"] == "esn"


# --- ATS detection ---------------------------------------------------------


def test_detect_ats_from_urls() -> None:
    cases = {
        "https://boards.greenhouse.io/pendo": ("greenhouse", "pendo"),
        "https://job-boards.greenhouse.io/pendo/jobs/1": ("greenhouse", "pendo"),
        "https://jobs.lever.co/palantir/abc": ("lever", "palantir"),
        "https://jobs.eu.lever.co/acme": ("lever", "acme"),
        "https://jobs.ashbyhq.com/openai": ("ashby", "openai"),
        "https://careers.smartrecruiters.com/Devoteam": ("smartrecruiters", "Devoteam"),
        "https://jobs.smartrecruiters.com/wavestone1/744000145453689": ("smartrecruiters", "wavestone1"),
        "https://apply.workable.com/acme/j/ABC/": ("workable", "acme"),
        "https://acme.recruitee.com/o/dev": ("recruitee", "acme"),
        "https://consortgroup.teamtailor.com/jobs": ("teamtailor", "consortgroup"),
        "https://xebia.jobs.personio.de/job/1": ("personio", "xebia"),
    }
    for url, (ats, slug) in cases.items():
        found = emp.detect_ats(url)
        assert found and found["ats"] == ats and found["slug"] == slug, url


def test_detect_ats_workday_tenant_host_site_and_locale_prefix() -> None:
    found = emp.detect_ats("https://kainos.wd3.myworkdayjobs.com/en-US/Kainos/job/Senior-IT_JR_1")
    assert found == {"ats": "workday", "slug": "kainos", "host": "kainos.wd3.myworkdayjobs.com", "site": "Kainos"}
    found = emp.detect_ats("https://zuehlke.wd3.myworkdayjobs.com/zuhlke-careers")
    assert found and found["site"] == "zuhlke-careers"
    found = emp.detect_ats("https://neosoft.wd3.myworkdayjobs.com/fr-FR/neo-soft/details/Data-Engineer_R1")
    assert found and found["site"] == "neo-soft"
    assert emp.detect_ats("https://diconium.wd3.myworkdayjobs.com/en/job/Berlin/Dev_1") is None
    assert emp.detect_ats("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/jobs") is None
    assert emp.detect_ats("https://www.example.com/careers") is None
    assert emp.detect_ats("https://www.workable.com/resources") is None


def test_detect_ats_in_html_finds_embed_and_links() -> None:
    html = '<a href="https://jobs.lever.co/acme?x=1">Jobs</a>'
    assert emp.detect_ats_in_html(html) == {"ats": "lever", "slug": "acme"}
    html = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeco"></script>'
    assert emp.detect_ats_in_html(html) == {"ats": "greenhouse", "slug": "acmeco"}
    assert emp.detect_ats_in_html("<p>no ats here</p>") is None


def test_country_from_location_reads_ats_shapes() -> None:
    cases = {
        "Puteaux, IDF, fr": "FR",
        "Madrid, MD, es": "ES",
        "Bangalore, India": "IN",
        "Noida, UP, in": "IN",
        "Austin, Texas, USA": "US",
        "Schlieren": "CH",
        "Genève": "CH",
        "Bruxelles": "BE",
        "Néosoft Niort": "FR",
        "2 Locations": "",
        "Remote - Europe": "REMOTE",
        "": "",
    }
    for location, iso in cases.items():
        assert emp._country_from_location(location) == iso, location


def test_board_matches_name_rejects_strangers() -> None:
    assert emp.board_matches_name({"slug": "kainos", "host": "kainos.wd3.myworkdayjobs.com", "site": "Kainos"}, "Kainos")
    assert emp.board_matches_name({"slug": "zuehlke", "host": "zuehlke.wd3.myworkdayjobs.com", "site": "zuhlke-careers"}, "Zuhlke")
    assert emp.board_matches_name({"slug": "accenture", "host": "accenture.wd103.myworkdayjobs.com", "site": "AvanadeCareers"}, "Avanade")
    assert emp.board_matches_name({"slug": "wavestone1"}, "Wavestone")
    assert not emp.board_matches_name({"slug": "cambiumlearning", "host": "cambiumlearning.wd1.myworkdayjobs.com", "site": "Opportunities"}, "Capgemini")
    assert not emp.board_matches_name({"slug": "career"}, "Theodo")
    assert not emp.board_matches_name({"slug": "www"}, "adesso")
    assert emp.board_matches_name({"slug": "SopraSteria1"}, "Sopra Steria")
    assert emp.board_matches_name({"slug": "Sia"}, "Sia Partners")
    assert not emp.board_matches_name({"slug": "nttglobaldatacenters", "host": "nttglobaldatacenters.wd501.myworkdayjobs.com", "site": "External"}, "NTT Data")
    assert not emp.board_matches_name({"slug": "workday", "host": "workday.wd5.myworkdayjobs.com", "site": "Workday"}, "EY")
    assert not emp.board_matches_name({"slug": "asmglobal", "host": "asmglobal.wd1.myworkdayjobs.com", "site": "careers"}, "Insight Global")
    assert not emp.board_matches_name({"slug": "gbmc", "host": "gbmc.wd1.myworkdayjobs.com", "site": "gbmc"}, "GBM")
    assert emp.board_matches_name({"slug": "cgi", "host": "cgi.wd3.myworkdayjobs.com", "site": "CGI"}, "CGI")
    assert not emp.board_matches_name({"slug": "my-applications"}, "Tietoevry")


# --- feeds -----------------------------------------------------------------


def _fetcher(routes: dict[str, bytes | Exception]):
    calls: list[str] = []

    def fetch(url: str, **_: Any) -> bytes:
        calls.append(url)
        for prefix, payload in routes.items():
            if url.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise urllib.error.HTTPError(url, 404, "nf", None, None)  # type: ignore[arg-type]

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_feed_greenhouse_maps_rows() -> None:
    payload = {
        "jobs": [
            {
                "title": "Data Engineer (H/F)",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
                "location": {"name": "Paris, France"},
                "content": "<p>Build &amp; run pipelines</p>",
                "updated_at": "2026-08-19T18:00:47-04:00",
                "departments": [{"name": "Data"}],
            },
            {"title": "", "absolute_url": "https://job-boards.greenhouse.io/acme", "location": {"name": ""}},
        ]
    }
    fetch = _fetcher({"https://boards-api.greenhouse.io/v1/boards/acme/jobs": json.dumps(payload).encode()})
    rows = emp._feed_greenhouse(_house(), {"ats": "greenhouse", "slug": "acme"}, fetch, "")
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "greenhouse" and row["company"] == "Acme Conseil"
    assert row["country"] == "FR" and row["posted_at"] == "2026-08-19"
    assert row["ingest"] == "employer_feed" and row["employer_id"] == "acme-conseil"
    assert "pipelines" in row["description"] and row["department"] == "Data"


def test_feed_smartrecruiters_passes_query_and_reads_country() -> None:
    payload = {
        "content": [
            {
                "id": "744000146933930",
                "name": "Product Owner IA",
                "releasedDate": "2026-09-02T09:27:14.774Z",
                "location": {"city": "Madrid", "region": "MD", "country": "es", "remote": False},
                "department": {"label": "Data"},
                "typeOfEmployment": {"label": "Full-time"},
            }
        ]
    }
    fetch = _fetcher({"https://api.smartrecruiters.com/v1/companies/Devoteam/postings": json.dumps(payload).encode()})
    rows = emp._feed_smartrecruiters(_house("Devoteam"), {"ats": "smartrecruiters", "slug": "Devoteam", "_markets": ["FR", "ES"]}, fetch, "data engineer")
    assert len(fetch.calls) == 2  # one call per wanted market, deduplicated by posting id
    assert "q=data+engineer" in fetch.calls[0] and "country=fr" in fetch.calls[0] and "country=es" in fetch.calls[1]
    assert len(rows) == 1
    assert rows[0]["country"] == "ES" and rows[0]["url"] == "https://jobs.smartrecruiters.com/Devoteam/744000146933930"
    assert rows[0]["employment_type"] == "Full-time" and rows[0]["posted_at"] == "2026-09-02"


def test_feed_workable_recruitee_lever_ashby_shapes() -> None:
    workable = {"results": [{"title": "Dev Python", "shortcode": "AB12", "location": {"city": "Lyon", "countryCode": "FR"}, "published": "2026-08-01T00:00:00Z", "remote": True}]}
    recruitee = {"offers": [{"title": "Data Analyst", "careers_url": "https://acme.recruitee.com/o/data", "location": "Brussels", "country_code": "BE", "status": "published", "published_at": "2026-08-02"}]}
    lever = [{"text": "Cloud Architect", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {"location": "Geneva", "commitment": "Contract"}, "createdAt": 1756684800000, "workplaceType": "remote"}]
    ashby = {"jobs": [{"title": "SRE", "jobUrl": "https://jobs.ashbyhq.com/acme/1", "location": "Amsterdam", "publishedAt": "2026-08-03", "isListed": True, "isRemote": False}]}
    fetch = _fetcher(
        {
            "https://apply.workable.com/api/v3/accounts/acme/jobs": json.dumps(workable).encode(),
            "https://acme.recruitee.com/api/offers/": json.dumps(recruitee).encode(),
            "https://api.lever.co/v0/postings/acme": json.dumps(lever).encode(),
            "https://api.ashbyhq.com/posting-api/job-board/acme": json.dumps(ashby).encode(),
        }
    )
    res = {"slug": "acme"}
    w = emp._feed_workable(_house(), res, fetch, "python")[0]
    assert w["url"] == "https://apply.workable.com/acme/j/AB12/" and w["country"] == "FR" and w["remote"] == "remote"
    r = emp._feed_recruitee(_house(), res, fetch, "")[0]
    assert r["country"] == "BE" and r["source"] == "recruitee"
    lv = emp._feed_lever(_house(), res, fetch, "")[0]
    assert lv["posted_at"] == "2025-09-01" and lv["employment_type"] == "Contract" and lv["remote"] == "remote"
    a = emp._feed_ashby(_house(), res, fetch, "")[0]
    assert a["title"] == "SRE" and a["location"] == "Amsterdam"


def test_feed_lever_falls_back_to_eu_host() -> None:
    lever = [{"text": "Dev", "hostedUrl": "https://jobs.eu.lever.co/acme/1", "categories": {}}]
    fetch = _fetcher(
        {
            "https://api.lever.co/v0/postings/acme": urllib.error.HTTPError("u", 404, "nf", None, None),  # type: ignore[arg-type]
            "https://api.eu.lever.co/v0/postings/acme": json.dumps(lever).encode(),
        }
    )
    rows = emp._feed_lever(_house(), {"slug": "acme"}, fetch, "")
    assert len(rows) == 1 and len(fetch.calls) == 2


def test_feed_teamtailor_rss_and_personio_xml() -> None:
    rss = b"""<?xml version="1.0"?><rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel>
    <item><title>Consultant Data (F/H)</title><link>https://consortgroup.teamtailor.com/jobs/1-consultant</link>
    <description>&lt;p&gt;Mission Paris&lt;/p&gt;</description><pubDate>Mon, 01 Sep 2026 10:00:00 +0000</pubDate>
    <tt:location>Paris</tt:location><tt:department>Data</tt:department></item></channel></rss>"""
    xml = b"""<?xml version="1.0"?><workzag-jobs><position><id>77</id><office>Munich</office><department>Tech</department>
    <name>Data Engineer</name><employmentType>permanent</employmentType><createdAt>2026-08-20T00:00:00</createdAt>
    <jobDescriptions><jobDescription><name>Role</name><value>&lt;p&gt;Spark&lt;/p&gt;</value></jobDescription></jobDescriptions></position></workzag-jobs>"""
    fetch = _fetcher({"https://consortgroup.teamtailor.com/jobs.rss": rss, "https://xebia.jobs.personio.de/xml": xml})
    tt = emp._feed_teamtailor(_house("Consort Group"), {"slug": "consortgroup"}, fetch, "")[0]
    assert tt["posted_at"] == "2026-09-01" and tt["location"] == "Paris" and tt["country"] == "FR" and tt["department"] == "Data"
    pe = emp._feed_personio(_house("Xebia", markets=("NL", "FR")), {"slug": "xebia"}, fetch, "")[0]
    assert pe["url"] == "https://xebia.jobs.personio.de/job/77" and pe["location"] == "Munich" and pe["country"] == "DE"
    assert "Spark" in pe["description"] and pe["posted_at"] == "2026-08-20"


def test_feed_workday_posts_search_text_and_builds_urls() -> None:
    payload = {"total": 1, "jobPostings": [{"title": "Data Engineer", "externalPath": "/job/Paris/Data-Engineer_JR1", "locationsText": "Paris, France", "postedOn": "Posted Today"}]}
    seen: dict[str, Any] = {}

    def fetch(url: str, **kwargs: Any) -> bytes:
        seen["url"] = url
        seen.update(kwargs)
        return json.dumps(payload).encode()

    res = {"ats": "workday", "slug": "kainos", "host": "kainos.wd3.myworkdayjobs.com", "site": "Kainos"}
    rows = emp._feed_workday(_house("Kainos", markets=("GB", "FR")), res, fetch, "data engineer")
    assert seen["url"] == "https://kainos.wd3.myworkdayjobs.com/wday/cxs/kainos/Kainos/jobs"
    assert seen["json_body"]["searchText"] == "data engineer" and seen["prime"] == "https://kainos.wd3.myworkdayjobs.com/Kainos"
    assert rows[0]["url"] == "https://kainos.wd3.myworkdayjobs.com/Kainos/job/Paris/Data-Engineer_JR1" and rows[0]["country"] == "FR"


def test_feed_careers_page_prefers_jsonld_then_job_links() -> None:
    jsonld = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Ingenieur DevOps",
        "url": "https://acme.example/jobs/devops",
        "datePosted": "2026-08-30",
        "employmentType": "FULL_TIME",
        "jobLocation": {"@type": "Place", "address": {"addressLocality": "Nantes", "addressCountry": "FR"}},
        "description": "<p>Kubernetes</p>",
    }
    page = f'<html><script type="application/ld+json">{json.dumps(jsonld)}</script></html>'.encode()
    fetch = _fetcher({"https://acme.example/careers": page})
    rows = emp._feed_careers_page(_house(), {"ats": "careers-page", "careers_url": "https://acme.example/careers"}, fetch, "")
    assert rows[0]["title"] == "Ingenieur DevOps" and rows[0]["country"] == "FR" and rows[0]["source"] == "careers-page"

    links = b"""<html><a href="/offre/123-data-engineer-hf">Data Engineer H/F</a>
    <a href="/about">About us</a><a href="https://other.example/x">Data Engineer elsewhere</a>
    <a href="/job/456">Consultant Cloud senior</a></html>"""
    fetch = _fetcher({"https://acme.example/careers": links})
    rows = emp._feed_careers_page(_house(), {"ats": "careers-page", "careers_url": "https://acme.example/careers"}, fetch, "")
    assert [row["url"] for row in rows] == ["https://acme.example/offre/123-data-engineer-hf", "https://acme.example/job/456"]


# --- resolution ------------------------------------------------------------


def test_resolve_employer_uses_seed_without_network() -> None:
    def boom(*_: Any, **__: Any) -> bytes:
        raise AssertionError("no network expected")

    house = _house("Devoteam", seed={"ats": "smartrecruiters", "slug": "Devoteam"})
    res = emp.resolve_employer(house, fetch=boom, landing=boom, search=None)
    assert res["ats"] == "smartrecruiters" and res["slug"] == "Devoteam"


def test_resolve_employer_follows_redirect_to_ats() -> None:
    def landing(url: str) -> str:
        if url.endswith("/careers"):
            return "https://jobs.lever.co/acme"
        raise urllib.error.HTTPError(url, 404, "nf", None, None)  # type: ignore[arg-type]

    res = emp.resolve_employer(_house(), fetch=_fetcher({}), landing=landing, search=None)
    assert res == {"ats": "lever", "slug": "acme", "careers_url": "https://jobs.lever.co/acme"}


def test_resolve_employer_reads_html_markers_then_search_then_page_only() -> None:
    html = b'<html><iframe src="https://boards.greenhouse.io/embed/job_board?for=acmeco"></iframe></html>'
    res = emp.resolve_employer(
        _house(),
        fetch=_fetcher({"https://acme.example/careers": html}),
        landing=lambda url: url,
        search=None,
    )
    assert res["ats"] == "greenhouse" and res["slug"] == "acmeco"

    plain = b"<html><h1>Rejoignez-nous</h1><p>Nos offres arrivent bientot</p></html>"
    searches: list[str] = []

    def search(query: str, count: int) -> str:
        searches.append(query)
        if "myworkdayjobs" in query:
            return "1. Acme Conseil careers\n   https://acmeconseil.wd3.myworkdayjobs.com/AcmeCareers\n"
        return "No results"

    res = emp.resolve_employer(
        _house(),
        fetch=_fetcher({"https://acme.example/careers": plain}),
        landing=lambda url: url,
        search=search,
    )
    assert res["ats"] == "workday" and res["site"] == "AcmeCareers" and len(searches) == 1

    res = emp.resolve_employer(
        _house(),
        fetch=_fetcher({"https://acme.example/careers": plain}),
        landing=lambda url: url,
        search=lambda q, c: "1. Stranger\n   https://stranger.wd1.myworkdayjobs.com/Jobs\n",
    )
    assert res["ats"] == "careers-page" and res["careers_url"] == "https://acme.example/careers"


def test_resolve_employer_reports_error_when_nothing_answers() -> None:
    def landing(url: str) -> str:
        raise urllib.error.URLError("dns")

    res = emp.resolve_employer(_house(), fetch=_fetcher({}), landing=landing, search=None)
    assert res["ats"] == "" and "dns" in res["error"]


# --- collection ------------------------------------------------------------


def test_pick_employers_splits_read_and_resolve_budgets() -> None:
    profile = {"countries_primary": ["BE"], "employers": [{"name": "Ma Boite", "url": "https://boards.greenhouse.io/maboite"}]}
    state = {"devoteam": {"checked_at": 100.0}, "nrb": {"checked_at": 50.0}}
    readable, unresolved = emp.pick_employers(profile, ["FR", "BE"], state, limit=500, resolve_budget=500, now=1000.0)
    # Seeds were applied for free: Devoteam is readable, NRB (no seed) waits for resolution.
    assert state["devoteam"]["ats"] == "smartrecruiters" and state["devoteam"]["seeded"] is True
    assert all(emp.FEEDS.get(state[row["id"]]["ats"]) for row in readable)
    assert unresolved[0]["name"] == "Ma Boite"  # user rows first
    ids = [row["id"] for row in unresolved]
    be_count = sum(1 for row in emp.directory(["FR", "BE"]) if "BE" in row["markets"] and not row.get("seed"))
    assert all("BE" in row["markets"] for row in unresolved[1 : be_count + 1])
    # Never-checked houses first, then the stalest of the checked ones.
    assert ids.index("nrb") > ids.index("cegeka")
    readable, unresolved = emp.pick_employers(profile, ["FR", "BE"], state, limit=5, resolve_budget=3, now=1000.0)
    assert len(readable) == 5 and len(unresolved) == 3

    # A house that failed recently is not retried; after the retry window it is.
    state["nrb"] = {"ats": "", "resolved_at": 900.0, "error": "HTTP 404"}
    _, unresolved = emp.pick_employers(profile, ["FR", "BE"], state, limit=5, resolve_budget=500, now=1000.0)
    assert "nrb" not in [row["id"] for row in unresolved]
    _, unresolved = emp.pick_employers(profile, ["FR", "BE"], state, limit=5, resolve_budget=500, now=900.0 + emp.RESOLVE_RETRY_S + 1)
    assert "nrb" in [row["id"] for row in unresolved]


def test_collect_employers_resolves_reads_and_updates_state(tmp_path) -> None:
    profile = {"titles": ["Data Engineer"], "countries_primary": ["FR"], "employers": [{"name": "Ma Boite", "url": "https://boards.greenhouse.io/maboite", "country": "FR"}]}
    payload = {
        "jobs": [
            {"title": "Data Engineer Spark", "absolute_url": "https://job-boards.greenhouse.io/maboite/jobs/1", "location": {"name": "Paris"}},
            {"title": "Office Manager", "absolute_url": "https://job-boards.greenhouse.io/maboite/jobs/2", "location": {"name": "Paris"}},
            {"title": "Data Engineer Bangalore", "absolute_url": "https://job-boards.greenhouse.io/maboite/jobs/3", "location": {"name": "Bangalore, India"}},
        ]
    }
    fetch = _fetcher({"https://boards-api.greenhouse.io/v1/boards/maboite/jobs": json.dumps(payload).encode()})

    def landing(url: str) -> str:
        raise urllib.error.HTTPError(url, 404, "nf", None, None)  # type: ignore[arg-type]

    state: dict[str, Any] = {}
    # limit=0: no seeded feed is read, only the houses resolved in this run (user row first).
    out = emp.collect_employers(profile=profile, markets=["FR"], state=state, track="freelance", fetch=fetch, landing=landing, search=None, now=1000.0, limit=0, resolve_budget=3)
    assert out["resolved_now"] == 3 and out["checked"] == 1 and out["picked"] == 3
    titles = [row["title"] for row in out["jobs"]]
    # Light title filter drops the office manager; market gate drops the Bangalore posting.
    assert titles == ["Data Engineer Spark"]
    assert out["jobs"][0]["track"] == "freelance" and out["jobs"][0]["company"] == "Ma Boite"
    entry = state["ma-boite"]
    assert entry["ats"] == "greenhouse" and entry["last_count"] == 1 and entry["checked_at"] == 1000.0
    failed = [v for k, v in state.items() if k != "ma-boite" and not v.get("ats")]
    assert failed and all(v.get("error") for v in failed)

    # Second pass: cached resolution is reused and the recently failed houses are not probed again.
    failed_ids = {k for k, v in state.items() if not v.get("ats")}
    out2 = emp.collect_employers(profile=profile, markets=["FR"], state=state, track="freelance", fetch=fetch, landing=landing, search=None, now=2000.0, limit=1, resolve_budget=3)
    assert state["ma-boite"]["resolved_at"] == 1000.0 and state["ma-boite"]["checked_at"] == 2000.0
    assert out2["checked"] >= 1 and all(state[k]["resolved_at"] == 1000.0 for k in failed_ids)

    store = CareerStore(tmp_path)
    store.save_employer_state(state)
    assert store.load_employer_state()["ma-boite"]["slug"] == "maboite"
    summary = emp.employer_summary(state, ["FR"])
    assert summary["directory"] >= 40 and "resolved" in summary


def test_profile_normalizes_employer_fields(tmp_path) -> None:
    store = CareerStore(tmp_path)
    profile = store.save_profile(
        {
            "employer_watch": False,
            "employers": [{"name": "Ma Boite", "url": "jobs.maboite.fr/careers", "country": "fr"}, {"name": ""}, "x"],
            "employers_hidden": ["Devoteam", "hays"],
        }
    )
    assert profile["employer_watch"] is False
    assert profile["employers"] == [{"name": "Ma Boite", "url": "https://jobs.maboite.fr/careers", "kind": "esn", "markets": ["FR"]}]
    assert profile["employers_hidden"] == ["devoteam", "hays"]
    assert store.load_profile()["employer_watch"] is False
    assert CareerStore(tmp_path / "fresh").load_profile()["employer_watch"] is True
