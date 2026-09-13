# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Company sourcing inside Career: isolated providers and evidence-based matches.

SerpApi: https://serpapi.com/google-jobs-api and /organic-results
Brave: https://api-dashboard.search.brave.com/app/documentation/web-search
Search snippets are discovery evidence, never confirmation of availability.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from filelock import FileLock, Timeout

from navin.career.errors import CareerError
from navin.career.prospecting_catalog import INDEXED_MISSIONS, mission_catalog, platform_catalog
from navin.career.scope import (
    candidate_countries,
    country_in_scope,
    offer_rejection,
    publication_day,
    result_countries,
)
from navin.career.search_plan import plan_search, role_scope, role_shares
from navin.career.sources import (
    MARKETS,
    html_to_text,
    normalize_job_url,
    stable_job_id,
)
from navin.career.store import CareerStore, _atomic_write, _read_json, _split

KEYS = {"serpapi": "CAREER_SERPAPI_KEY", "brave": "CAREER_BRAVE_KEY"}
SOURCES = {s["id"] for s in mission_catalog()}
STAGES = {"discovered", "contacted", "qualified", "submitted", "interview", "placed", "contracted", "rejected"}
MAX_TASKS = 32


class SourcingRows(list):
    """Search results with counts of evidence rejected before normalization."""

    def __init__(self):
        super().__init__()
        self.rejected: dict[str, int] = {}

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


def _state(store: CareerStore) -> dict[str, Any]:
    raw = _read_json(store.root / "prospecting.json", {})
    profile = store.load_profile()
    pool = {c["id"]: c for c in raw.get("candidates", [])}
    for talent in profile.get("talents", []):
        if not talent.get("name"):
            continue
        cid = "internal:" + str(talent["id"])
        previous = pool.get(cid, {})
        pool[cid] = {**previous, "id": cid, "name": talent["name"], "headline": talent.get("headline", ""),
                     "snippet": talent.get("master_cv", ""), "skills": talent.get("stack", []),
                     "email": talent.get("email") or previous.get("email", ""),
                     "phone": talent.get("phone") or previous.get("phone", ""),
                     "country": talent.get("residence_country", ""), "city": "", "url": "",
                     "signal": "unknown", "source": "Vivier interne", "observed_at": previous.get("observed_at") or time.time()}
    defaults = {
        "mode": "both", "domain": "", "roles": profile.get("titles", []), "role_priorities": {}, "role_skills": {},
        "skills": profile.get("stack", []), "countries": profile.get("countries_primary", []),
        "city": "", "track": profile.get("track", "both"), "internal_talents": True,
        "work_mode": profile.get("work_mode", "any"), "max_age_days": 30,
        "sources": [source["id"] for source in mission_catalog() if not source["provider"]],
        "platforms": [source["id"] for source in platform_catalog() if source["indexed_profiles"]], "signal_only": True,
        "company": profile.get("company", {}), "sale_rate": 0, "min_rate": profile.get("min_rate", 0), "margin_percent": 0,
        "buy_rate_max": 0, "salary_max": 0, "currency": "EUR", "profile_domain": "",
        "profile_roles": raw.get("criteria", {}).get("roles", profile.get("titles", [])),
        "profile_skills": [], "profile_countries": [], "profile_city": "", "signature": "",
        "auto_contact": False, "auto_present": False, "min_score": 70, "max_per_day": 5,
        "candidate_cc": [], "client_cc": [], "require_dossier": False,
        "daily_search": True,
    }
    return {"criteria": {**defaults, **raw.get("criteria", {})},
            "candidates": list(pool.values()), "matches": raw.get("matches", {}),
            "health": raw.get("health", {}), "last_run": raw.get("last_run", {}),
            "checks": raw.get("checks", {}), "cursor": raw.get("cursor", 0), "automation": raw.get("automation", {}),
            "runs": raw.get("runs", []), "search_schedule": raw.get("search_schedule", {})}


def prospecting_snapshot(store: CareerStore) -> dict[str, Any]:
    state = _state(store)
    return {**state, "platform_catalog": platform_catalog(), "mission_catalog": mission_catalog(),
            "keys": {key: store.has_secret(name) for key, name in KEYS.items()}}


def _save(store: CareerStore, state: dict[str, Any]) -> None:
    _atomic_write(store.root / "prospecting.json", state)


def _clean(value: Any, limit: int = 500) -> str:
    return html_to_text(str(value or "")).strip()[:limit].replace("\u2014", "-").replace("\u2013", "-")


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def _has(text: str, term: str) -> bool:
    return bool(term.strip()) and bool(re.search(r"(?<!\w)" + re.escape(_fold(term.strip())) + r"(?!\w)", _fold(text)))


def _role_evidence(text: str) -> str:
    text = _fold(text)
    text = re.sub(r"\b(?:intelligence artificielle|artificial intelligence|ia)\b", "ai", text)
    for word, canonical in {"director": "directeur", "directrice": "directeur", "direction": "directeur",
                            "projects": "projet", "project": "projet", "projets": "projet"}.items():
        text = re.sub(r"\b" + word + r"\b", canonical, text)
    return text


def _url(value: Any) -> str:
    raw = str(value or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return parsed.scheme + "://" + normalize_job_url(raw)


def _request(url: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    # Hosts are constants owned by the adapters. Never follow a redirect with credentials.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=15) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise CareerError("Response exceeds the size limit.")
        payload = json.loads(data)
        if not isinstance(payload, dict) or payload.get("error"):
            raise CareerError("The provider rejected the search. Check the account and quota.")
        return payload
    except urllib.error.HTTPError as exc:
        # Exception strings may contain the API key in the query URL.
        raise CareerError(f"Provider returned HTTP {exc.code}. Check access and quota.") from None
    except (OSError, ValueError):
        raise CareerError("Provider unavailable or invalid response. Other sources will continue.") from None


def _web(store: CareerStore, provider: str, query: str) -> list[dict[str, Any]]:
    if provider == "public_web":
        from navin.career.collect import _search_ddgs

        return _search_ddgs(query, limit=20, strict=True)
    key = store.get_secret(KEYS[provider])
    if not key:
        raise CareerError("Configure an API key.")
    if provider == "serpapi":
        payload = _request("https://serpapi.com/search.json", {"engine": "google", "api_key": key, "q": query})
        rows = payload.get("organic_results")
        if rows is None and "search_information" not in payload:
            raise CareerError("Unexpected Google response format.")
        return [{"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet"), "posted_at": publication_day(r.get("date"))}
                for r in (rows or []) if isinstance(r, dict)]
    payload = _request("https://api.search.brave.com/res/v1/web/search", {"q": query[:600], "count": 20},
                       {"X-Subscription-Token": key})
    if "web" not in payload and "query" not in payload:
        raise CareerError("Unexpected Brave response format.")
    return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description"), "posted_at": publication_day(r.get("age"))}
            for r in (payload.get("web") or {}).get("results", []) if isinstance(r, dict)]


def _query(criteria: dict[str, Any]) -> str:
    countries = [str((MARKETS.get(c) or {}).get("label") or c) for c in criteria["countries"]]
    terms = " ".join([*criteria["roles"], criteria["domain"], *criteria["skills"], str(criteria.get("query") or "")])[:350]
    mode = {"remote": '"full remote"', "hybrid": "hybrid", "onsite": '"on site"'}.get(criteria.get("work_mode"), "")
    return " ".join([terms, criteria["city"], *countries, mode]).strip()


def _mission_source(store: CareerStore, source: str, criteria: dict[str, Any]) -> list[dict[str, Any]]:
    query = _query(criteria)
    track = criteria["track"]
    if source == "freelancescope":
        from navin.career.freelancescope import search_missions

        if track == "jobs":
            return []
        return search_missions(" ".join(criteria["roles"]) or criteria["domain"] or " ".join(criteria["skills"]))
    from navin.career.feeds import FEED_IDS, collect_feeds

    if source in FEED_IDS:
        result = collect_feeds(titles=criteria["roles"] or [criteria["domain"]], countries=criteria["countries"],
                               track=track, sources=[source], max_requests=2)
        if result.get("walls") and not result.get("jobs"):
            raise CareerError("Feed temporarily unavailable.")
        return result.get("jobs", [])
    indexed = next((s for s in INDEXED_MISSIONS if s[0] == source), None)
    if indexed:
        from navin.career.collect import _hit_to_job

        hits = None
        for provider in KEYS:
            if store.has_secret(KEYS[provider]):
                try:
                    hits = _web(store, provider, f"site:{indexed[2]} {query} (mission OR job OR emploi OR freelance)")
                    break
                except CareerError:
                    continue
        if hits is None:
            raise CareerError("Configure SerpApi or Brave to search indexed public listings.")
        rows = []
        for hit in hits:
            host = urllib.parse.urlsplit(str(hit.get("url") or "")).hostname or ""
            if host != indexed[2] and not host.endswith("." + indexed[2]):
                continue
            row = _hit_to_job(hit, country="", track=track)
            if row:
                rows.append({**row, "source": source, "attribution": indexed[1] + " - recherche publique indexée"})
        return rows
    if source in {"linkedin", "freework", "collective"}:
        from navin.career.collective import search_collective_jobs
        from navin.career.freework import search_freework_jobs
        from navin.career.linkedin import search_linkedin_jobs

        getter = {"linkedin": search_linkedin_jobs, "freework": search_freework_jobs,
                  "collective": search_collective_jobs}[source]
        role = " ".join(criteria["roles"]) or criteria["domain"] or " ".join(criteria["skills"])
        pages = 1 if source == "linkedin" else 2
        result = getter(titles=[role], countries=criteria["countries"], track=track,
                        max_requests=pages, max_pages=pages, **({"max_details": 0} if source == "linkedin" else {}))
        if result.get("walls") and not result.get("jobs"):
            raise CareerError("Access temporarily unavailable. Retrying after cooldown.")
        return result.get("jobs", [])
    if source == "remotive":
        from navin.career.collect import _fetch_remotive

        return _fetch_remotive(" ".join(criteria["roles"]) or criteria["domain"], strict=True)
    if source in {"brave_jobs", "public_jobs"}:
        hits = _web(store, "brave" if source == "brave_jobs" else "public_web", query + " (emploi OR mission OR hiring OR project)")
        from navin.career.collect import _hit_to_job

        return [row for hit in hits if (row := _hit_to_job(
            hit, country="", track=track))]
    key = store.get_secret(KEYS["serpapi"])
    if not key:
        raise CareerError("Configure a SerpApi key for Google Jobs.")
    payload = _request("https://serpapi.com/search.json", {"engine": "google_jobs", "api_key": key, "q": query})
    if "jobs_results" not in payload and "search_information" not in payload:
        raise CareerError("Unexpected Google Jobs response format.")
    rows = []
    for job in payload.get("jobs_results", []):
        if not isinstance(job, dict):
            continue
        url = next((_url(item.get("link")) for item in job.get("apply_options", [])
                    if isinstance(item, dict) and _url(item.get("link"))), "") or _url(job.get("share_link"))
        if not url or not job.get("title"):
            continue
        rows.append({"id": stable_job_id("google_jobs", url, job["title"]), "source": "google_jobs",
                     "title": _clean(job["title"], 180), "company": _clean(job.get("company_name")),
                     "location": _clean(job.get("location")), "description": _clean(job.get("description"), 4000),
                     "url": url, "stage": "discovered", "track": track,
                     "posted_at": publication_day((job.get("detected_extensions") or {}).get("posted_at")),
                     "attribution": "Google Jobs via SerpApi", "stack": []})
    return rows


def _candidates(store: CareerStore, provider: str, criteria: dict[str, Any]) -> list[dict[str, Any]]:
    platforms = [p for p in platform_catalog() if p["id"] in criteria["platforms"] and p["indexed_profiles"]]
    if not platforms:
        return []
    sites = " OR ".join("site:" + p["domain"] + p["profile_path"] for p in platforms)
    signals = ("disponible" if set(criteria["countries"]) & {"FR", "BE", "CH", "LU", "MA", "TN"} else "available") if criteria["signal_only"] else ""
    # A profile rarely repeats the vacancy title's gender suffix, all its
    # skills or its work-mode wording. Apply business constraints to evidence.
    roles = [re.sub(r"\b(?:F\s*/\s*H|H\s*/\s*F|M\s*/\s*F)\b", "", role, flags=re.I).strip()
             for role in criteria["roles"]]
    roles = [" ".join(word for word in re.findall(r"[\w+#.-]+", role)
                      if word.casefold() not in {"de", "du", "des", "en", "et", "le", "la", "les", "the", "of", "and"})
             for role in roles]
    query = _query({**criteria, "roles": roles, "skills": [] if roles else criteria["skills"][:3],
                    "domain": "" if roles else criteria["domain"], "work_mode": "any"})
    site_query = f"({sites})" if len(platforms) > 1 else sites
    hits = _web(store, provider, f"{site_query} {query} {signals}")
    rows = SourcingRows()
    for hit in hits:
        url = _url(hit.get("url"))
        parsed = urllib.parse.urlsplit(url)
        platform = next((p for p in platforms if (parsed.hostname == p["domain"] or
                        (parsed.hostname or "").endswith("." + p["domain"])) and
                        p["profile_path"].lower() in parsed.path.lower()), None)
        if not platform:
            rows.reject("platform")
            continue
        if len(str(hit.get("title") or "")) > 300:
            # Some search layouts accidentally concatenate several cards.
            # Their combined geography/availability is not one person's data.
            rows.reject("evidence")
            continue
        title, snippet = _clean(hit.get("title"), 180), _clean(hit.get("snippet"), 1500)
        evidence = f"{title} {snippet}"
        signal = any(_has(evidence, term) for term in ("open to work", "opentowork", "disponible", "available", "recherche emploi", "recherche un emploi"))
        if any(_has(evidence, term) for term in ("not available", "not open to work", "indisponible", "pas disponible", "non disponible")):
            rows.reject("unavailable")
            continue
        if criteria["signal_only"] and not signal:
            rows.reject("availability_unknown")
            continue
        from navin.career.mail_settings import email_address

        addresses = re.findall(r"[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", evidence)
        contact = ""
        if len(set(addresses)) == 1:
            try:
                contact = email_address(addresses[0])
            except CareerError:
                pass
        from navin.career.normalize import enrich_facts

        facts = enrich_facts({"title": title, "description": snippet, "track": criteria["track"]})
        geography = {"headline": title, "snippet": snippet}
        if not country_in_scope(geography, criteria["countries"]):
            rows.reject("country")
            continue
        observed_countries = result_countries(geography)
        country = next(iter(observed_countries)) if len(observed_countries) == 1 else ""
        phones = re.findall(r"(?<!\w)\+[1-9][\d .()-]{7,20}\d", evidence)
        phone = re.sub(r"[^+\d]", "", phones[0]) if len(phones) == 1 else ""
        rows.append({"id": hashlib.sha256(url.encode()).hexdigest()[:20], "name": title, "headline": title,
                     "daily_rate": facts.get("daily_rate_min"), "salary": facts.get("salary_min"), "currency": facts.get("currency", ""),
                     "email": contact, "email_source": url if contact else "", "phone": phone,
                     "url": url, "source": platform["name"], "snippet": snippet,
                     "signal": "declared" if signal else "unknown", "observed_at": time.time(),
                     "country": country,
                     "city": criteria["city"] if criteria["city"] and _has(evidence, criteria["city"]) else "",
                     "skills": [skill for skill in criteria["skills"] if _has(evidence, skill)], "provider": provider})
    return rows


def score_candidate(candidate: dict[str, Any], offer: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    text = " ".join([candidate.get("headline", ""), candidate.get("snippet", ""),
                     " ".join(candidate.get("skills", []))])
    if candidate.get("document_text"):
        text = candidate["document_text"]
    skills = _split(offer.get("stack")) or criteria["skills"]
    title = _clean(offer.get("title")) or " ".join(criteria["roles"])
    words = list(dict.fromkeys(w for w in re.findall(r"[\w+#.]+", _role_evidence(title)) if (len(w) > 2 or w == "ai") and w not in
             {"the", "and", "les", "des", "pour", "avec", "job", "emploi", "mission", "freelance"}))
    countries = candidate_countries(criteria, offer)
    city = criteria["profile_city"] or criteria["city"]
    parts = []
    matched = [s for s in skills if _has(text, s)]
    parts.append({"label": "Skills", "points": round(60 * len(matched) / len(skills)) if skills else 0,
                  "max": 60, "detail": ", ".join(matched) or "Not documented"})
    title_hits = sum(_has(_role_evidence(text), word) for word in words)
    parts.append({"label": "Role", "points": round(20 * title_hits / len(words)) if words else 0,
                  "max": 20, "detail": title})
    geo_text = text + " " + str(candidate.get("country", "")) + " " + str(candidate.get("city", ""))
    country_ok = any(candidate.get("country") == c or _has(geo_text, str((MARKETS.get(c) or {}).get("label") or c)) for c in countries)
    parts.append({"label": "Country", "points": 10 if country_ok else 0, "max": 10,
                  "detail": ", ".join(countries) if country_ok else "To be confirmed"})
    city_ok = bool(city) and _has(geo_text, city)
    parts.append({"label": "City", "points": 10 if city_ok else 0, "max": 10,
                  "detail": city if city_ok else "To be confirmed" if city else "Not requested"})
    possible = (60 if skills else 0) + (20 if words else 0) + (10 if countries else 0) + (10 if city else 0)
    score = round(100 * sum(p["points"] for p in parts) / possible) if possible else 0
    return {"candidate": candidate, "score": score, "reasons": parts,
            "missing_skills": [s for s in skills if s not in matched]}


def _match(store: CareerStore, state: dict[str, Any], offer: dict[str, Any]) -> None:
    criteria = state["criteria"]
    pool = [c for c in state["candidates"] if criteria["internal_talents"] or not c["id"].startswith("internal:")]
    previous = {row["candidate"]["id"]: row for row in state["matches"].get(offer["id"], {}).get("results", [])}
    results = []
    for candidate in pool:
        if not country_in_scope(candidate, candidate_countries(criteria, offer)):
            continue
        same_currency = candidate.get("currency") == criteria["currency"]
        if same_currency and ((criteria["buy_rate_max"] and float(candidate.get("daily_rate") or 0) > criteria["buy_rate_max"])
                              or (criteria["salary_max"] and float(candidate.get("salary") or 0) > criteria["salary_max"])):
            continue
        scored = score_candidate(candidate, offer, criteria)
        if not any(part["points"] > 0 if part["label"] == "Skills" else part["points"] >= part["max"] / 2
                   for part in scored["reasons"] if part["label"] in {"Skills", "Role"}):
            continue
        old = previous.pop(candidate["id"], {})
        results.append({**old, **scored, "stage": old.get("stage", "discovered"),
                        "interest": old.get("interest", "unknown"), "availability": old.get("availability", "unknown"),
                        "note": old.get("note", ""), "contract": old.get("contract", ""),
                        "confirmed_at": old.get("confirmed_at"), "history": old.get("history", [])})
    # Retain followed dossiers even when the candidate disappears from search.
    results.extend(row for row in previous.values() if row.get("stage") != "discovered" or row.get("confirmed_at"))
    results.sort(key=lambda row: -row["score"])
    state["matches"][offer["id"]] = {"searched_at": time.time(), "results": results,
                                     "criteria": criteria.copy()}


def _search(store: CareerStore, state: dict[str, Any], offer_id: str = "", *, request: dict[str, Any] | None = None) -> None:
    criteria = state["criteria"].copy()
    request = request or {}
    criteria["query"] = _clean(request.get("query") or request.get("brief"), 350)
    if request.get("mode") in {"missions", "profiles", "both"}:
        criteria["mode"] = request["mode"]
    if request.get("track"):
        if request["track"] not in {"freelance", "jobs", "both"}:
            raise CareerError("Invalid search track.", status=400)
        criteria["track"] = request["track"]
    if request.get("countries"):
        selected = list(dict.fromkeys(c.upper() for c in _split(request["countries"])))
        if criteria["countries"] and any(c not in criteria["countries"] for c in selected):
            raise CareerError("Search countries must remain within the company configuration.", status=400)
        criteria["countries"] = selected
    offer = store.get_opportunity(offer_id) if offer_id else None
    profile_countries = candidate_countries(criteria, offer)
    if offer:
        criteria["mode"] = "profiles"
        criteria["roles"] = [offer["title"]]
        criteria["skills"] = _split(offer.get("stack"))
    if not any(criteria[key] for key in ("roles", "domain", "skills", "profile_roles", "profile_domain", "profile_skills")):
        raise CareerError("Enter a role, domain or skills.", status=400)
    tasks = []
    statuses = []
    now = time.time()
    countries = criteria["countries"] or [""]
    priorities = criteria.get("role_priorities", {}) if not offer else {}

    def add_task(side, source, country, scoped, role, weight, platform=""):
        from navin.improvement.search import career_search

        task_id = f"{side}:{source}:{country}:" + hashlib.sha256(role.encode()).hexdigest()[:12]
        if platform:
            task_id += ":" + platform
        fn = _mission_source if side == "missions" else _candidates
        tasks.append({"id": task_id, "group": f"{side}:{role}", "role": role, "weight": weight, "criteria": scoped, "platform": platform,
                      "run": (lambda: fn(store, source, scoped)) if offer else
                          lambda: career_search(store, side, source, scoped, criteria, fn)})

    if not offer and criteria["mode"] in {"both", "missions"}:
        unavailable = set()
        for source in mission_catalog():
            if source["id"] not in criteria["sources"] or not source["provider"]:
                continue
            configured = any(store.has_secret(key) for key in KEYS.values()) if source["mode"] == "indexed" else store.has_secret(KEYS[source["provider"]])
            if not configured:
                unavailable.add(source["id"])
                statuses.append({"source": "missions:" + source["id"], "status": "not_configured", "count": 0,
                                 "message": "Configure a search API key for this source."})
        for role, weight in role_shares(criteria["roles"] or [""], priorities).items():
            if not weight:
                continue
            for country in countries:
                scoped = role_scope({**criteria, "countries": [country] if country else []}, role)
                for source in criteria["sources"]:
                    if source in unavailable:
                        continue
                    markets = next(s["markets"] for s in mission_catalog() if s["id"] == source)
                    served = None if "International" in markets or "Europe" in markets else set(markets.split())
                    if served and country and country not in served:
                        continue
                    add_task("missions", source, country, scoped, role, weight)
    if offer or criteria["mode"] in {"both", "profiles"}:
        for platform in platform_catalog():
            if platform["id"] in criteria["platforms"] and not platform["indexed_profiles"]:
                statuses.append({"source": "profiles:" + platform["id"], "status": "access_required", "count": 0,
                                 "message": f"{platform['name']}: candidate database access is required; public profile search is unavailable."})
        explicit_profile = criteria.get("autofill", {}).get("version") == 1
        providers = [provider for provider in KEYS if store.has_secret(KEYS[provider])] + ["public_web"]
        roles = criteria["roles"] if offer else criteria["profile_roles"]
        for role, weight in role_shares(roles or [""], priorities).items():
            if not weight:
                continue
            for country in (profile_countries or [""]):
                scoped = {**criteria, "countries": [country] if country else [], "roles": roles,
                          "domain": criteria["profile_domain"] if explicit_profile else criteria["profile_domain"] or criteria["domain"],
                          "skills": criteria["skills"] if offer else (criteria["profile_skills"] if explicit_profile else criteria["profile_skills"] or criteria["skills"]),
                          "city": criteria["profile_city"] or criteria["city"]}
                # Per-offer matching uses the offer's complete skill requirements.
                scoped = {**scoped, "domain": ""} if offer else role_scope(scoped, role)
                for platform in platform_catalog():
                    if not platform["indexed_profiles"] or platform["id"] not in criteria["platforms"]:
                        continue
                    for provider in providers:
                        add_task("profiles", provider, country, {**scoped, "platforms": [platform["id"]]}, role, weight, platform["id"])
    if not tasks and criteria["roles"] and priorities and not any(role_shares(criteria["roles"], priorities).values()):
        raise CareerError("All selected roles are paused. Set a positive search priority for at least one role.", status=400)
    task_countries = list(dict.fromkeys(countries + (profile_countries or [""])))
    priority = {"linkedin": 0, "google_jobs": 1, "serpapi": 2, "brave": 3, "public_web": 4, "public_jobs": 4, "brave_jobs": 5}
    if criteria["track"] != "jobs":
        # Public freelance boards expose rates, dates and work modes in their
        # listings. Search those before snippets that cannot verify the floor.
        priority.update({"freework": -3, "collective": -2, "freelancescope": -1})
    platform_order = {p["id"]: index for index, p in enumerate(platform_catalog())}
    all_tasks = sorted(tasks, key=lambda task: (
        priority.get(task["id"].split(":")[1], 5), platform_order.get(task["platform"], 0),
        task_countries.index(task["id"].split(":")[2])))
    ready_tasks = []
    for task in all_tasks:
        health = state["health"].get(task["id"], {})
        if health.get("retry_at", 0) > now:
            statuses.append({"source": task["id"], "role": task["role"], "platform": task["platform"],
                             "status": "cooldown", "count": 0, "error_code": health.get("error_code", ""),
                             "retry_at": health["retry_at"]})
        else:
            ready_tasks.append(task)
    all_tasks = ready_tasks
    planned, schedule = plan_search(all_tasks, {} if offer else state["search_schedule"], MAX_TASKS)
    if not offer:
        state["search_schedule"] = schedule
    deferred = max(0, len(all_tasks) - len(planned))
    candidates = {c["id"]: c for c in state["candidates"]}
    previous_candidates = set(candidates)
    found_offers = []
    before = {r["id"] for r in store.load_opportunities()}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for task in planned:
            task_id, fn = task["id"], task["run"]
            health = state["health"].get(task_id, {})
            if health.get("retry_at", 0) > now:
                statuses.append({"source": task_id, "role": task["role"], "status": "cooldown", "count": 0,
                                 "platform": task["platform"], "error_code": health.get("error_code", ""),
                                 "message": "Source is cooling down. Other searches will continue.", "retry_at": health["retry_at"]})
                continue
            futures[executor.submit(fn)] = task
        for future in as_completed(futures):
            task = futures[future]
            task_id = task["id"]
            try:
                rows = future.result()
                rejected: dict[str, int] = dict(getattr(rows, "rejected", {}))
                if task_id.startswith("missions:"):
                    # Reuse Career's normal deduplication and offer store.
                    from navin.career.normalize import contracts_track, enrich_facts

                    rows = [{**row, "company_sourced": True} for row in rows]
                    for row in rows:
                        observed_countries = result_countries(row)
                        if len(observed_countries) == 1:
                            row["country"] = next(iter(observed_countries))
                        row["posted_at"] = publication_day(row.get("posted_at") or row.get("published_at"))
                        enrich_facts(row)
                    for row in rows:
                        row["track"] = contracts_track(row.get("contracts", []), row.get("track", ""))
                    accepted = []
                    for row in rows:
                        reason = offer_rejection(row, task["criteria"])
                        if reason:
                            rejected[reason] = rejected.get(reason, 0) + 1
                            continue
                        observed_countries = result_countries(row)
                        if len(observed_countries) == 1:
                            row["country"] = next(iter(observed_countries))
                        accepted.append(row)
                    rows = accepted
                    store.upsert_opportunities(rows)
                    found_offers.extend(rows)
                else:
                    accepted = [row for row in rows if country_in_scope(row, task["criteria"]["countries"])]
                    if len(rows) > len(accepted):
                        rejected["country"] = rejected.get("country", 0) + len(rows) - len(accepted)
                    rows = accepted
                    for row in rows:
                        previous = candidates.get(row["id"], {})
                        if previous.get("email") and not row.get("email"):
                            row["email"] = previous["email"]
                            row["email_source"] = previous.get("email_source", "")
                        candidates[row["id"]] = {**previous, **row}
                status = {"source": task_id, "status": "ok", "count": len(rows), "rejected": rejected,
                          "checked_at": time.time(), "retry_at": 0}
            except Exception as exc:  # noqa: BLE001 - isolate each external provider
                failures = state["health"].get(task_id, {}).get("failures", 0) + 1
                status = {"source": task_id, "status": "error", "count": 0, "failures": failures,
                          "error_code": "rate_limited" if isinstance(exc, CareerError) and "limited by the provider" in str(exc) else "source_unavailable",
                          "message": str(exc) if isinstance(exc, CareerError) else "Source unavailable. Other sources will continue.",
                          "checked_at": time.time(), "retry_at": time.time() + min(3600, 60 * 2 ** min(failures, 6))}
            status["role"] = task["role"]
            status["platform"] = task["platform"]
            state["health"][task_id] = status
            statuses.append(status)
            state["candidates"] = list(candidates.values())
            _save(store, state)
    state["candidates"] = list(candidates.values())
    if offer:
        _match(store, state, offer)
    elif criteria["mode"] == "both":
        # Also match existing Career offers: companies can start from their current book.
        for row in store.load_opportunities():
            if not row.get("archived") and not offer_rejection(row, criteria):
                _match(store, state, row)
    state["last_run"] = {"at": time.time(), "offer_id": offer_id, "sources": statuses,
                         "offers": len({r["id"] for r in store.load_opportunities()} - before),
                         "observed_offers": len(found_offers), "profiles": len(candidates), "deferred": deferred,
                         "criteria": {k: criteria.get(k) for k in ("mode", "roles", "countries", "sources", "platforms", "min_rate", "sale_rate", "currency", "work_mode", "max_age_days")},
                         "status": "complete" if not deferred and statuses and all(s["status"] == "ok" for s in statuses) else "partial"}
    summary = {k: state["last_run"][k] for k in ("at", "offers", "status", "deferred")}
    summary.update({"new_profiles": len(set(candidates) - previous_candidates),
                    "matched_offers": sum(bool(g.get("results")) for g in state["matches"].values()),
                    "matches": sum(len(g.get("results", [])) for g in state["matches"].values()),
                    "source_errors": sum(s["status"] != "ok" for s in statuses)})
    state["runs"] = (state["runs"] + [summary])[-180:]


def _delete_candidate(store: CareerStore, state: dict[str, Any], cid: str) -> None:
    candidate = next((row for row in state["candidates"] if row["id"] == cid), None)
    if candidate is None:
        raise CareerError("Candidate not found.", status=404)
    removed = [candidate]
    state["candidates"] = [row for row in state["candidates"] if row["id"] != cid]
    for group in state["matches"].values():
        removed.extend(row["candidate"] for row in group.get("results", []) if row["candidate"]["id"] == cid)
        group["results"] = [row for row in group.get("results", []) if row["candidate"]["id"] != cid]
    if cid.startswith("internal:"):
        profile = store.load_profile()
        talents = [row for row in profile.get("talents", []) if "internal:" + row["id"] != cid]
        active = profile.get("active_talent_id", "")
        if "internal:" + active == cid:
            active = talents[0]["id"] if talents else ""
        store.save_profile({"talents": talents, "active_talent_id": active})
    # Documents may be shared by multiple candidates. Only delete owned files
    # that no remaining pool or matching record references.
    remaining = state["candidates"] + [row["candidate"] for group in state["matches"].values() for row in group.get("results", [])]
    references = {row.get(key, {}).get("file") for row in remaining for key in ("cv", "dossier") if isinstance(row.get(key), dict)}
    for row in removed:
        for key in ("cv", "dossier"):
            filename = row.get(key, {}).get("file", "") if isinstance(row.get(key), dict) else ""
            if filename not in references and re.fullmatch(r"[a-f0-9]{64}\.(pdf|docx)", filename):
                path = store.root / "talent-files" / filename
                if not path.parent.is_symlink() and not path.is_symlink():
                    path.unlink(missing_ok=True)
    store.append_journal({"kind": "delete_candidate", "text": cid})


def handle_prospecting(store: CareerStore, action: str, body: dict[str, Any]) -> None:
    if action == "prospecting_automate":
        from navin.career.sourcing_mail import run_sourcing_cycle

        run_sourcing_cycle(store)
        return
    try:
        with FileLock(str(store.root / "prospecting.lock"), timeout=0):
            state = _state(store)
            if action == "prospecting_config":
                raw = body.get("criteria", {})
                if not isinstance(raw, dict):
                    raise CareerError("Invalid search criteria.", status=400)
                criteria = state["criteria"]
                inherit_roles = "profile_roles" not in raw and criteria["profile_roles"] == criteria["roles"]
                for key in ("roles", "skills", "countries", "sources", "platforms", "profile_roles", "profile_skills", "profile_countries"):
                    if key in raw:
                        limit = 200 if key in {"skills", "profile_skills"} else 100
                        criteria[key] = list(dict.fromkeys(_clean(x, 100) for x in _split(raw[key])))[:limit]
                if inherit_roles:
                    criteria["profile_roles"] = list(criteria["roles"])
                if "role_priorities" in raw:
                    weights = raw["role_priorities"]
                    if not isinstance(weights, dict) or len(weights) > 100:
                        raise CareerError("Invalid role priorities. Use a percentage from 0 to 100 for each role.", status=400)
                    clean_weights = {}
                    for role, weight in weights.items():
                        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 100:
                            raise CareerError("Invalid role priority. Use a percentage from 0 to 100.", status=400)
                        clean_weights[_clean(role, 100)] = weight
                    criteria["role_priorities"] = clean_weights
                criteria["role_priorities"] = {role: weight for role, weight in criteria["role_priorities"].items() if role in criteria["roles"]}
                if "role_skills" in raw:
                    matrix = raw["role_skills"]
                    if not isinstance(matrix, dict) or len(matrix) > 200 or any(not isinstance(skills, list) for skills in matrix.values()):
                        raise CareerError("Invalid role skill matrix.", status=400)
                    criteria["role_skills"] = {_clean(role, 100): list(dict.fromkeys(_clean(skill, 100) for skill in skills))[:200]
                                               for role, skills in matrix.items()}
                selected_roles = set(criteria["roles"] + criteria["profile_roles"])
                criteria["role_skills"] = {role: skills for role, skills in criteria["role_skills"].items() if role in selected_roles}
                if "autofill" in raw:
                    autofill = raw["autofill"]
                    if not isinstance(autofill, dict) or autofill.get("version") != 1:
                        raise CareerError("Invalid company defaults.", status=400)
                    fields = {"skills", "profile_roles", "profile_skills", "profile_domain", "sources", "platforms"}
                    clean_autofill = {"version": 1}
                    for group in ("generated", "dismissed"):
                        entries = autofill.get(group, {})
                        if not isinstance(entries, dict):
                            raise CareerError("Invalid company defaults.", status=400)
                        clean_autofill[group] = {key: list(dict.fromkeys(_clean(item, 100) for item in _split(value)))[:200]
                                                for key, value in entries.items() if key in fields}
                    criteria["autofill"] = clean_autofill
                criteria["countries"] = [c.upper() for c in criteria["countries"] if re.fullmatch(r"[a-zA-Z]{2}", c)]
                criteria["profile_countries"] = [c.upper() for c in criteria["profile_countries"] if re.fullmatch(r"[a-zA-Z]{2}", c)]
                criteria["sources"] = [s for s in criteria["sources"] if s in SOURCES]
                criteria["platforms"] = [s for s in criteria["platforms"] if s in {p["id"] for p in platform_catalog()}]
                for key in ("domain", "city", "profile_domain", "profile_city", "currency"):
                    if key in raw:
                        criteria[key] = _clean(raw[key], 500 if key in {"domain", "profile_domain"} else 150)
                for key, allowed in (("mode", {"both", "missions", "profiles"}), ("track", {"both", "jobs", "freelance"}),
                                     ("work_mode", {"any", "remote", "hybrid", "onsite"})):
                    if key in raw:
                        if raw[key] not in allowed:
                            raise CareerError("Invalid search mode.", status=400)
                        criteria[key] = raw[key]
                if "max_age_days" in raw:
                    age = raw["max_age_days"]
                    if isinstance(age, bool) or not isinstance(age, int) or not 1 <= age <= 30:
                        raise CareerError("Publication age must be between 1 and 30 days.", status=400)
                    criteria["max_age_days"] = age
                for key in ("internal_talents", "signal_only", "auto_contact", "auto_present", "require_dossier", "daily_search"):
                    if key in raw:
                        criteria[key] = raw[key] is True
                for key in ("candidate_cc", "client_cc"):
                    if key in raw:
                        from navin.career.mail_settings import email_address

                        criteria[key] = list(dict.fromkeys(email_address(a) for a in _split(raw[key])))[:10]
                for key, cap in (("sale_rate", 100000), ("min_rate", 100000), ("buy_rate_max", 100000), ("salary_max", 10000000),
                                 ("margin_percent", 95), ("min_score", 100), ("max_per_day", 25)):
                    if key in raw:
                        try:
                            value = float(raw[key])
                            if not 0 <= value <= cap:
                                raise ValueError
                        except (ValueError, TypeError):
                            raise CareerError(f"Invalid value: {key}.", status=400) from None
                        criteria[key] = value
                if "signature" in raw:
                    criteria["signature"] = _clean(raw["signature"], 2000)
                if isinstance(raw.get("company"), dict):
                    criteria["company"] = {k: _clean(raw["company"].get(k), 250) for k in ("name", "email", "phone", "address")}
                secrets = body.get("keys", {})
                if not isinstance(secrets, dict):
                    raise CareerError("Invalid API keys.", status=400)
                for provider, name in KEYS.items():
                    if provider in secrets:
                        store.save_secret(name, str(secrets[provider] or ""))
                        state["health"] = {k: v for k, v in state["health"].items() if provider not in k and not (provider == "serpapi" and "google_jobs" in k)}
                        state["checks"].pop(provider, None)
                if body.get("activate_company") is True:
                    if not criteria["company"].get("name"):
                        raise CareerError("Company name is required.", status=400)
                    store.save_profile({"account_kind": "company", "company_prospecting": True, "wizard_complete": True,
                                        "company": criteria["company"], "display_name": criteria["company"]["name"],
                                        "email": criteria["company"].get("email", ""), "phone": criteria["company"].get("phone", ""),
                                        "titles": criteria["roles"] or [criteria["domain"]], "stack": criteria["skills"],
                                        "countries_primary": criteria["countries"], "track": criteria["track"],
                                        "work_mode": criteria["work_mode"]})
                    if body.get("configure_daily") is True:
                        from navin.career.loop import start_loop, stop_loop

                        if criteria["daily_search"]:
                            schedule = store.load_loop().get("schedule") or {"kind": "daily", "hour": 9, "minute": 0}
                            if not schedule.get("tz") and body.get("tz"):
                                schedule["tz"] = str(body["tz"])
                            start_loop(store, schedule=schedule,
                                       tz=str(body.get("tz") or "") or None, run_now=False)
                        else:
                            stop_loop(store)
            elif action in {"prospecting_delete_candidate", "prospecting_empty_archive"}:
                if body.get("confirmed") is not True:
                    raise CareerError("Deletion requires confirmation.", status=400)
                if action == "prospecting_delete_candidate":
                    _delete_candidate(store, state, str(body.get("id") or ""))
                else:
                    from navin.career.retention import empty_archive

                    removed = set(empty_archive(store))
                    state["matches"] = {oid: group for oid, group in state["matches"].items() if oid not in removed}
            elif action == "prospecting_reuse":
                _match(store, state, store.get_opportunity(str(body.get("id") or "")))
            elif action in {"prospecting_search", "prospecting_match"}:
                if action == "prospecting_match" and not body.get("id"):
                    raise CareerError("An offer is required.", status=400)
                _search(store, state, str(body.get("id") or ""), request=body)
            elif action == "prospecting_test":
                provider = str(body.get("provider") or "")
                if provider not in KEYS:
                    raise CareerError("Unknown provider.", status=400)
                try:
                    _web(store, provider, "recrutement")
                    state["checks"][provider] = {"ok": True, "at": time.time(), "message": "Connection verified."}
                    state["health"] = {k: v for k, v in state["health"].items() if provider not in k and not (provider == "serpapi" and "google_jobs" in k)}
                except CareerError as exc:
                    state["checks"][provider] = {"ok": False, "at": time.time(), "message": str(exc)}
            elif action == "prospecting_dossier":
                rows = state["matches"].get(str(body.get("id") or ""), {}).get("results", [])
                row = next((r for r in rows if r["candidate"]["id"] == body.get("candidate_id")), None)
                if row is None:
                    raise CareerError("Candidate dossier not found.", status=404)
                patch = {k: body[k] for k in ("stage", "interest", "availability", "note", "contract") if k in body}
                next_row = {**row, **patch}
                if "purchase_rate" in body:
                    try:
                        rate = float(body["purchase_rate"] or 0)
                        if not 0 <= rate <= 100000:
                            raise ValueError
                        next_row["purchase_rate"] = rate
                    except (ValueError, TypeError):
                        raise CareerError("Invalid buying day rate.", status=400) from None
                from navin.career.mail_settings import email_address

                if body.get("candidate_email"):
                    address = email_address(body["candidate_email"])
                    next_row["candidate"] = {**row["candidate"], "email": address, "email_source": "user"}
                    for candidate in state["candidates"]:
                        if candidate["id"] == row["candidate"]["id"]:
                            candidate.update({"email": address, "email_source": "user"})
                client_email = email_address(body["client_email"]) if body.get("client_email") else ""
                if next_row["stage"] not in STAGES or any(next_row[k] not in {"unknown", "confirmed", "declined"} for k in ("interest", "availability")):
                    raise CareerError("Invalid status.", status=400)
                if next_row["stage"] in {"qualified", "submitted", "interview", "placed", "contracted"} and any(next_row[k] != "confirmed" for k in ("interest", "availability")):
                    raise CareerError("Confirm interest and availability after speaking with the candidate.", status=400)
                if next_row["stage"] == "contracted" and not str(next_row["contract"]).strip():
                    raise CareerError("Add the signed contract reference.", status=400)
                if next_row["interest"] == "declined" or next_row["availability"] == "declined":
                    next_row.update({"stage": "rejected", "sharing_consent": False})
                if client_email:
                    store.update_opportunity(str(body["id"]), {"application_email": client_email})
                for key in ("note", "contract"):
                    next_row[key] = _clean(next_row[key], 2000)
                next_row["confirmed_at"] = time.time()
                next_row["history"] = (row.get("history", []) + [{"at": time.time(), "stage": next_row["stage"],
                    "interest": next_row["interest"], "availability": next_row["availability"]}])[-100:]
                row.update(next_row)
            else:
                raise CareerError("Unknown prospecting action.", status=400)
            _save(store, state)
    except Timeout:
        raise CareerError("A company search is already running. Try again after it finishes.", status=409) from None


def search_company(store: CareerStore, **kwargs: Any) -> dict[str, Any]:
    # Offer collection must not inherit the mode used by general prospecting.
    handle_prospecting(store, "prospecting_search", {**kwargs, "mode": "missions", "id": ""})
    last = _state(store)["last_run"]
    return {"added": last.get("offers", 0), "scanned": sum(s.get("count", 0) for s in last.get("sources", [])),
            "prospecting": last}


def run_company_hunt(store: CareerStore, **kwargs: Any) -> dict[str, Any]:
    handle_prospecting(store, "prospecting_search", {})
    last = _state(store)["last_run"]
    state = _state(store)
    if state["runs"]:
        from navin.career.notify import deliver_alert

        run = state["runs"][-1]
        deliver_alert(store, title="Carrière : résultats du jour", level="info",
                      detail=f"{run['offers']} nouvelles missions, {run['new_profiles']} nouveaux profils. "
                             f"{run['matched_offers']} missions avec profils correspondants. "
                             f"Consultez Suivi société. Sources à vérifier : {run['source_errors']}.",
                      event_id="company-search:" + str(run["at"]), event_type="company_search")
    return {"added": last.get("offers", 0), "scanned": sum(s.get("count", 0) for s in last.get("sources", [])),
            "prospecting": last}
