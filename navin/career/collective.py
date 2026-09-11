# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Collective.work public job listings (search pages, no login, no apply).

collective.work ships the search results in the Next.js ``__NEXT_DATA__``
script: title, company, location, contract, daily rate / salary, remote
mode, skills, publication date and the description. This reader fetches
only the public board (``/jobs/{locale}``, allowed by robots.txt), one
request per second, with a hard cap per collect, and backs off on the
first 403 / 429 / Cloudflare challenge. It never signs in, never posts,
never applies. One EU board: France, Belgium and the neighbouring
markets share the same listing.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from loguru import logger

from navin.career.normalize import (
    duration_from_text,
    normalize_contracts,
    normalize_experience,
    parse_pay_range,
    pay_fields,
)
from navin.career.sources import (
    clean_job_text,
    html_to_text,
    infer_country_iso,
    is_listing_hit,
    stable_job_id,
)

HttpGet = Callable[[str], str]

BASE_URL = "https://www.collective.work"
SEARCH_PATH = "/jobs/{locale}"
JOB_PATH = "/jobs/{locale}/{slug}"
# Markets that justify a collect. The board itself is one EU list.
SERVED = frozenset({"FR", "BE", "CH", "LU", "MA", "TN", "ES", "IT", "NL", "DE", "GB"})
FR_LOCALES = frozenset({"FR", "BE", "CH", "LU", "MA", "TN"})
PAGE_SIZE = 30
MAX_REQUESTS = 6
MAX_PAGES = 2
MIN_INTERVAL_S = 1.0
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_CONTRACTS = {"freelance": "Freelance", "jobs": "Permanent"}
_REMOTE = {"HYBRID": "hybrid", "REMOTE": "remote", "ON_SITE": "onsite"}
_CHALLENGE_MARKERS = ("cf-chl", "Just a moment", "challenge-platform", "captcha")
_NEXT_DATA_RE = re.compile(
    r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.S | re.I,
)


class CollectiveWallError(RuntimeError):
    """Collective.work answered with a block (403 / 429 / challenge page)."""


def _text(raw: str) -> str:
    return clean_job_text(html_to_text(raw or "")).strip()


def default_http_get(url: str, timeout: float = 20.0) -> str:
    locale = "en" if "/jobs/en" in url else "fr"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8" if locale == "en" else "fr-FR,fr;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429, 503}:
            raise CollectiveWallError(f"HTTP {exc.code}") from exc
        if exc.code == 404:
            return ""
        raise
    except urllib.error.URLError as exc:
        raise CollectiveWallError(f"unreachable: {exc.reason}") from exc
    if "__NEXT_DATA__" not in body and any(marker in body for marker in _CHALLENGE_MARKERS):
        raise CollectiveWallError("challenge page")
    return body


def locale_for(countries: list[str]) -> str:
    isos = {(item or "").strip().upper() for item in countries or []}
    if isos & FR_LOCALES or not (isos & SERVED):
        return "fr"
    return "en"


def search_url(query: str, *, locale: str = "fr", track: str = "", page: int = 1) -> str:
    params: dict[str, Any] = {"search": (query or "").strip()}
    contracts = _CONTRACTS.get((track or "").strip().lower())
    if contracts:
        params["contractType"] = contracts
    if page > 1:
        params["page"] = page
    return BASE_URL + SEARCH_PATH.format(locale=locale or "fr") + "?" + urllib.parse.urlencode(params)


def job_url(locale: str, slug: str) -> str:
    return BASE_URL + JOB_PATH.format(locale=locale or "fr", slug=slug)


def parse_search_page(body: str) -> dict[str, Any] | None:
    """Listing payload out of one search page: {"jobs": [...], "total": n, "from": n}.

    None when the page carries no Next payload (layout changed or blocked).
    """
    match = _NEXT_DATA_RE.search(body or "")
    if not match:
        return None
    try:
        raw = json.loads(match.group(1))
    except ValueError:
        return None
    queries = (
        (((raw.get("props") or {}).get("pageProps") or {}).get("dehydratedState") or {}).get("queries")
        or []
    )
    for item in queries:
        data = ((item or {}).get("state") or {}).get("data") if isinstance(item, dict) else None
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, dict) or not isinstance(results.get("projects"), list):
            continue
        jobs = [row for row in results["projects"] if isinstance(row, dict)]
        pagination = results.get("pagination") if isinstance(results.get("pagination"), dict) else {}
        try:
            total = int(pagination.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        try:
            start = int(pagination.get("from") or 0)
        except (TypeError, ValueError):
            start = 0
        return {"jobs": jobs, "total": total, "from": start}
    return None


def skill_label(key: str) -> str:
    return clean_job_text(str(key or "").replace("_", " ")).strip().title()


def _location_label(record: dict[str, Any]) -> str:
    loc = record.get("location") if isinstance(record.get("location"), dict) else {}
    return clean_job_text(str(loc.get("fullNameEnglish") or loc.get("fullNameFrench") or "")).strip()


def _pay(record: dict[str, Any], *, track: str, country: str) -> dict[str, Any]:
    brief = clean_job_text(str(record.get("budgetBrief") or "")).strip()
    freq = str(record.get("salaryFrequency") or "").strip().lower()
    period = "year" if freq in {"year", "yearly", "annual"} else "day" if freq in {"day", "daily"} else ""
    if not period:
        period = "year" if track == "jobs" else "day"
    brief_range = parse_pay_range(brief, default_period=period)
    try:
        lo = float(record["minSalary"]) if record.get("minSalary") is not None else None
    except (TypeError, ValueError):
        lo = None
    try:
        hi = float(record["maxSalary"]) if record.get("maxSalary") is not None else None
    except (TypeError, ValueError):
        hi = None
    currency = (
        str(record.get("salaryCurrency") or "").strip().upper()
        or brief_range["currency"]
        or ("EUR" if country in FR_LOCALES or not country else "")
    )
    daily = (brief_range["min"], brief_range["max"]) if period == "day" else (None, None)
    annual = (brief_range["min"], brief_range["max"]) if period == "year" else (None, None)
    if lo is not None or hi is not None:
        if period == "year":
            annual = (lo if lo is not None else annual[0], hi if hi is not None else annual[1])
        else:
            daily = (lo if lo is not None else daily[0], hi if hi is not None else daily[1])
    return pay_fields(daily=daily, annual=annual, currency=currency, country=country, track=track)


def to_job(record: dict[str, Any], *, locale: str, track: str) -> dict[str, Any] | None:
    """One Navin opportunity out of one Collective.work listing record."""
    title = clean_job_text(str(record.get("name") or "")).strip()[:180]
    slug = str(record.get("slug") or "").strip()
    if not title or not slug:
        return None
    url = job_url(locale, slug)
    if is_listing_hit(title, url):
        return None
    company = record.get("company") if isinstance(record.get("company"), dict) else {}
    place = _location_label(record)
    iso = infer_country_iso("", place) or ("FR" if locale == "fr" else "")
    permanent = bool(record.get("isPermanentContract"))
    row_track = "jobs" if permanent else "freelance"
    if track in {"freelance", "jobs"} and row_track != track:
        return None
    kinds = normalize_contracts(["permanent"] if permanent else ["contractor"])
    prefs = [str(item).strip().upper() for item in (record.get("workPreferences") or []) if str(item).strip()]
    remote = next((_REMOTE[item] for item in prefs if item in _REMOTE), "")
    skills: list[str] = []
    for item in record.get("projectTypes") or []:
        name = skill_label(str(item))
        if name and name.lower() not in {known.lower() for known in skills}:
            skills.append(name)
    skills = skills[:30]
    description = _text(str(record.get("description") or record.get("sumUp") or ""))[:4000]
    months, duration = duration_from_text(description)
    level, years = normalize_experience("", title)
    posted = str(record.get("publishedAt") or "")[:10]
    return {
        "id": stable_job_id("collective", url, title),
        "source": "collective",
        "title": title,
        "company": clean_job_text(str(company.get("name") or "")).strip()[:120],
        "location": place[:120],
        "country": iso,
        **_pay(record, track=row_track, country=iso),
        "stack": skills,
        "seniority": level,
        "experience_level": level,
        "experience_years_min": years,
        "remote": remote,
        "posted_at": posted,
        "contact": "",
        "description": description,
        "url": url,
        "track": row_track,
        "stage": "discovered",
        "ingest": "collective_public",
        "attribution": "Collective.work (public listing)",
        "contracts": kinds,
        "employment_type": "permanent" if permanent else "contractor",
        "duration": duration,
        "duration_months": months,
        "collective_id": str(record.get("id") or "").strip(),
    }


class _Budget:
    def __init__(self, max_requests: int, min_interval_s: float, sleep: Callable[[float], None]) -> None:
        self.left = max_requests
        self.min_interval_s = min_interval_s
        self._sleep = sleep
        self._last = 0.0

    def take(self) -> bool:
        if self.left <= 0:
            return False
        wait = self.min_interval_s - (time.monotonic() - self._last)
        if wait > 0 and self._last:
            self._sleep(wait)
        self._last = time.monotonic()
        self.left -= 1
        return True


def supported_markets(countries: list[str]) -> list[str]:
    """Profile ISOs that justify reading Collective.work, in profile order."""
    out: list[str] = []
    for item in countries or []:
        iso = str(item or "").strip().upper()
        if iso in SERVED and iso not in out:
            out.append(iso)
    return out


def search_collective_jobs(
    *,
    titles: list[str],
    countries: list[str],
    track: str = "",
    http_get: HttpGet | None = None,
    max_requests: int = MAX_REQUESTS,
    max_pages: int = MAX_PAGES,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Public Collective.work listings for the profile titles.

    Returns {"jobs": [...], "requests": n, "walls": [...], "pairs": n,
    "markets": [...], "total": n}. Markets the board does not serve cost no
    request. A wall (403 / 429 / challenge) stops the whole run.
    """
    getter = http_get or default_http_get
    budget = _Budget(max_requests, MIN_INTERVAL_S if http_get is None else 0.0, sleep)
    wanted_titles = [str(item).strip() for item in titles if str(item).strip()][:3] or ["Data Engineer"]
    markets = supported_markets(countries)
    locale = locale_for(markets)
    records: dict[str, dict[str, Any]] = {}
    walls: list[str] = []
    pairs = 0
    total = 0
    stopped = False
    if not markets:
        return {"jobs": [], "requests": 0, "walls": [], "pairs": 0, "markets": [], "total": 0}
    for title in wanted_titles:
        pairs += 1
        for page in range(1, max_pages + 1):
            if not budget.take():
                stopped = True
                break
            url = search_url(title, locale=locale, track=track, page=page)
            try:
                body = getter(url)
            except CollectiveWallError as exc:
                walls.append(f"Collective.work {exc} on {title}")
                stopped = True
                break
            except (OSError, ValueError) as exc:
                logger.info("career collective.work search skipped: {}", exc)
                break
            if not body:
                break
            parsed = parse_search_page(body)
            if parsed is None:
                walls.append(f"Collective.work listing payload missing on {title}")
                stopped = True
                break
            if page == 1:
                total += int(parsed.get("total") or 0)
            page_jobs = parsed.get("jobs") or []
            for record in page_jobs:
                key = str(record.get("id") or record.get("slug") or "")
                if key and key not in records:
                    records[key] = record
            if len(page_jobs) < PAGE_SIZE or int(parsed.get("from") or 0) + PAGE_SIZE >= int(parsed.get("total") or 0):
                break
        if stopped:
            break

    jobs: list[dict[str, Any]] = []
    for record in records.values():
        row = to_job(record, locale=locale, track=track)
        if row:
            jobs.append(row)
    return {
        "jobs": jobs,
        "requests": max_requests - budget.left,
        "walls": walls,
        "pairs": pairs,
        "markets": markets,
        "total": total,
    }
