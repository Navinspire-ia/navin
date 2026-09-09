# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Collect job sources: Remotive, ATS boards, LinkedIn public listings, keyed APIs, web, scrape."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from loguru import logger

from navin.career.employers import collect_employers
from navin.career.linkedin import search_linkedin_jobs
from navin.career.matching import job_is_relevant, score_opportunity
from navin.career.official import collect_official_apis
from navin.career.scrape_net import scrape_open_net
from navin.career.sources import (
    clean_job_text,
    countries_mentioned,
    expand_search_countries,
    host_of,
    html_to_text,
    is_closed_job_url,
    is_linkedin_url,
    is_listing_hit,
    official_search_pack,
    scrape_search_queries,
    stable_job_id,
    web_search_queries,
    web_search_run_pack,
)
from navin.career.stack import connector_status
from navin.career.store import CareerStore

_DDG_HTML = "https://html.duckduckgo.com/html/"
_DDG_RESULT_RE = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET_RE = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S)
_JOB_HINT = re.compile(
    r"job|hiring|career|engineer|developer|offre|emploi|mission|freelance|contract|recrut|vacanc|poste|data",
    re.I,
)
_NOISE_HOSTS = (
    "google.com",
    "bing.com",
    "duckduckgo.com",
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "pinterest.com",
    "wikipedia.org",
)

_REMOTIVE = "https://remotive.com/api/remote-jobs"
# Boards that only open in the browser or take a pasted import. LinkedIn is
# not in this set any more: its public guest listing is read live.
_KEYED_ONLY = {
    "malt",
    "indeed",
    "welcometothejungle",
    "apec",
    "free-work",
    "chooseyourboss",
}
_LIVE_OFF = "live-off"
_OFFICIAL_IDS = {
    "adzuna",
    "jooble",
    "usajobs",
    "francetravail",
    "france-travail",
    "official",
}
_OFFICIAL_APIS = ("adzuna", "jooble", "usajobs")
_LIVE_FAMILIES = {
    "remotive": {"remotive"},
    "ats": {"greenhouse", "lever", "ashby", "ats"},
    "official": set(_OFFICIAL_IDS),
    "web": {"web-job-search", "web-search", "web"},
    "scrape": {"scrape", "web-job-search"},
    "linkedin": {"linkedin", "linkedin-public"},
    "employers": {"employers", "esn", "company-pages"},
}


def _source_ids(profile: dict[str, Any]) -> set[str]:
    return {str(item).strip().lower() for item in (profile.get("source_ids") or []) if str(item).strip()}


_LINKEDIN_IDS = {"linkedin", "linkedin-public"}


def _want_family(profile: dict[str, Any], family: str) -> bool:
    """Which live collectors a profile turns on.

    No live pick means every family runs. Picking one or more live families
    narrows the run to those. LinkedIn is additive: ticking it never switches
    the other families off (older profiles listed it as a portal to open),
    and it runs by default like the rest.
    """
    ids = _source_ids(profile)
    live = ids - _KEYED_ONLY - {_LIVE_OFF} - _OFFICIAL_IDS - _LINKEDIN_IDS
    if family == "official":
        return bool(ids & _OFFICIAL_IDS)
    if family == "linkedin":
        if ids & _LINKEDIN_IDS:
            return True
        return _LIVE_OFF not in ids and not live
    if _LIVE_OFF in ids and not live:
        return False
    if not live:
        return True
    return bool(live & _LIVE_FAMILIES.get(family, set()))


def _want_official(profile: dict[str, Any], source_id: str) -> bool:
    return str(source_id or "").strip().lower() in _source_ids(profile)


def _search_families(
    profile: dict[str, Any],
    official_wanted: list[str],
    portals: list[dict[str, str]],
    queries: list[dict[str, str]],
) -> dict[str, bool]:
    del portals, queries  # the browser links no longer decide whether LinkedIn ran
    return {
        "remotive": _want_family(profile, "remotive"),
        "ats": _want_family(profile, "ats"),
        "web": _want_family(profile, "web"),
        "scrape": _want_family(profile, "scrape"),
        "official": bool(official_wanted),
        "linkedin": _want_family(profile, "linkedin"),
        "employers": profile.get("employer_watch", True) is not False and _want_family(profile, "employers"),
    }


def _search_note(families: dict[str, bool], official_wanted: list[str]) -> str:
    ran = [name for name, on in families.items() if on]
    skipped = [name for name, on in families.items() if not on]
    keyed_on = ", ".join(official_wanted) or "none"
    keyed_off = ", ".join(sid for sid in _OFFICIAL_APIS if sid not in official_wanted) or "none"
    parts = [f"Families that ran: {', '.join(ran) or 'none'}."]
    if skipped:
        parts.append(f"Families off: {', '.join(skipped)}.")
    parts.append(
        "Employer feeds read the ESN, consulting and agency job boards of the selected "
        "markets directly (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, "
        "Teamtailor, Personio, Workday or the careers page). "
        "Web search includes company career pages and public ATS boards. "
        "Scrape fetches open hosts only (never Indeed, Bayt). "
        "LinkedIn public listings are read from the guest job search "
        "(no login, one request per second, capped per run). Never Easy Apply."
    )
    parts.append(f"Keyed APIs on this run: {keyed_on}. Keyed APIs off: {keyed_off}.")
    parts.append(
        "API key values are never returned. Closed boards stay as official pages or a pasted import."
    )
    return " ".join(parts)


def _stable_id(source: str, url: str, title: str) -> str:
    return stable_job_id(source, url, title)


def _guess_track(title: str, profile_track: str) -> str:
    hay = (title or "").lower()
    if any(token in hay for token in ("freelance", "contract", "contractor", "mission")):
        return "freelance"
    if profile_track in {"freelance", "jobs"}:
        return profile_track
    return "jobs"


def _fetch_remotive(query: str) -> list[dict[str, Any]]:
    url = f"{_REMOTIVE}?search={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": "NavinCareer/1.0 (+https://navin.ai)"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("career remotive fetch skipped: {}", exc)
        return []
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in jobs[:40]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        company = str(item.get("company_name") or "").strip()
        link = str(item.get("url") or "").strip()
        if not title or not link:
            continue
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        rows.append(
            {
                "id": _stable_id("remotive", link, title),
                "source": "remotive",
                "title": title,
                "company": company,
                "location": str(item.get("candidate_required_location") or "Remote"),
                "country": "REMOTE",
                "compensation": None,
                "currency": "",
                "stack": [str(tag) for tag in tags[:8]],
                "seniority": "",
                "remote": "remote",
                "posted_at": str(item.get("publication_date") or ""),
                "contact": "",
                "description": html_to_text(str(item.get("description") or ""))[:4000],
                "url": link,
                "track": _guess_track(title, "jobs"),
                "stage": "discovered",
                "attribution": "Remotive",
            }
        )
    return rows


def _fetch_json(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NavinCareer/1.0 (+https://navin.ai)", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_ats_boards(slugs: list[str]) -> list[dict[str, Any]]:
    """Published Greenhouse / Lever / Ashby job boards only. Never a closed site."""
    rows: list[dict[str, Any]] = []
    for slug in [str(item).strip().lower() for item in slugs if str(item).strip()][:8]:
        if not slug.isalnum() and "-" not in slug and "_" not in slug:
            continue
        endpoints = (
            (f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(slug)}/jobs?content=true", "greenhouse"),
            (f"https://api.lever.co/v0/postings/{urllib.parse.quote(slug)}?mode=json", "lever"),
            (f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(slug)}", "ashby"),
        )
        for url, source in endpoints:
            try:
                payload = _fetch_json(url)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                logger.info("career {} {} skipped: {}", source, slug, exc)
                continue
            added = _normalize_ats(source, slug, payload)
            if added:
                rows.extend(added)
                break
    return rows


def _normalize_ats(source: str, slug: str, payload: Any) -> list[dict[str, Any]]:
    jobs: list[Any]
    if source == "lever" and isinstance(payload, list):
        jobs = payload
    elif isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
        jobs = payload["jobs"]
    else:
        return []
    out: list[dict[str, Any]] = []
    for item in jobs[:25]:
        if not isinstance(item, dict):
            continue
        if source == "greenhouse":
            title = str(item.get("title") or "").strip()
            loc_obj = item.get("location") if isinstance(item.get("location"), dict) else {}
            loc = str(loc_obj.get("name") or "")
            link = str(item.get("absolute_url") or "").strip()
            desc = str(item.get("content") or "")
        elif source == "lever":
            title = str(item.get("text") or "").strip()
            cats = item.get("categories") if isinstance(item.get("categories"), dict) else {}
            loc = str(cats.get("location") or "")
            link = str(item.get("hostedUrl") or item.get("applyUrl") or "").strip()
            desc = str(item.get("descriptionPlain") or item.get("description") or "")
        else:
            title = str(item.get("title") or "").strip()
            loc = str(item.get("location") or "")
            link = str(item.get("jobUrl") or "").strip()
            desc = str(item.get("descriptionPlain") or item.get("descriptionHtml") or "")
        if not title or not link or is_closed_job_url(link):
            continue
        out.append(
            {
                "id": _stable_id(source, link, title),
                "source": source,
                "title": title,
                "company": slug,
                "location": loc,
                "country": "",
                "description": html_to_text(desc)[:4000],
                "url": link,
                "track": _guess_track(title, "jobs"),
                "stage": "discovered",
                "remote": "remote" if "remote" in f"{title} {loc}".lower() else "",
            }
        )
    return out


def _strip_tags(value: str) -> str:
    return html_to_text(value or "")


def _unwrap_ddg(href: str) -> str:
    try:
        parsed = urllib.parse.urlparse(href if "//" in href else f"https://{href}")
        if parsed.path.startswith("/l/") and "uddg=" in (parsed.query or ""):
            target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                return urllib.parse.unquote(target)
    except ValueError:
        return href
    return href


def _is_noise_host(url: str) -> bool:
    host = host_of(url)
    return any(host == blocked or host.endswith("." + blocked) for blocked in _NOISE_HOSTS)


def _company_from_title(title: str) -> str:
    parts = re.split(r"\s[-|·•|/]+\s", title)
    if len(parts) < 2:
        return ""
    last = re.sub(r"\s+(on\s+)?LinkedIn.*$", "", parts[-1], flags=re.I).strip()
    return last[:80]


def _hit_to_job(hit: dict[str, str], *, country: str, track: str) -> dict[str, Any] | None:
    title = clean_job_text(str(hit.get("title") or "")).strip()
    url = str(hit.get("url") or "").strip()
    snippet = clean_job_text(str(hit.get("snippet") or "")).strip()
    if not title or not url or _is_noise_host(url):
        return None
    if is_listing_hit(title, url):
        return None
    if not _JOB_HINT.search(f"{title} {snippet}"):
        return None
    linkedin = is_linkedin_url(url)
    source = "linkedin" if linkedin else "web"
    return {
        "id": _stable_id(source, url, title),
        "source": source,
        "title": title[:180],
        "company": _company_from_title(title),
        "location": "",
        "country": country,
        "description": snippet[:4000],
        "url": url,
        "track": _guess_track(title, track),
        "stage": "discovered",
        "remote": "remote" if "remote" in f"{title} {snippet}".lower() else "",
        "ingest": "search_snippet",
        "attribution": "Web search",
    }


def _search_ddg_html(query: str, limit: int = 6) -> list[dict[str, str]]:
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    req = urllib.request.Request(
        _DDG_HTML,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; NavinCareer/1.0; +https://navin.ai)",
            "Accept": "text/html",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.info("career web search skipped: {}", exc)
        return []
    if "anomaly-modal" in body:
        return []
    links = _DDG_RESULT_RE.findall(body)
    snippets = [_strip_tags(item) for item in _DDG_SNIPPET_RE.findall(body)]
    rows: list[dict[str, str]] = []
    for index, (href, raw_title) in enumerate(links[:limit]):
        rows.append(
            {
                "title": _strip_tags(raw_title),
                "url": _unwrap_ddg(href),
                "snippet": snippets[index] if index < len(snippets) else "",
            }
        )
    return rows


def _search_ddgs(query: str, limit: int = 6) -> list[dict[str, str]]:
    try:
        from ddgs import DDGS
    except ImportError:
        return _search_ddg_html(query, limit)
    try:
        raw = DDGS(timeout=10).text(query, max_results=limit)
    except Exception as exc:
        logger.info("career ddgs skipped: {}", exc)
        return _search_ddg_html(query, limit)
    rows: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("href") or item.get("url") or ""),
                "snippet": str(item.get("body") or ""),
            }
        )
    return rows[:limit]


def search_web_hits(
    *,
    titles: list[str],
    countries: list[str],
    track: str,
    stack: list[str] | None = None,
    query: str = "",
) -> list[dict[str, Any]]:
    """Run the compact web pack. Snippets only. Never fetch closed job pages."""
    role = (query or "").strip() or (titles[0] if titles else "Data Engineer")
    pack = web_search_run_pack(
        titles=titles or [role],
        countries=countries or list(expand_search_countries()),
        track=track,
        stack=stack,
    )
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in pack:
        for hit in _search_ddgs(str(row.get("query") or role), 5):
            job = _hit_to_job(hit, country=str(row.get("country") or ""), track=track)
            if not job or job["id"] in seen:
                continue
            seen.add(job["id"])
            hits.append(job)
    return hits[:30]


def collect(
    store: CareerStore,
    *,
    query: str = "",
    track: str = "",
    countries: list[str] | None = None,
) -> dict[str, Any]:
    profile = store.load_profile()
    titles = [str(item) for item in (profile.get("titles") or [])]
    role = (query or "").strip() or (titles[0] if titles else "Data Engineer")
    search_title = role.split(",")[0].strip() or role
    score_profile = dict(profile)
    extra_titles = [search_title] if search_title else []
    score_profile["titles"] = extra_titles + [item for item in titles if item and item != search_title]
    wanted_track = (track or profile.get("track") or "freelance").strip().lower()
    if wanted_track == "both":
        wanted_track = "freelance"
    excluded = [str(item) for item in (profile.get("countries_excluded") or [])]
    picked = list(countries or []) + list(profile.get("countries_primary") or []) + list(
        profile.get("countries_secondary") or []
    )
    markets = expand_search_countries(
        picked,
        excluded,
        extra=countries_mentioned(role),
    )
    rows: list[dict[str, Any]] = []
    if _want_family(profile, "remotive"):
        rows.extend(_fetch_remotive(search_title))
    if _want_family(profile, "ats"):
        rows.extend(_fetch_ats_boards(list(profile.get("ats_boards") or [])))
    linkedin_net: dict[str, Any] = {"jobs": [], "walls": [], "requests": 0}
    if _want_family(profile, "linkedin"):
        try:
            linkedin_net = search_linkedin_jobs(
                titles=score_profile["titles"] or [search_title],
                countries=markets,
                track=wanted_track,
            )
        except Exception as exc:  # noqa: BLE001 - one wall must not sink the whole hunt
            logger.warning("career linkedin collect failed: {}", exc)
            linkedin_net = {"jobs": [], "walls": [f"LinkedIn: {exc}"], "requests": 0}
    linkedin_rows = list(linkedin_net.get("jobs") or [])
    rows.extend(linkedin_rows)
    official_wanted = [sid for sid in _OFFICIAL_APIS if _want_official(profile, sid)]
    keyed_rows = (
        collect_official_apis(
            search_title,
            markets,
            wanted_track,
            sources=official_wanted,
            secrets=store.load_secrets(),
        )
        if official_wanted
        else []
    )
    rows.extend(keyed_rows)
    linkedin_net: dict[str, Any] = {"jobs": [], "walls": [], "requests": 0}
    if _want_family(profile, "linkedin"):
        try:
            linkedin_net = search_linkedin_jobs(
                titles=score_profile["titles"] or [search_title],
                countries=markets,
                track=wanted_track,
            )
        except Exception as exc:  # noqa: BLE001 - LinkedIn must never break the other families
            logger.warning("career linkedin collect failed: {}", exc)
            linkedin_net = {"jobs": [], "walls": [f"LinkedIn error: {exc}"], "requests": 0}
    linkedin_rows = list(linkedin_net.get("jobs") or [])
    rows.extend(linkedin_rows)
    employer_net: dict[str, Any] = {"jobs": [], "checked": 0, "reports": [], "errors": []}
    if profile.get("employer_watch", True) is not False and _want_family(profile, "employers"):
        state = store.load_employer_state()
        try:
            employer_net = collect_employers(
                profile=score_profile,
                markets=markets,
                state=state,
                track=wanted_track,
            )
        except Exception as exc:  # noqa: BLE001 - a broken careers site must never break the run
            logger.warning("career employer feeds failed: {}", exc)
            employer_net = {"jobs": [], "checked": 0, "reports": [], "errors": [str(exc)[:120]]}
        store.save_employer_state(state)
    employer_rows = list(employer_net.get("jobs") or [])
    rows.extend(employer_rows)
    web_rows = (
        search_web_hits(
            titles=score_profile["titles"] or [search_title],
            countries=markets,
            track=wanted_track,
            stack=list(profile.get("stack") or []),
            query=search_title,
        )
        if _want_family(profile, "web")
        else []
    )
    rows.extend(web_rows)
    scrape_net = (
        scrape_open_net(
            titles=score_profile["titles"] or [search_title],
            countries=markets,
            track=wanted_track,
        )
        if _want_family(profile, "scrape")
        else {"jobs": []}
    )
    scrape_rows = list(scrape_net.get("jobs") or [])
    rows.extend(scrape_rows)
    kept: list[dict[str, Any]] = []
    for row in rows:
        url = str(row.get("url") or "")
        source = str(row.get("source") or "")
        if is_closed_job_url(url) and source not in {"web", "linkedin", "import"}:
            continue
        if is_listing_hit(str(row.get("title") or ""), url):
            continue
        if not job_is_relevant(row, score_profile):
            continue
        row["track"] = _guess_track(row.get("title") or "", wanted_track)
        row["description"] = html_to_text(str(row.get("description") or ""))[:4000]
        scored = score_opportunity(row, score_profile)
        row.update(scored)
        kept.append(row)
    before_ids = {str(row.get("id")) for row in store.load_opportunities() if row.get("id")}
    merged = store.upsert_opportunities(kept)
    after_ids = {str(row.get("id")) for row in merged if row.get("id")}
    added = len(after_ids - before_ids)
    rows = kept
    queries = web_search_queries(
        titles=score_profile["titles"] or [search_title],
        countries=markets,
        track=wanted_track,
        stack=list(profile.get("stack") or []),
    )
    portals = official_search_pack(
        search_title,
        markets,
        track=wanted_track,
        work_mode=str(profile.get("work_mode") or "remote"),
    )
    families = _search_families(profile, official_wanted, portals, queries)
    if families["scrape"]:
        queries = list(queries) + scrape_search_queries(
            titles=score_profile["titles"] or [search_title],
            countries=markets,
            track=wanted_track,
        )
    store.append_journal(
        {
            "kind": "search",
            "text": role,
            "count": len(rows),
            "added": added,
            "web": len(web_rows),
            "official": len(keyed_rows),
            "scrape": len(scrape_rows),
            "linkedin": len(linkedin_rows),
            "employers": len(employer_rows),
            "employers_checked": int(employer_net.get("checked") or 0),
            "families": dict(families),
        }
    )
    connectors = connector_status(store.load_secrets())
    return {
        "added": added,
        "scanned": len(rows),
        "web": len(web_rows),
        "official": len(keyed_rows),
        "scrape": len(scrape_rows),
        "linkedin": len(linkedin_rows),
        "linkedin_requests": int(linkedin_net.get("requests") or 0),
        "employers": len(employer_rows),
        "employers_checked": int(employer_net.get("checked") or 0),
        "employer_reports": list(employer_net.get("reports") or [])[:60],
        "query": role,
        "queries": queries,
        "portals": portals,
        "families": families,
        "connectors": connectors,
        "walls": list(scrape_net.get("walls") or []) + list(linkedin_net.get("walls") or []),
        "note": _search_note(families, official_wanted),
    }
