# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Official job APIs (keyed). Never used for LinkedIn or closed boards."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from loguru import logger

from navin.career.normalize import (
    contracts_track,
    normalize_contracts,
    normalize_experience,
    normalize_remote,
    pay_from_numbers,
)
from navin.career.sources import is_closed_job_url, stable_job_id

_ADZUNA_COUNTRIES = {
    "AT": "at",
    "AU": "au",
    "BE": "be",
    "BR": "br",
    "CA": "ca",
    "CH": "ch",
    "DE": "de",
    "ES": "es",
    "FR": "fr",
    "GB": "gb",
    "IN": "in",
    "IT": "it",
    "MX": "mx",
    "NL": "nl",
    "NZ": "nz",
    "PL": "pl",
    "SG": "sg",
    "UK": "gb",
    "US": "us",
    "ZA": "za",
}

_JOOBLE_LABELS = {
    "AE": "United Arab Emirates",
    "FR": "France",
    "GB": "United Kingdom",
    "QA": "Qatar",
    "SA": "Saudi Arabia",
    "US": "United States",
}


def _stable_id(source: str, url: str, title: str) -> str:
    return stable_job_id(source, url, title)


def _guess_track(title: str, profile_track: str) -> str:
    hay = (title or "").lower()
    if any(token in hay for token in ("freelance", "contract", "contractor", "mission")):
        return "freelance"
    if profile_track in {"freelance", "jobs"}:
        return profile_track
    return "jobs"


def _row(
    *,
    source: str,
    title: str,
    company: str,
    location: str,
    country: str,
    url: str,
    description: str,
    track: str,
    compensation: float | None = None,
    currency: str = "",
    posted_at: str = "",
) -> dict[str, Any] | None:
    title = title.strip()
    url = url.strip()
    if not title or not url or is_closed_job_url(url):
        return None
    return {
        "id": _stable_id(source, url, title),
        "source": source,
        "title": title[:180],
        "company": company.strip()[:80],
        "location": location.strip()[:120],
        "country": country,
        "compensation": compensation,
        "currency": currency,
        "description": description.strip()[:4000],
        "url": url,
        "track": _guess_track(title, track),
        "stage": "discovered",
        "remote": "remote" if "remote" in f"{title} {location} {description}".lower() else "",
        "posted_at": posted_at,
        "attribution": source.title(),
        "ingest": "official_api",
    }


def _get_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NavinCareer/1.0 (+https://navin.ai)",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cred(name: str, secrets: dict[str, str] | None = None) -> str:
    bag = secrets or {}
    key = str(name or "").strip()
    return str(bag.get(key) or bag.get(key.upper()) or os.environ.get(key, "") or "").strip()


def _post_json(url: str, payload: dict[str, Any]) -> Any:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "NavinCareer/1.0 (+https://navin.ai)",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_adzuna(
    query: str,
    countries: list[str],
    track: str,
    *,
    secrets: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    app_id = _cred("ADZUNA_APP_ID", secrets)
    app_key = _cred("ADZUNA_APP_KEY", secrets)
    if not app_id or not app_key:
        return []
    codes = [_ADZUNA_COUNTRIES[iso] for iso in countries if iso in _ADZUNA_COUNTRIES]
    if not codes:
        codes = ["fr"]
    rows: list[dict[str, Any]] = []
    for code in codes[:3]:
        params = urllib.parse.urlencode(
            {
                "app_id": app_id,
                "app_key": app_key,
                "what": query,
                "results_per_page": 20,
            }
        )
        url = f"https://api.adzuna.com/v1/api/jobs/{code}/search/1?{params}"
        try:
            payload = _get_json(url)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            logger.info("career adzuna {} skipped: {}", code, exc)
            continue
        items = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        iso = next((key for key, value in _ADZUNA_COUNTRIES.items() if value == code), code.upper())
        for item in items[:20]:
            if not isinstance(item, dict):
                continue
            company = item.get("company") if isinstance(item.get("company"), dict) else {}
            location = item.get("location") if isinstance(item.get("location"), dict) else {}
            salary = item.get("salary_max") or item.get("salary_min")
            try:
                compensation = float(salary) if salary is not None else None
            except (TypeError, ValueError):
                compensation = None
            job = _row(
                source="adzuna",
                title=str(item.get("title") or ""),
                company=str(company.get("display_name") or ""),
                location=str(location.get("display_name") or ""),
                country=iso,
                url=str(item.get("redirect_url") or item.get("adref") or ""),
                description=str(item.get("description") or ""),
                track=track,
                compensation=compensation,
                currency=str(item.get("salary_currency") or ""),
                posted_at=str(item.get("created") or ""),
            )
            if job:
                rows.append(job)
    return rows


def fetch_jooble(
    query: str,
    countries: list[str],
    track: str,
    *,
    secrets: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    key = _cred("JOOBLE_API_KEY", secrets)
    if not key:
        return []
    location = ""
    for iso in countries:
        if iso in _JOOBLE_LABELS:
            location = _JOOBLE_LABELS[iso]
            break
        if iso:
            location = iso
            break
    url = f"https://jooble.org/api/{urllib.parse.quote(key)}"
    try:
        payload = _post_json(url, {"keywords": query, "location": location})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("career jooble skipped: {}", exc)
        return []
    items = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    country = countries[0] if countries else ""
    rows: list[dict[str, Any]] = []
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        job = _row(
            source="jooble",
            title=str(item.get("title") or ""),
            company=str(item.get("company") or ""),
            location=str(item.get("location") or ""),
            country=country,
            url=str(item.get("link") or item.get("url") or ""),
            description=str(item.get("snippet") or item.get("description") or ""),
            track=track,
            posted_at=str(item.get("updated") or ""),
        )
        if job:
            rows.append(job)
    return rows


def fetch_usajobs(
    query: str,
    countries: list[str],
    track: str,
    *,
    secrets: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    key = _cred("USAJOBS_API_KEY", secrets)
    agent = _cred("USAJOBS_USER_AGENT", secrets)
    if not key or not agent:
        return []
    wanted = {iso.upper() for iso in countries}
    if wanted and "US" not in wanted:
        return []
    params = urllib.parse.urlencode({"Keyword": query, "ResultsPerPage": 20})
    url = f"https://data.usajobs.gov/api/search?{params}"
    try:
        payload = _get_json(
            url,
            headers={
                "Host": "data.usajobs.gov",
                "User-Agent": agent,
                "Authorization-Key": key,
            },
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("career usajobs skipped: {}", exc)
        return []
    result = payload.get("SearchResult") if isinstance(payload, dict) else None
    items = result.get("SearchResultItems") if isinstance(result, dict) else None
    if not isinstance(items, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        desc = item.get("MatchedObjectDescriptor")
        if not isinstance(desc, dict):
            continue
        area = desc.get("UserArea") if isinstance(desc.get("UserArea"), dict) else {}
        details = area.get("Details") if isinstance(area.get("Details"), dict) else {}
        job = _row(
            source="usajobs",
            title=str(desc.get("PositionTitle") or ""),
            company=str(desc.get("OrganizationName") or ""),
            location=str(desc.get("PositionLocationDisplay") or ""),
            country="US",
            url=str(desc.get("PositionURI") or ""),
            description=str(details.get("JobSummary") or desc.get("QualificationSummary") or ""),
            track=track,
            posted_at=str(desc.get("PublicationStartDate") or ""),
        )
        if job:
            rows.append(job)
    return rows


_JOBOPP_URL = "https://api.jobopportunitiesapi.org/v1/jobs"
_JOBOPP_LIMIT = 50  # include_description=true caps the page at 50


def fetch_jobopportunities(
    query: str,
    countries: list[str],
    track: str,
    *,
    secrets: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Job Opportunities API (employer-direct ledger, Bearer key). https://www.jobopportunitiesapi.org/docs"""
    key = _cred("JOBOPPORTUNITIES_API_KEY", secrets)
    if not key:
        return []
    isos = [str(iso).strip().upper() for iso in countries if str(iso).strip() and str(iso).strip().upper() != "REMOTE"][:6]
    params: dict[str, str] = {"q": query, "limit": str(_JOBOPP_LIMIT), "include_description": "true"}
    if isos:
        params["country"] = ",".join(isos)
    url = f"{_JOBOPP_URL}?{urllib.parse.urlencode(params)}"
    try:
        payload = _get_json(url, headers={"Authorization": f"Bearer {key}"})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("career jobopportunities skipped: {}", exc)
        return []
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in items[:_JOBOPP_LIMIT]:
        if not isinstance(item, dict):
            continue
        iso = str(item.get("country") or "").strip().upper()
        job = _row(
            source="jobopportunities",
            title=str(item.get("title") or ""),
            company=str(item.get("company") or ""),
            location=str(item.get("location") or item.get("city") or ""),
            country=iso if len(iso) == 2 else (isos[0] if len(isos) == 1 else ""),
            url=str(item.get("apply_url") or item.get("url") or ""),
            description=str(item.get("description") or ""),
            track=track,
            currency=str(item.get("salary_currency") or ""),
            posted_at=str(item.get("posted_at") or item.get("first_seen_at") or ""),
        )
        if not job:
            continue
        job.update(
            pay_from_numbers(
                item.get("salary_min"),
                item.get("salary_max"),
                period=str(item.get("salary_period") or ""),
                currency=str(item.get("salary_currency") or ""),
                country=job["country"],
                track=job["track"],
            )
        )
        kinds = normalize_contracts(item.get("employment_type"), job["title"])
        if kinds:
            job["contracts"] = kinds
            job["employment_type"] = ", ".join(kinds)
            job["track"] = contracts_track(kinds, track)
        remote = normalize_remote(str(item.get("remote") or ""))
        if remote:
            job["remote"] = remote
        level, years = normalize_experience(str(item.get("seniority") or ""), job["title"])
        if level:
            job["experience_level"] = level
            job["seniority"] = level
        if years is not None:
            job["experience_years_min"] = years
        job["attribution"] = f"Job Opportunities API ({item.get('source') or 'employer'})"
        rows.append(job)
    return rows


def collect_official_apis(
    query: str,
    countries: list[str],
    track: str,
    *,
    sources: list[str] | None = None,
    secrets: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Adzuna / Jooble / USAJOBS / Job Opportunities API when keys are on file. Empty if not configured."""
    wanted = {str(item).strip().lower() for item in (sources or []) if str(item).strip()}
    if sources is not None and not wanted:
        return []
    rows: list[dict[str, Any]] = []
    if sources is None or "adzuna" in wanted:
        rows.extend(fetch_adzuna(query, countries, track, secrets=secrets))
    if sources is None or "jooble" in wanted:
        rows.extend(fetch_jooble(query, countries, track, secrets=secrets))
    if sources is None or "usajobs" in wanted:
        rows.extend(fetch_usajobs(query, countries, track, secrets=secrets))
    if sources is None or "jobopportunities" in wanted:
        rows.extend(fetch_jobopportunities(query, countries, track, secrets=secrets))
    return rows
