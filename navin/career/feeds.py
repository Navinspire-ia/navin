# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Public job APIs and RSS feeds: one engine, declarative sources.

Connector 1 of the Career engine (API / RSS -> JSON-LD -> ATS -> HTML adapters).
Every source here publishes its offers on purpose, without an account or key:

- Jobicy       https://jobicy.com/api/v2/remote-jobs (geo, industry, tag; 200 max)
- Remote OK    https://remoteok.com/api (credit Remote OK, keep the listing link)
- Himalayas    https://himalayas.app/jobs/api (salary, seniority, restrictions)
- We Work Remotely  https://weworkremotely.com/remote-jobs.rss (+ programming feed)
- Arbeitnow    https://www.arbeitnow.com/api/job-board-api (Europe, hourly refresh)
- Hacker News  "Ask HN: Who is hiring?" through the public Algolia API

Rules shared by all of them: one request budget per run, a one hour on-disk
cache so a feed is never polled more often than its fair-use asks, the original
listing URL kept as the apply link, and the source named in ``attribution``.
Rows come out on the shared vocabulary (navin.career.normalize) so the desk
filters on contract, remote mode, experience, day rate / salary and currency
the same way for every source.
"""

from __future__ import annotations

import email.utils
import hashlib
import html as html_lib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from navin.career.normalize import (
    contracts_track,
    duration_from_text,
    market_currency,
    normalize_contracts,
    normalize_experience,
    normalize_remote,
    pay_from_numbers,
    pay_from_text,
)
from navin.career.sources import html_to_text, infer_country_iso, is_closed_job_url, stable_job_id

HttpGet = Callable[[str], str]

USER_AGENT = "NavinCareer/1.0 (+https://navin.ai)"
CACHE_TTL_S = 3600.0
MAX_REQUESTS = 16
MAX_ROWS_PER_SOURCE = 120

FEED_IDS: tuple[str, ...] = ("jobicy", "remoteok", "himalayas", "weworkremotely", "arbeitnow", "hn-hiring")

# Navin market ISO -> Jobicy geo slug (https://jobicy.com/api/v2/remote-jobs?get=locations).
JOBICY_GEO: dict[str, str] = {
    "FR": "france",
    "BE": "belgium",
    "CH": "switzerland",
    "LU": "europe",
    "DE": "germany",
    "NL": "netherlands",
    "IE": "ireland",
    "ES": "spain",
    "IT": "italy",
    "PT": "portugal",
    "AT": "austria",
    "SE": "sweden",
    "PL": "poland",
    "GB": "uk",
    "US": "usa",
    "CA": "canada",
    "AU": "australia",
    "AE": "united-arab-emirates",
    "SA": "emea",
    "QA": "emea",
    "KW": "emea",
    "OM": "emea",
    "BH": "emea",
    "MA": "emea",
    "TN": "emea",
    "IN": "apac",
    "REMOTE": "anywhere",
}
_JOBICY_GEO_NAMES: dict[str, str] = {
    "france": "FR",
    "belgium": "BE",
    "switzerland": "CH",
    "germany": "DE",
    "netherlands": "NL",
    "ireland": "IE",
    "spain": "ES",
    "italy": "IT",
    "portugal": "PT",
    "austria": "AT",
    "sweden": "SE",
    "poland": "PL",
    "uk": "GB",
    "united kingdom": "GB",
    "usa": "US",
    "united states": "US",
    "canada": "CA",
    "australia": "AU",
    "uae": "AE",
    "united arab emirates": "AE",
    "india": "IN",
}
_JOBICY_TAG_MAX_TITLES = 3
_JOBICY_MAX_GEOS = 2
_JOBICY_COUNT = 100

_WWR_FEEDS: tuple[str, ...] = (
    "https://weworkremotely.com/remote-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
)
_HN_STORY_URL = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&query=%22who%20is%20hiring%22&hitsPerPage=1"
_HN_COMMENTS_URL = "https://hn.algolia.com/api/v1/search_by_date?tags=comment,story_{story}&hitsPerPage=200&page={page}"
_HN_PAGES = 2
_HN_HEADER_RE = re.compile(r"\s*\|\s*")

_STOP_TOKENS = frozenset(
    {
        "and",
        "or",
        "of",
        "the",
        "de",
        "des",
        "du",
        "la",
        "le",
        "les",
        "en",
        "et",
        "h/f",
        "f/h",
        "m/f",
        "senior",
        "junior",
        "lead",
        "sr",
        "jr",
        "staff",
        "principal",
        "confirmé",
        "confirme",
    }
)


class FeedWallError(RuntimeError):
    """The feed answered with a block, a rate limit or an outage."""


def default_http_get(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 429, 503}:
            raise FeedWallError(f"HTTP {exc.code}") from exc
        if exc.code == 404:
            return ""
        raise
    except (urllib.error.URLError, TimeoutError) as exc:
        raise FeedWallError(f"unreachable: {exc}") from exc


class FeedCache:
    """One JSON file, url -> {at, body}. Keeps every feed under one poll per hour."""

    def __init__(self, path: Path | None, ttl_s: float = CACHE_TTL_S) -> None:
        self.path = path
        self.ttl_s = ttl_s
        self._data: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._data is None:
            self._data = {}
            if self.path and self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raw = {}
                if isinstance(raw, dict):
                    self._data = {str(key): value for key, value in raw.items() if isinstance(value, dict)}
        return self._data

    def get(self, url: str, now: float | None = None) -> str | None:
        entry = self._load().get(_cache_key(url))
        if not entry:
            return None
        stamp = float(entry.get("at") or 0.0)
        if (now if now is not None else time.time()) - stamp > self.ttl_s:
            return None
        body = entry.get("body")
        return body if isinstance(body, str) else None

    def put(self, url: str, body: str, now: float | None = None) -> None:
        data = self._load()
        stamp = now if now is not None else time.time()
        data[_cache_key(url)] = {"at": stamp, "body": body, "url": url}
        # Drop stale entries so the file never grows past one run's worth of feeds.
        for key in [key for key, value in data.items() if stamp - float(value.get("at") or 0.0) > self.ttl_s * 2]:
            data.pop(key, None)
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(data), encoding="utf-8")
            except OSError as exc:  # pragma: no cover - disk full / read-only
                logger.info("career feeds cache not written: {}", exc)


def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def default_cache_path() -> Path | None:
    try:
        from navin.config.paths import get_runtime_subdir

        return get_runtime_subdir("career") / "feeds-cache.json"
    except Exception:  # noqa: BLE001 - cache is optional
        return None


# --------------------------------------------------------------------------- helpers


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    return [text] if text else []


def iso_day(value: Any) -> str:
    """YYYY-MM-DD out of ISO strings, epochs and RFC 2822 dates."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    text = _text(value)
    if text.isdigit() and len(text) >= 9:
        return iso_day(int(text))
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return ""
    return parsed.strftime("%Y-%m-%d") if parsed else ""


def title_tokens(title: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.split(r"[^a-z0-9+#]+", _text(title).lower()):
        token = raw.strip()
        if len(token) < 2 or token in _STOP_TOKENS or token in tokens:
            continue
        tokens.append(token)
    return tokens


def title_matches(text: str, titles: Iterable[str]) -> bool:
    """True when every meaningful token of one wanted title appears in the text."""
    hay = f" {_text(text).lower()} "
    wanted = [title for title in titles if title_tokens(title)]
    if not wanted:
        return True
    for title in wanted:
        tokens = title_tokens(title)
        if not tokens:
            continue
        if all(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", hay) for token in tokens):
            return True
    return False


def feed_row(
    source: str,
    *,
    title: str,
    url: str,
    company: str = "",
    location: str = "",
    country: str = "",
    description: str = "",
    posted_at: Any = "",
    track: str = "",
    contracts: Any = None,
    remote: Any = None,
    level: Any = None,
    pay: dict[str, Any] | None = None,
    stack: Iterable[str] = (),
    attribution: str = "",
    ingest: str = "public_api",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The one row shape every feed produces. None when the offer is unusable."""
    clean_title = _text(title)[:180]
    link = _text(url)
    if not clean_title or not link or is_closed_job_url(link):
        return None
    text = html_to_text(description)[:4000]
    kinds = normalize_contracts(contracts, clean_title)
    level_name, years = normalize_experience(level, clean_title)
    iso = _text(country).upper()
    if not iso:
        iso = infer_country_iso("", location) or "REMOTE"
    months, duration = duration_from_text(f"{clean_title} {text[:600]}")
    seen: set[str] = set()
    skills: list[str] = []
    for item in stack:
        token = _text(item)
        if token and token.lower() not in seen:
            seen.add(token.lower())
            skills.append(token[:40])
    row: dict[str, Any] = {
        "id": stable_job_id(source, link, clean_title),
        "source": source,
        "title": clean_title,
        "company": _text(company)[:80],
        "location": _text(location)[:120],
        "country": iso,
        "stack": skills[:12],
        "seniority": level_name,
        "experience_level": level_name,
        "experience_years_min": years,
        "remote": normalize_remote(remote) or "",
        "posted_at": iso_day(posted_at),
        "contact": "",
        "description": text,
        "url": link,
        "track": contracts_track(kinds, track),
        "stage": "discovered",
        "contracts": kinds,
        "employment_type": ", ".join(kinds),
        "duration": duration,
        "duration_months": months,
        "attribution": attribution or source,
        "ingest": ingest,
    }
    row.update(pay or pay_from_numbers(None, None, country=iso, track=row["track"]))
    if not row.get("currency"):
        row["currency"] = market_currency(iso)
    if extra:
        row.update(extra)
    return row


# --------------------------------------------------------------------------- Jobicy


def jobicy_geos(countries: Iterable[str]) -> list[str]:
    out: list[str] = []
    for iso in countries:
        slug = JOBICY_GEO.get(_text(iso).upper())
        if slug and slug not in out:
            out.append(slug)
    return out[:_JOBICY_MAX_GEOS] or ["anywhere"]


def jobicy_url(*, tag: str = "", geo: str = "", count: int = _JOBICY_COUNT) -> str:
    params: dict[str, str] = {"count": str(max(1, min(200, count)))}
    if geo:
        params["geo"] = geo
    clean_tag = _text(tag)[:50]
    if len(clean_tag) >= 3:
        params["tag"] = clean_tag
    return "https://jobicy.com/api/v2/remote-jobs?" + urllib.parse.urlencode(params)


def plan_jobicy(titles: list[str], countries: list[str]) -> list[tuple[str, str]]:
    """(url, market ISO) pairs: one request per (title tag, geo)."""
    tags = [title for title in titles if len(_text(title)) >= 3][:_JOBICY_TAG_MAX_TITLES] or [""]
    plan: list[tuple[str, str]] = []
    iso_for_geo = {slug: iso for iso, slug in JOBICY_GEO.items() if len(iso) == 2}
    for geo in jobicy_geos(countries):
        market = next((iso for iso in countries if JOBICY_GEO.get(_text(iso).upper()) == geo), iso_for_geo.get(geo, ""))
        for tag in tags:
            plan.append((jobicy_url(tag=tag, geo=geo), _text(market).upper()))
    return plan


def parse_jobicy(body: str, *, market: str = "", track: str = "") -> list[dict[str, Any]]:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return []
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        geo = _text(item.get("jobGeo"))
        geo_parts = [part.strip().lower() for part in geo.split(",") if part.strip()]
        country = ""
        if len(geo_parts) == 1 and geo_parts[0] in _JOBICY_GEO_NAMES:
            country = _JOBICY_GEO_NAMES[geo_parts[0]]
        elif geo_parts == ["anywhere"] or not geo_parts:
            country = "REMOTE"
        elif market and len(market) == 2:
            country = market  # eligible in the searched market (Jobicy filtered on it)
        pay = pay_from_numbers(
            item.get("salaryMin") or item.get("annualSalaryMin"),
            item.get("salaryMax") or item.get("annualSalaryMax"),
            period=_text(item.get("salaryPeriod")) or "yearly",
            currency=_text(item.get("salaryCurrency")),
            country=country or market,
            track=track,
        )
        if not pay.get("currency"):
            pay["currency"] = market_currency(country if len(country) == 2 else market)
        row = feed_row(
            "jobicy",
            title=_text(item.get("jobTitle")),
            url=_text(item.get("url")),
            company=_text(item.get("companyName")),
            location=geo or "Remote",
            country=country or "REMOTE",
            description=_text(item.get("jobDescription")) or _text(item.get("jobExcerpt")),
            posted_at=item.get("pubDate"),
            track=track,
            contracts=item.get("jobType"),
            remote="remote",
            level=_text(item.get("jobLevel")) if _text(item.get("jobLevel")).lower() != "any" else "",
            pay=pay,
            stack=_list(item.get("jobIndustry")),
            attribution="Jobicy",
            extra={"jobicy_id": _text(item.get("id"))},
        )
        if row:
            rows.append(row)
    return rows


# --------------------------------------------------------------------------- Remote OK


def parse_remoteok(body: str, *, titles: list[str], track: str = "") -> list[dict[str, Any]]:
    try:
        payload = json.loads(body or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or "position" not in item:
            continue  # the first element is the legal notice
        title = _text(item.get("position"))
        tags = _list(item.get("tags"))
        if titles and not title_matches(f"{title} {' '.join(tags)}", titles):
            continue
        location = _text(item.get("location"))
        country = infer_country_iso("", location) or "REMOTE"
        pay = pay_from_numbers(item.get("salary_min"), item.get("salary_max"), period="yearly", currency="USD", country=country, track=track)
        row = feed_row(
            "remoteok",
            title=title,
            url=_text(item.get("url")) or _text(item.get("apply_url")),
            company=_text(item.get("company")),
            location=location or "Remote",
            country=country,
            description=_text(item.get("description")),
            posted_at=item.get("date") or item.get("epoch"),
            track=track,
            contracts=tags,
            remote="remote",
            level=title,
            pay=pay,
            stack=tags,
            attribution="Remote OK (listing link required)",
            extra={"remoteok_id": _text(item.get("id"))},
        )
        if row:
            rows.append(row)
    return rows


# --------------------------------------------------------------------------- Himalayas


def himalayas_url(limit: int = 100, offset: int = 0) -> str:
    return f"https://himalayas.app/jobs/api?limit={max(1, min(100, limit))}&offset={max(0, offset)}"


def parse_himalayas(body: str, *, titles: list[str], track: str = "") -> list[dict[str, Any]]:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return []
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"))
        categories = _list(item.get("categories"))
        if titles and not title_matches(f"{title} {' '.join(categories)}", titles):
            continue
        restrictions = _list(item.get("locationRestrictions"))
        location = ", ".join(restrictions[:3]) or "Remote"
        country = infer_country_iso("", restrictions[0]) if len(restrictions) == 1 else ""
        pay = pay_from_numbers(
            item.get("minSalary"),
            item.get("maxSalary"),
            period=_text(item.get("salaryPeriod")) or "yearly",
            currency=_text(item.get("currency")),
            country=country or "REMOTE",
            track=track,
        )
        row = feed_row(
            "himalayas",
            title=title,
            url=_text(item.get("applicationLink")) or _text(item.get("guid")),
            company=_text(item.get("companyName")),
            location=location,
            country=country or "REMOTE",
            description=_text(item.get("description")) or _text(item.get("excerpt")),
            posted_at=item.get("pubDate"),
            track=track,
            contracts=_text(item.get("employmentType")),
            remote="remote",
            level=_list(item.get("seniority")),
            pay=pay,
            stack=[token.replace("-", " ") for token in categories],
            attribution="Himalayas",
        )
        if row:
            rows.append(row)
    return rows


# --------------------------------------------------------------------------- We Work Remotely (RSS)


def _rss_items(body: str) -> list[ET.Element]:
    try:
        root = ET.fromstring(body or "")
    except ET.ParseError:
        return []
    return list(root.iter("item"))


def _child(item: ET.Element, name: str) -> str:
    node = item.find(name)
    return _text(node.text if node is not None else "")


def parse_weworkremotely(body: str, *, titles: list[str], track: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _rss_items(body):
        raw_title = html_lib.unescape(_child(item, "title"))
        company, _, position = raw_title.partition(": ")
        if not position:
            position, company = raw_title, ""
        skills = [token.strip() for token in _child(item, "skills").split(",") if token.strip()]
        category = _child(item, "category")
        if titles and not title_matches(f"{position} {category} {' '.join(skills)}", titles):
            continue
        region = _child(item, "region")
        country_text = _child(item, "country")
        country = infer_country_iso("", country_text) or infer_country_iso("", region) or "REMOTE"
        description = _child(item, "description")
        pay = pay_from_text("", country=country, track=track)
        salary_match = re.search(r"(?:\$|€|£)\s?\d[\d,.]*\s?k?(?:\s*-\s*(?:\$|€|£)?\s?\d[\d,.]*\s?k?)?", description[:800])
        if salary_match:
            # Keep the words after the amount: "per hour", "/ year" decide the period.
            pay = pay_from_text(description[salary_match.start() : salary_match.end() + 24], country=country, track=track)
        row = feed_row(
            "weworkremotely",
            title=position,
            url=_child(item, "link"),
            company=company,
            location=", ".join(part for part in (region, country_text) if part) or "Remote",
            country=country,
            description=description,
            posted_at=_child(item, "pubDate"),
            track=track,
            contracts=_child(item, "type"),
            remote="remote",
            level=position,
            pay=pay,
            stack=skills or ([category] if category else []),
            attribution="We Work Remotely",
            ingest="public_rss",
        )
        if row:
            rows.append(row)
    return rows


# --------------------------------------------------------------------------- Arbeitnow


def arbeitnow_url(page: int = 1) -> str:
    return f"https://www.arbeitnow.com/api/job-board-api?page={max(1, page)}"


def parse_arbeitnow(body: str, *, titles: list[str], track: str = "") -> list[dict[str, Any]]:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"))
        tags = _list(item.get("tags"))
        if titles and not title_matches(f"{title} {' '.join(tags)}", titles):
            continue
        location = _text(item.get("location"))
        country = infer_country_iso("", location) or "DE"
        remote_flag = item.get("remote")
        row = feed_row(
            "arbeitnow",
            title=title,
            url=_text(item.get("url")),
            company=_text(item.get("company_name")),
            location=location or ("Remote" if remote_flag else ""),
            country=country,
            description=_text(item.get("description")),
            posted_at=item.get("created_at"),
            track=track,
            contracts=_list(item.get("job_types")),
            remote="remote" if remote_flag is True else "",
            level=title,
            pay=pay_from_numbers(None, None, country=country, track=track),
            stack=tags,
            attribution="Arbeitnow",
        )
        if row:
            rows.append(row)
    return rows


# --------------------------------------------------------------------------- Hacker News "Who is hiring?"


def hn_story_id(body: str) -> str:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return ""
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        return ""
    for hit in hits:
        if isinstance(hit, dict) and "who is hiring" in _text(hit.get("title")).lower():
            return _text(hit.get("objectID"))
    return ""


def hn_comments_url(story: str, page: int = 0) -> str:
    return _HN_COMMENTS_URL.format(story=urllib.parse.quote(story), page=max(0, page))


def parse_hn_hiring(body: str, *, titles: list[str], track: str = "", story: str = "") -> list[dict[str, Any]]:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return []
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        return []
    rows: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        if story and _text(hit.get("parent_id")) != story:
            continue  # replies to a posting, not a posting
        text = html_to_text(_text(hit.get("comment_text")))
        if not text:
            continue
        header = text.split("\n", 1)[0][:300]
        parts = [part.strip() for part in _HN_HEADER_RE.split(header) if part.strip()]
        if len(parts) < 2:
            continue
        company = parts[0][:80]
        role = parts[1][:180]
        rest = " | ".join(parts[2:])
        if titles and not title_matches(f"{role} {rest} {text[:400]}", titles):
            continue
        location = next((part for part in parts[2:] if not re.search(r"remote|onsite|on-site|hybrid|full[- ]time|contract|\$|€|£", part, re.I)), "")
        country = infer_country_iso("", f"{location} {rest}") or "REMOTE"
        pay_text = next((part for part in parts[2:] if re.search(r"\$|€|£|\d+k", part, re.I)), "")
        pay = pay_from_text(pay_text, country=country, track=track) if pay_text else pay_from_numbers(None, None, country=country, track=track)
        object_id = _text(hit.get("objectID"))
        row = feed_row(
            "hn-hiring",
            title=role,
            url=f"https://news.ycombinator.com/item?id={object_id}" if object_id else "",
            company=company,
            location=location or ("Remote" if re.search(r"remote", rest, re.I) else ""),
            country=country,
            description=text,
            posted_at=_text(hit.get("created_at")) or hit.get("created_at_i"),
            track=track,
            contracts=rest,
            remote=rest,
            level=f"{role} {rest}",
            pay=pay,
            stack=[],
            attribution="Hacker News (Ask HN: Who is hiring?)",
            extra={"hn_id": object_id},
        )
        if row:
            rows.append(row)
    return rows


# --------------------------------------------------------------------------- engine


class _Budget:
    def __init__(self, limit: int) -> None:
        self.limit = max(0, int(limit))
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def _fetch(url: str, *, http_get: HttpGet, cache: FeedCache | None, budget: _Budget, walls: list[str], label: str) -> str | None:
    """Body from cache or network. None when blocked, out of budget or empty."""
    if cache is not None:
        cached = cache.get(url)
        if cached is not None:
            return cached
    if not budget.take():
        walls.append(f"{label}: request budget reached")
        return None
    try:
        body = http_get(url)
    except FeedWallError as exc:
        walls.append(f"{label}: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 - a broken feed never breaks the run
        walls.append(f"{label}: {exc}")
        return None
    if body and cache is not None:
        cache.put(url, body)
    return body or None


def collect_feeds(
    *,
    titles: list[str],
    countries: list[str],
    track: str = "",
    sources: Iterable[str] | None = None,
    http_get: HttpGet | None = None,
    cache: FeedCache | None = None,
    max_requests: int = MAX_REQUESTS,
) -> dict[str, Any]:
    """Run every enabled feed once. Returns jobs, requests, walls and per-source counts."""
    wanted = [_text(title) for title in titles if _text(title)]
    markets = [_text(iso).upper() for iso in countries if _text(iso)]
    enabled = [sid for sid in (sources if sources is not None else FEED_IDS) if sid in FEED_IDS]
    getter = http_get or default_http_get
    store = cache if cache is not None else FeedCache(default_cache_path())
    budget = _Budget(max_requests)
    walls: list[str] = []
    by_source: dict[str, int] = {}
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push(rows: list[dict[str, Any]], sid: str) -> None:
        count = 0
        for row in rows:
            if count >= MAX_ROWS_PER_SOURCE:
                break
            key = row.get("url", "")
            if key in seen:
                continue
            seen.add(key)
            jobs.append(row)
            count += 1
        by_source[sid] = by_source.get(sid, 0) + count

    if "jobicy" in enabled:
        for url, market in plan_jobicy(wanted, markets):
            body = _fetch(url, http_get=getter, cache=store, budget=budget, walls=walls, label="Jobicy")
            if body is None:
                break
            rows = parse_jobicy(body, market=market, track=track)
            push([row for row in rows if not wanted or title_matches(f"{row['title']} {' '.join(row['stack'])}", wanted)], "jobicy")
    if "remoteok" in enabled:
        body = _fetch("https://remoteok.com/api", http_get=getter, cache=store, budget=budget, walls=walls, label="Remote OK")
        if body:
            push(parse_remoteok(body, titles=wanted, track=track), "remoteok")
    if "himalayas" in enabled:
        body = _fetch(himalayas_url(), http_get=getter, cache=store, budget=budget, walls=walls, label="Himalayas")
        if body:
            push(parse_himalayas(body, titles=wanted, track=track), "himalayas")
    if "weworkremotely" in enabled:
        for url in _WWR_FEEDS:
            body = _fetch(url, http_get=getter, cache=store, budget=budget, walls=walls, label="We Work Remotely")
            if body is None:
                break
            push(parse_weworkremotely(body, titles=wanted, track=track), "weworkremotely")
    if "arbeitnow" in enabled:
        body = _fetch(arbeitnow_url(1), http_get=getter, cache=store, budget=budget, walls=walls, label="Arbeitnow")
        if body:
            push(parse_arbeitnow(body, titles=wanted, track=track), "arbeitnow")
    if "hn-hiring" in enabled:
        body = _fetch(_HN_STORY_URL, http_get=getter, cache=store, budget=budget, walls=walls, label="Hacker News")
        story = hn_story_id(body or "")
        if story:
            for page in range(_HN_PAGES):
                page_body = _fetch(hn_comments_url(story, page), http_get=getter, cache=store, budget=budget, walls=walls, label="Hacker News")
                if page_body is None:
                    break
                push(parse_hn_hiring(page_body, titles=wanted, track=track, story=story), "hn-hiring")
    for sid in enabled:
        by_source.setdefault(sid, 0)
    return {
        "jobs": jobs,
        "requests": budget.used,
        "walls": walls,
        "by_source": by_source,
        "sources": enabled,
    }
