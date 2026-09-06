"""LinkedIn public job listings (guest search, no login, no Easy Apply).

linkedin.com serves its job search to anonymous visitors through
`jobs-guest/jobs/api/seeMoreJobPostings/search` (HTML cards) and
`jobs-guest/jobs/api/jobPosting/<id>` (one description). This reader uses
exactly those two public pages, one request per second, a hard cap per
collect, and backs off on the first 429 / 999 so the hunt never hammers.
It reads what any browser shows; it never signs in, never posts, never
applies.
"""

from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from loguru import logger

from navin.career.sources import (
    MARKETS,
    clean_job_text,
    html_to_text,
    infer_country_iso,
    is_listing_hit,
    stable_job_id,
)

HttpGet = Callable[[str], str]

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
PAGE_SIZE = 25

# One collect never spends more than this many HTTP calls on LinkedIn.
MAX_REQUESTS = 14
# Cards per (title, country) pair; two pages of 25 covers a week of postings.
MAX_PAGES = 2
# Descriptions fetched for the best cards only. Cards already carry title,
# company, location and date, which is enough to score and de-duplicate.
MAX_DETAILS = 8
MIN_INTERVAL_S = 1.0
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# f_TPR: posted within the last week. f_JT: C = contract, F = full-time.
_RECENT = "r604800"
_JOB_TYPE = {"freelance": "C", "jobs": "F"}

_CARD_RE = re.compile(r"<li>(.*?)</li>", re.S)
_URN_RE = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')
_LINK_RE = re.compile(r'<a[^>]+class="base-card__full-link[^"]*"[^>]*href="([^"]+)"', re.S)
_TITLE_RE = re.compile(r'class="base-search-card__title[^"]*"[^>]*>\s*(.*?)\s*</h3>', re.S)
_COMPANY_RE = re.compile(r'class="base-search-card__subtitle[^"]*"[^>]*>(.*?)</h4>', re.S)
_LOCATION_RE = re.compile(r'class="job-search-card__location[^"]*"[^>]*>\s*(.*?)\s*</span>', re.S)
_DATE_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
_DESC_RE = re.compile(r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', re.S)
_CRITERIA_RE = re.compile(
    r'class="description__job-criteria-subheader[^"]*"[^>]*>\s*(.*?)\s*</h3>\s*'
    r'<span[^>]*class="description__job-criteria-text[^"]*"[^>]*>\s*(.*?)\s*</span>',
    re.S,
)
_REMOTE_RE = re.compile(r"\b(remote|t[ée]l[ée]travail|hybrid|hybride|full remote)\b", re.I)


class LinkedInWallError(RuntimeError):
    """LinkedIn answered with a block (429 / 999 / login redirect)."""


def _text(raw: str) -> str:
    return clean_job_text(html_to_text(raw or "")).strip()


def default_http_get(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if "login" in str(resp.geturl() or "") or "authwall" in str(resp.geturl() or ""):
                raise LinkedInWallError("login wall")
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {429, 999, 403}:
            raise LinkedInWallError(f"HTTP {exc.code}") from exc
        if exc.code == 400:
            # LinkedIn answers 400 past the last page of a search.
            return ""
        raise
    except urllib.error.URLError as exc:
        raise LinkedInWallError(f"unreachable: {exc.reason}") from exc


def location_for(country: str) -> str:
    market = MARKETS.get((country or "").strip().upper()) or {}
    return str(market.get("label") or "").strip()


def search_url(keywords: str, location: str, *, track: str = "", start: int = 0) -> str:
    params: dict[str, Any] = {"keywords": keywords, "f_TPR": _RECENT, "start": start}
    if location:
        params["location"] = location
    job_type = _JOB_TYPE.get((track or "").strip().lower())
    if job_type:
        params["f_JT"] = job_type
    return SEARCH_URL + "?" + urllib.parse.urlencode(params)


def parse_cards(body: str) -> list[dict[str, Any]]:
    """Job cards out of one guest search page. Tolerates missing fields."""
    cards: list[dict[str, Any]] = []
    for chunk in _CARD_RE.findall(body or ""):
        urn = _URN_RE.search(chunk)
        link = _LINK_RE.search(chunk)
        title = _TITLE_RE.search(chunk)
        if not link or not title:
            continue
        company = _COMPANY_RE.search(chunk)
        location = _LOCATION_RE.search(chunk)
        posted = _DATE_RE.search(chunk)
        url = html.unescape(link.group(1)).split("?", 1)[0]
        cards.append(
            {
                "job_id": urn.group(1) if urn else "",
                "url": url,
                "title": _text(title.group(1))[:180],
                "company": _text(company.group(1))[:120] if company else "",
                "location": _text(location.group(1))[:120] if location else "",
                "posted_at": (posted.group(1) if posted else "")[:10],
            }
        )
    return cards


def parse_detail(body: str) -> dict[str, Any]:
    """Description + criteria (seniority, employment type, function, industry)."""
    desc = _DESC_RE.search(body or "")
    criteria: dict[str, str] = {}
    for label, value in _CRITERIA_RE.findall(body or ""):
        key = _text(label).lower()
        criteria[key] = _text(value)
    return {
        "description": _text(desc.group(1))[:4000] if desc else "",
        "seniority": criteria.get("seniority level") or criteria.get("niveau hiérarchique") or "",
        "employment_type": criteria.get("employment type") or criteria.get("type d'emploi") or "",
        "function": criteria.get("job function") or criteria.get("fonction") or "",
        "industry": criteria.get("industries") or criteria.get("secteurs") or "",
    }


def _guess_track(card: dict[str, Any], detail: dict[str, Any], wanted: str) -> str:
    hay = f"{card.get('title', '')} {detail.get('employment_type', '')}".lower()
    if any(token in hay for token in ("freelance", "contract", "contractor", "mission", "indépendant")):
        return "freelance"
    return wanted if wanted in {"freelance", "jobs"} else "jobs"


def to_job(card: dict[str, Any], detail: dict[str, Any] | None, *, country: str, track: str) -> dict[str, Any]:
    detail = detail or {}
    title = str(card.get("title") or "")
    url = str(card.get("url") or "")
    location = str(card.get("location") or "")
    iso = infer_country_iso(country, location) or country
    hay = f"{title} {location} {detail.get('description', '')[:400]}"
    return {
        "id": stable_job_id("linkedin", url, title),
        "source": "linkedin",
        "title": title,
        "company": str(card.get("company") or ""),
        "location": location,
        "country": iso,
        "compensation": None,
        "currency": "",
        "stack": [],
        "seniority": str(detail.get("seniority") or ""),
        "remote": "remote" if _REMOTE_RE.search(hay) else "",
        "posted_at": str(card.get("posted_at") or ""),
        "contact": "",
        "description": str(detail.get("description") or ""),
        "url": url,
        "track": _guess_track(card, detail, track),
        "stage": "discovered",
        "ingest": "linkedin_public",
        "attribution": "LinkedIn (public listing)",
        "employment_type": str(detail.get("employment_type") or ""),
        "linkedin_job_id": str(card.get("job_id") or ""),
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


def search_linkedin_jobs(
    *,
    titles: list[str],
    countries: list[str],
    track: str = "",
    http_get: HttpGet | None = None,
    max_requests: int = MAX_REQUESTS,
    max_pages: int = MAX_PAGES,
    max_details: int = MAX_DETAILS,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Public LinkedIn listings for the profile titles in the selected markets.

    Returns {"jobs": [...], "requests": n, "walls": [...], "pairs": n}. A wall
    (429 / 999 / login redirect) stops the whole run: the next collect starts
    fresh, which is what LinkedIn rate limits expect from a polite client.
    """
    getter = http_get or default_http_get
    budget = _Budget(max_requests, MIN_INTERVAL_S if http_get is None else 0.0, sleep)
    wanted_titles = [str(item).strip() for item in titles if str(item).strip()][:3] or ["Data Engineer"]
    wanted_countries = [str(item).strip().upper() for item in countries if str(item).strip()][:4] or [""]
    cards_by_id: dict[str, tuple[dict[str, Any], str]] = {}
    walls: list[str] = []
    pairs = 0
    stopped = False
    for country in wanted_countries:
        location = location_for(country) if country else ""
        for title in wanted_titles:
            pairs += 1
            for page in range(max_pages):
                if not budget.take():
                    stopped = True
                    break
                url = search_url(title, location, track=track, start=page * PAGE_SIZE)
                try:
                    body = getter(url)
                except LinkedInWallError as exc:
                    walls.append(f"LinkedIn {exc} on {location or 'worldwide'} / {title}")
                    stopped = True
                    break
                except (OSError, ValueError) as exc:
                    logger.info("career linkedin search skipped: {}", exc)
                    break
                cards = parse_cards(body)
                for card in cards:
                    key = card.get("job_id") or card.get("url")
                    if key and key not in cards_by_id:
                        cards_by_id[key] = (card, country)
                if len(cards) < PAGE_SIZE:
                    break
            if stopped:
                break
        if stopped:
            break

    jobs: list[dict[str, Any]] = []
    details_left = max_details
    for card, country in cards_by_id.values():
        if is_listing_hit(str(card.get("title") or ""), str(card.get("url") or "")):
            continue
        detail: dict[str, Any] | None = None
        if details_left > 0 and card.get("job_id") and not walls and budget.take():
            details_left -= 1
            try:
                detail = parse_detail(getter(DETAIL_URL.format(job_id=card["job_id"])))
            except LinkedInWallError as exc:
                walls.append(f"LinkedIn {exc} on job {card['job_id']}")
            except (OSError, ValueError) as exc:
                logger.info("career linkedin detail skipped: {}", exc)
        jobs.append(to_job(card, detail, country=country, track=track))
    return {
        "jobs": jobs,
        "requests": max_requests - budget.left,
        "walls": walls,
        "pairs": pairs,
    }
