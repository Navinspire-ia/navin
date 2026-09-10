# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Free-Work public job listings (search pages, no login, no apply).

free-work.com renders its job search on the server (Nuxt). Every search page
ships the full listing payload in the `__NUXT_DATA__` script: title, company,
location, contracts, daily rate (TJM), annual salary, duration, remote mode,
skills, publication date and the complete description. This reader fetches
only that public search page (`/{locale}/tech-it/jobs`, allowed by
robots.txt), one request per second, with a hard cap per collect, and backs
off on the first 403 / 429 / Cloudflare challenge. It never signs in, never
posts, never applies. France (`fr`) and the United Kingdom (`en-gb`) are the
two markets the site serves.
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
    normalize_contracts,
    normalize_experience,
    parse_pay_range,
    pay_fields,
)
from navin.career.sources import (
    clean_job_text,
    html_to_text,
    is_listing_hit,
    stable_job_id,
)

HttpGet = Callable[[str], str]

BASE_URL = "https://www.free-work.com"
SEARCH_PATH = "/{locale}/tech-it/jobs"
JOB_PATH = "/{locale}/tech-it/job-mission/{job_slug}/{slug}"
# Market ISO -> site locale. Free-Work only serves these two markets.
LOCALES: dict[str, str] = {"FR": "fr", "GB": "en-gb"}
PAGE_SIZE = 16

# One collect never spends more than this many HTTP calls on Free-Work.
MAX_REQUESTS = 8
# Pages per (title, market) pair; two pages of 16 covers the fresh missions.
MAX_PAGES = 2
MIN_INTERVAL_S = 1.0
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Track -> `contracts` filter. Empty track reads every contract type.
_CONTRACTS = {"freelance": "contractor", "jobs": "permanent"}
_FREELANCE_CONTRACTS = {"contractor"}
_EMPLOYEE_CONTRACTS = {"permanent", "fixed-term", "apprenticeship", "internship"}
_REMOTE_MODES = {"full": "remote", "partial": "hybrid", "none": "onsite"}
_DEFAULT_CURRENCY = {"fr": "EUR", "en-gb": "GBP"}
_PERIOD_MONTHS = {"day": 1 / 21.0, "week": 0.25, "month": 1.0, "year": 12.0}

_NUXT_DATA_RE = re.compile(
    r'<script[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>',
    re.S | re.I,
)
_CHALLENGE_MARKERS = ("cf-chl", "Just a moment", "challenge-platform", "captcha")

# devalue reducers Nuxt wraps around plain values. Unknown tags hydrate to None.
_WRAPPER_TAGS = {"ShallowReactive", "Reactive", "ShallowRef", "Ref"}
_EMPTY_REF_TAGS = {"EmptyShallowRef", "EmptyRef"}


class FreeWorkWallError(RuntimeError):
    """Free-Work answered with a block (403 / 429 / challenge page)."""


def _text(raw: str) -> str:
    return clean_job_text(html_to_text(raw or "")).strip()


def default_http_get(url: str, timeout: float = 20.0) -> str:
    locale = "en-gb" if "/en-gb/" in url else "fr"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8" if locale == "en-gb" else "fr-FR,fr;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429, 503}:
            raise FreeWorkWallError(f"HTTP {exc.code}") from exc
        if exc.code == 404:
            return ""
        raise
    except urllib.error.URLError as exc:
        raise FreeWorkWallError(f"unreachable: {exc.reason}") from exc
    if "__NUXT_DATA__" not in body and any(marker in body for marker in _CHALLENGE_MARKERS):
        raise FreeWorkWallError("challenge page")
    return body


def locale_for(country: str) -> str:
    return LOCALES.get((country or "").strip().upper(), "")


def search_url(query: str, country: str, *, track: str = "", page: int = 1) -> str:
    locale = locale_for(country) or "fr"
    params: dict[str, Any] = {"query": (query or "").strip()}
    contracts = _CONTRACTS.get((track or "").strip().lower())
    if contracts:
        params["contracts"] = contracts
    if page > 1:
        params["page"] = page
    return BASE_URL + SEARCH_PATH.format(locale=locale) + "?" + urllib.parse.urlencode(params)


def job_url(locale: str, job_slug: str, slug: str) -> str:
    loc = locale or "fr"
    if slug and job_slug:
        return BASE_URL + JOB_PATH.format(locale=loc, job_slug=job_slug, slug=slug)
    if slug:
        return BASE_URL + SEARCH_PATH.format(locale=loc) + "?" + urllib.parse.urlencode({"query": slug})
    return BASE_URL + SEARCH_PATH.format(locale=loc)


def hydrate_devalue(raw: list[Any]) -> Any:
    """Rebuild the value tree of a devalue / Nuxt `__NUXT_DATA__` array.

    Index 0 is the root. Objects and arrays hold indices into the same array;
    negative indices are the devalue sentinels (undefined, hole, NaN, ...).
    Reactive wrappers unwrap to their inner value; Date keeps its ISO string.
    """
    if not isinstance(raw, list) or not raw:
        return None
    cache: dict[int, Any] = {}

    def hydrate(index: Any) -> Any:
        if not isinstance(index, int) or isinstance(index, bool):
            return None
        if index < 0:
            return None
        if index >= len(raw):
            return None
        if index in cache:
            return cache[index]
        value = raw[index]
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                tag = value[0]
                if tag in _WRAPPER_TAGS:
                    inner = hydrate(value[1]) if len(value) > 1 else None
                    cache[index] = inner
                    return inner
                if tag in _EMPTY_REF_TAGS:
                    inner = None
                    if len(value) > 1 and isinstance(value[1], str):
                        try:
                            inner = json.loads(value[1])
                        except ValueError:
                            inner = None
                    cache[index] = inner
                    return inner
                if tag == "Date":
                    inner = value[1] if len(value) > 1 else None
                    cache[index] = inner
                    return inner
                if tag == "Set":
                    out_list: list[Any] = []
                    cache[index] = out_list
                    out_list.extend(hydrate(item) for item in value[1:])
                    return out_list
                if tag == "Map":
                    out_map: dict[Any, Any] = {}
                    cache[index] = out_map
                    pairs = value[1:]
                    for pos in range(0, len(pairs) - 1, 2):
                        key = hydrate(pairs[pos])
                        if isinstance(key, (str, int, float, bool)) or key is None:
                            out_map[key] = hydrate(pairs[pos + 1])
                    return out_map
                if tag == "Object" and len(value) > 1:
                    inner = hydrate(value[1])
                    cache[index] = inner
                    return inner
                cache[index] = None
                return None
            out: list[Any] = []
            cache[index] = out
            out.extend(hydrate(item) for item in value)
            return out
        if isinstance(value, dict):
            obj: dict[str, Any] = {}
            cache[index] = obj
            for key, item in value.items():
                obj[str(key)] = hydrate(item)
            return obj
        cache[index] = value
        return value

    return hydrate(0)


def parse_search_page(body: str) -> dict[str, Any] | None:
    """Listing payload out of one search page: {"jobs": [...], "total": n, "page": n}.

    None when the page carries no Nuxt payload (layout changed or blocked).
    """
    match = _NUXT_DATA_RE.search(body or "")
    if not match:
        return None
    try:
        raw = json.loads(match.group(1))
    except ValueError:
        return None
    root = hydrate_devalue(raw)
    data = root.get("data") if isinstance(root, dict) else None
    if not isinstance(data, dict):
        return None
    search: dict[str, Any] | None = None
    for key, value in data.items():
        if str(key).startswith("jobs-search") and isinstance(value, dict):
            search = value
            break
    if search is None:
        return None
    jobs = [item for item in (search.get("jobs") or []) if isinstance(item, dict)]
    try:
        total = int(search.get("totalItems") or 0)
    except (TypeError, ValueError):
        total = 0
    try:
        page = int(search.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    return {"jobs": jobs, "total": total, "page": page}


def parse_money(text: str) -> tuple[float | None, str]:
    """Upper bound and currency of a posted pay line ("330-350 €", "40k-45k €", "£350-450").

    The upper bound is what the offer says it can reach; the profile floor is
    compared against it, so an offer whose ceiling sits under the floor scores
    low while a range that spans the floor stays negotiable. Thin wrapper over
    the shared parser in navin.career.normalize.
    """
    parsed = parse_pay_range(text)
    return parsed["max"], parsed["currency"]


def duration_months(value: Any, period: str) -> int:
    try:
        count = float(value)
    except (TypeError, ValueError):
        return 0
    factor = _PERIOD_MONTHS.get(str(period or "").strip().lower(), 0.0)
    if count <= 0 or factor <= 0:
        return 0
    return max(1, round(count * factor))


def duration_label(value: Any, period: str) -> str:
    try:
        count = int(float(value))
    except (TypeError, ValueError):
        return ""
    unit = str(period or "").strip().lower()
    if count <= 0 or unit not in _PERIOD_MONTHS:
        return ""
    return f"{count} {unit}{'s' if count > 1 else ''}"


def _track_for(contracts: list[str], wanted: str) -> str:
    kinds = {str(item).strip().lower() for item in contracts}
    freelance = bool(kinds & _FREELANCE_CONTRACTS)
    employee = bool(kinds & _EMPLOYEE_CONTRACTS) or not kinds
    if freelance and not employee:
        return "freelance"
    if employee and not freelance:
        return "jobs"
    return wanted if wanted in {"freelance", "jobs"} else "freelance"


def to_job(record: dict[str, Any], *, country: str, track: str) -> dict[str, Any] | None:
    """One Navin opportunity out of one Free-Work listing record."""
    title = clean_job_text(str(record.get("title") or "")).strip()[:180]
    slug = str(record.get("slug") or "").strip()
    if not title or not slug:
        return None
    iso = (country or "").strip().upper() if locale_for(country) else "FR"
    locale = locale_for(iso)
    job = record.get("job") if isinstance(record.get("job"), dict) else {}
    url = job_url(locale, str(job.get("slug") or "").strip(), slug)
    if is_listing_hit(title, url):
        return None
    company = record.get("company") if isinstance(record.get("company"), dict) else {}
    location = record.get("location") if isinstance(record.get("location"), dict) else {}
    contracts = [str(item).strip().lower() for item in (record.get("contracts") or []) if str(item).strip()]
    row_track = _track_for(contracts, (track or "").strip().lower())
    daily = clean_job_text(str(record.get("dailySalary") or "")).strip()
    annual = clean_job_text(str(record.get("annualSalary") or "")).strip()
    day_range = parse_pay_range(daily, default_period="day")
    year_range = parse_pay_range(annual, default_period="year")
    currency = day_range["currency"] or year_range["currency"] or _DEFAULT_CURRENCY.get(locale, "")
    pay = pay_fields(
        daily=(day_range["min"], day_range["max"]),
        annual=(year_range["min"], year_range["max"]),
        currency=currency,
        country=iso,
        track=row_track,
    )
    kinds = normalize_contracts(contracts)
    level, years = normalize_experience(str(record.get("experienceLevel") or ""), title)
    skills: list[str] = []
    for item in record.get("skills") or []:
        name = clean_job_text(str(item.get("name") or "")).strip() if isinstance(item, dict) else ""
        if name and name.lower() not in {known.lower() for known in skills}:
            skills.append(name)
    skills = skills[:30]
    remote = _REMOTE_MODES.get(str(record.get("remoteMode") or "").strip().lower(), "")
    description = _text(str(record.get("description") or ""))[:4000]
    posted = str(record.get("publishedAt") or "").strip()[:10]
    months = duration_months(record.get("durationValue"), str(record.get("durationPeriod") or ""))
    return {
        "id": stable_job_id("free-work", url, title),
        "source": "free-work",
        "title": title,
        "company": clean_job_text(str(company.get("name") or "")).strip()[:120],
        "location": clean_job_text(str(location.get("label") or "")).strip()[:120],
        "country": iso,
        **pay,
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
        "ingest": "freework_public",
        "attribution": "Free-Work (public listing)",
        "contracts": kinds,
        "employment_type": ", ".join(contracts)[:60],
        "duration": duration_label(record.get("durationValue"), str(record.get("durationPeriod") or "")),
        "duration_months": months,
        "freework_id": str(record.get("id") or "").strip(),
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
    """Market ISOs Free-Work serves, in profile order, without duplicates."""
    out: list[str] = []
    for item in countries or []:
        iso = str(item or "").strip().upper()
        if iso in LOCALES and iso not in out:
            out.append(iso)
    return out


def search_freework_jobs(
    *,
    titles: list[str],
    countries: list[str],
    track: str = "",
    http_get: HttpGet | None = None,
    max_requests: int = MAX_REQUESTS,
    max_pages: int = MAX_PAGES,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Public Free-Work listings for the profile titles in the FR / GB markets.

    Returns {"jobs": [...], "requests": n, "walls": [...], "pairs": n,
    "markets": [...], "total": n}. Markets the site does not serve cost no
    request. A wall (403 / 429 / challenge) stops the whole run: the next
    collect starts fresh, which is what a polite client owes a rate limit.
    """
    getter = http_get or default_http_get
    budget = _Budget(max_requests, MIN_INTERVAL_S if http_get is None else 0.0, sleep)
    wanted_titles = [str(item).strip() for item in titles if str(item).strip()][:3] or ["Data Engineer"]
    markets = supported_markets(countries)
    records: dict[str, tuple[dict[str, Any], str]] = {}
    walls: list[str] = []
    pairs = 0
    total = 0
    stopped = False
    for country in markets:
        for title in wanted_titles:
            pairs += 1
            for page in range(1, max_pages + 1):
                if not budget.take():
                    stopped = True
                    break
                url = search_url(title, country, track=track, page=page)
                try:
                    body = getter(url)
                except FreeWorkWallError as exc:
                    walls.append(f"Free-Work {exc} on {country} / {title}")
                    stopped = True
                    break
                except (OSError, ValueError) as exc:
                    logger.info("career free-work search skipped: {}", exc)
                    break
                if not body:
                    break
                parsed = parse_search_page(body)
                if parsed is None:
                    walls.append(f"Free-Work listing payload missing on {country} / {title}")
                    stopped = True
                    break
                if page == 1:
                    total += int(parsed.get("total") or 0)
                page_jobs = parsed.get("jobs") or []
                for record in page_jobs:
                    key = str(record.get("id") or record.get("slug") or "")
                    if key and key not in records:
                        records[key] = (record, country)
                if len(page_jobs) < PAGE_SIZE or page * PAGE_SIZE >= int(parsed.get("total") or 0):
                    break
            if stopped:
                break
        if stopped:
            break

    jobs: list[dict[str, Any]] = []
    for record, country in records.values():
        row = to_job(record, country=country, track=track)
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
