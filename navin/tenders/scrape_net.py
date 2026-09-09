# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Official-host scrape net: real web_search + scrape tools, no invented notices.

Portals without an API stay empty when search misses or a wall (login, captcha,
Cloudflare, paywall, 403) blocks the page. This module never bypasses a wall.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from loguru import logger

from navin.tenders.normalize import host_of, normalize_tender
from navin.tenders.sources import API_SOURCE_IDS, catalog, official_hosts, web_search_queries

MAX_QUERIES = 6
MAX_RESULTS = 5
MAX_FETCHES = 12
MAX_CHILD_LINKS = 4
# Cap the search+scrape pass so one slow portal cannot eat the whole hunt.
NET_BUDGET_S = 75.0

_HIT_RE = re.compile(
    r"^\s*\d+\.\s+(?P<title>.+?)\s*\n\s+(?P<url>https?://\S+)",
    re.M,
)
_NOTICE_RE = re.compile(
    r"tender|rfp|rfq|procurement|appel|offre|marche|مناقصة|licitaci|"
    r"notice|invitation|bid|dao|consultation|avis|opportunity|contrat|"
    r"for bids|eoi|expression of interest|appel d.offres",
    re.I,
)
_PATH_RE = re.compile(
    r"tender|notice|procurement|appel|offre|licit|rfp|rfq|bid|"
    r"opportunity|avis|marche|مناقصة|consulta",
    re.I,
)
_GENERIC_TITLES = frozenset(
    {
        "",
        "home",
        "accueil",
        "login",
        "sign in",
        "contact",
        "untitled",
        "untitled notice",
        "index",
    }
)
_DENIED_HOSTS = frozenset(
    {
        "marchesonline.com",
        "bidnet.com",
        "bidnetdirect.com",
    }
)
_API_SOURCE_IDS = API_SOURCE_IDS


SearchFn = Callable[[str, int], str]
FetchFn = Callable[[list[str]], list[dict[str, Any]]]


def parse_search_hits(text: str) -> list[dict[str, str]]:
    """Parse WebSearchTool plaintext (`1. Title\\n   url`)."""
    hits: list[dict[str, str]] = []
    if not text or text.startswith("No results"):
        return hits
    for match in _HIT_RE.finditer(text):
        title = " ".join(match.group("title").split())
        url = match.group("url").rstrip(").,;")
        if title and url:
            hits.append({"title": title, "url": url})
    return hits


def host_is_official(host: str, allow: set[str] | None = None) -> bool:
    """True when host is a catalog / site: official host, not a paid aggregator."""
    raw = (host or "").lower().removeprefix("www.")
    if not raw:
        return False
    if any(raw == denied or raw.endswith("." + denied) for denied in _DENIED_HOSTS):
        return False
    allow = allow if allow is not None else official_hosts()
    for token in allow:
        token = token.lower().removeprefix("www.")
        if not token:
            continue
        if raw == token or raw.endswith("." + token):
            return True
    return False


def looks_like_notice(title: str, url: str = "") -> bool:
    blob = f"{title} {url}"
    if _NOTICE_RE.search(blob) or _PATH_RE.search(urlparse(url).path or ""):
        return True
    return False


def is_portal_homepage(url: str) -> bool:
    path = (urlparse(url).path or "").strip("/")
    return path == "" or path.lower() in {"en", "fr", "ar", "home", "index.html", "index.php"}


def _run(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=90)


def default_web_search(query: str, count: int = MAX_RESULTS) -> str:
    from navin.agent.tools.web import WebSearchTool

    tool = WebSearchTool()
    try:
        return str(_run(tool.execute(query=query, count=count)))
    except Exception as exc:
        logger.warning("tenders web_search failed: {}", exc)
        return f"No results for: {query}"


def default_scrape_fetch(urls: list[str]) -> list[dict[str, Any]]:
    """Fetch public pages with the scrape tool. Walls stay walls."""
    from navin.agent.tools.scrape import ScrapeTool, ScrapeToolConfig

    if not urls:
        return []
    workspace = Path(tempfile.mkdtemp(prefix="navin-tenders-scrape-"))
    try:
        tool = ScrapeTool(
            workspace=workspace,
            config=ScrapeToolConfig(
                max_pages=min(len(urls), MAX_FETCHES),
                concurrency=min(4, max(1, len(urls))),
                timeout_seconds=15,
            ),
        )
        raw = _run(
            tool.execute(
                action="fetch",
                urls="\n".join(urls),
                max_pages=len(urls),
            )
        )
        if getattr(raw, "is_error", False):
            logger.warning("tenders scrape fetch error: {}", raw)
            return [{"url": url, "error": str(raw)[:200]} for url in urls]
        payload = json.loads(str(raw)) if isinstance(raw, str) or not isinstance(raw, dict) else raw
        if isinstance(payload, dict):
            return list(payload.get("pages") or [])
        return []
    except Exception as exc:
        logger.warning("tenders scrape fetch failed: {}", exc)
        return [{"url": url, "error": str(exc)[:200]} for url in urls]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _page_wall(page: dict[str, Any]) -> dict[str, Any] | None:
    from navin.agent.tools.scrape import detect_wall

    existing = page.get("wall")
    if isinstance(existing, dict):
        return existing
    return detect_wall(
        status=int(page.get("status") or 0) or None,
        title=str(page.get("title") or ""),
        text=str(page.get("text") or page.get("markdown") or ""),
        html=str(page.get("html") or ""),
        error=str(page.get("error") or "") or None,
        content_type=str(page.get("content_type") or page.get("contentType") or ""),
    )


def _pick_title(fetched: str, searched: str) -> str:
    fetched_c = " ".join((fetched or "").split())
    searched_c = " ".join((searched or "").split())
    if fetched_c.lower() not in _GENERIC_TITLES and looks_like_notice(fetched_c):
        return fetched_c
    if searched_c.lower() not in _GENERIC_TITLES and looks_like_notice(searched_c):
        return searched_c
    if fetched_c.lower() not in _GENERIC_TITLES:
        return fetched_c
    if searched_c.lower() not in _GENERIC_TITLES:
        return searched_c
    return ""


def _api_hosts() -> set[str]:
    hosts: set[str] = set()
    for row in catalog():
        if row["id"] not in _API_SOURCE_IDS:
            continue
        for key in ("url", "api"):
            host = host_of(str(row.get(key) or "")).removeprefix("www.")
            if host:
                hosts.add(host)
    return hosts


def _search_sources(countries: list[str]) -> list[dict[str, Any]]:
    wanted = {c.strip().upper() for c in countries if str(c).strip()}
    rows: list[dict[str, Any]] = []
    for row in catalog():
        if str(row.get("coverage") or "") != "search":
            continue
        country = str(row.get("country") or "").upper()
        if wanted and country not in wanted and country not in {"INTL", "EU"}:
            continue
        rows.append(row)
    return rows


def _source_for_url(
    url: str,
    *,
    search_sources: list[dict[str, Any]],
    query_country: str,
) -> dict[str, Any] | None:
    host = host_of(url).removeprefix("www.")
    if not host:
        return None
    for row in search_sources:
        remote = host_of(str(row.get("url") or "")).removeprefix("www.")
        if remote and (host == remote or host.endswith("." + remote) or remote.endswith("." + host)):
            return row
    iso = (query_country or "").upper()
    for row in search_sources:
        if str(row.get("country") or "").upper() == iso:
            return row
    for row in search_sources:
        if str(row.get("country") or "").upper() == "INTL":
            return row
    return search_sources[0] if search_sources else None


def _pick_queries(queries: list[dict[str, str]], countries: list[str], limit: int) -> list[dict[str, str]]:
    by_country: dict[str, list[dict[str, str]]] = {}
    for row in queries:
        by_country.setdefault(str(row.get("country") or "").upper(), []).append(row)
    order = [c.strip().upper() for c in countries if str(c).strip()]
    if "INTL" not in order:
        order.append("INTL")
    picked: list[dict[str, str]] = []
    seen: set[str] = set()
    for iso in order:
        for row in by_country.get(iso, [])[:2]:
            key = str(row.get("query") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            picked.append(row)
            if len(picked) >= limit:
                return picked
    return picked


def _child_notice_urls(page: dict[str, Any], allow: set[str]) -> list[str]:
    base = str(page.get("url") or page.get("finalUrl") or "")
    host = host_of(base).removeprefix("www.")
    out: list[str] = []
    for raw in page.get("links") or []:
        href = raw if isinstance(raw, str) else str((raw or {}).get("url") or "")
        if not href.startswith("http"):
            continue
        child_host = host_of(href).removeprefix("www.")
        if child_host != host or not host_is_official(child_host, allow):
            continue
        if is_portal_homepage(href):
            continue
        if looks_like_notice("", href) or _PATH_RE.search(href):
            if href not in out:
                out.append(href)
        if len(out) >= MAX_CHILD_LINKS:
            break
    return out


def scrape_official_net(
    *,
    countries: list[str],
    crafts: list[str] | None = None,
    tender_types: list[str] | None = None,
    project_types: list[str] | None = None,
    queries: list[dict[str, str]] | None = None,
    search_fn: SearchFn | None = None,
    fetch_fn: FetchFn | None = None,
    max_queries: int = MAX_QUERIES,
    max_fetches: int = MAX_FETCHES,
) -> dict[str, Any]:
    """Run web_search then scrape on official public hosts. Never invent a notice."""
    search = search_fn or default_web_search
    fetch = fetch_fn or default_scrape_fetch
    query_rows = queries or web_search_queries(
        countries=list(countries or []),
        crafts=list(crafts or []),
        tender_types=list(tender_types or []),
        project_types=list(project_types or []),
    )
    search_sources = _search_sources(list(countries or []))
    allow = official_hosts()
    skip_hosts = _api_hosts()
    by_source: dict[str, dict[str, Any]] = {
        row["id"]: {"count": 0, "detail": "", "walls": []} for row in search_sources
    }
    picked = _pick_queries(query_rows, list(countries or []), max_queries)
    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    deadline = time.monotonic() + NET_BUDGET_S
    for row in picked:
        if time.monotonic() >= deadline:
            logger.warning("tenders scrape_net budget reached - remaining queries skipped")
            break
        text = search(str(row.get("query") or ""), MAX_RESULTS)
        country = str(row.get("country") or "")
        for hit in parse_search_hits(str(text)):
            url = hit["url"]
            host = host_of(url).removeprefix("www.")
            if url in seen_urls or not host_is_official(host, allow):
                continue
            if host in skip_hosts or any(host.endswith("." + h) for h in skip_hosts if h):
                continue
            if is_portal_homepage(url) and not looks_like_notice(hit["title"], url):
                continue
            seen_urls.add(url)
            candidates.append({**hit, "country": country})

    to_fetch = [row["url"] for row in candidates[:max_fetches]]
    pages = fetch(to_fetch) if to_fetch else []
    page_by_url: dict[str, dict[str, Any]] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        for key in (page.get("url"), page.get("finalUrl")):
            if key:
                page_by_url[str(key)] = page
    extra: list[str] = []
    remaining = max_fetches - len(to_fetch)
    if remaining > 0:
        for page in pages:
            if not isinstance(page, dict) or _page_wall(page):
                continue
            for href in _child_notice_urls(page, allow):
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                extra.append(href)
                parent_country = ""
                parent_url = str(page.get("url") or "")
                for cand in candidates:
                    if cand["url"] == parent_url:
                        parent_country = cand.get("country") or ""
                        break
                candidates.append({"title": "", "url": href, "country": parent_country})
                if len(extra) >= min(MAX_CHILD_LINKS, remaining):
                    break
            if len(extra) >= min(MAX_CHILD_LINKS, remaining):
                break
        if extra and time.monotonic() < deadline:
            for page in fetch(extra):
                if not isinstance(page, dict):
                    continue
                for key in (page.get("url"), page.get("finalUrl")):
                    if key:
                        page_by_url[str(key)] = page

    tenders: list[dict[str, Any]] = []
    for cand in candidates:
        url = cand["url"]
        page = page_by_url.get(url) or {}
        wall = _page_wall(page) if page else None
        source = _source_for_url(url, search_sources=search_sources, query_country=cand.get("country") or "")
        sid = str((source or {}).get("id") or "")
        if wall:
            kind = str(wall.get("kind") or "wall")
            if sid and kind not in by_source.get(sid, {}).get("walls", []):
                by_source.setdefault(sid, {"count": 0, "detail": "", "walls": []})
                by_source[sid]["walls"].append(kind)
            continue
        if not page:
            continue
        title = _pick_title(str(page.get("title") or ""), cand.get("title") or "")
        if not title or title.lower() in _GENERIC_TITLES:
            continue
        if not looks_like_notice(title, url) and is_portal_homepage(url):
            continue
        if not sid:
            continue
        text = str(page.get("text") or page.get("markdown") or "")
        notice = normalize_tender(
            {
                "title": title,
                "source_url": url,
                "description": text[:400],
                "country": (source or {}).get("country") or cand.get("country") or "INTL",
                "buyer": (source or {}).get("name") or "",
                "reference": url,
            },
            source_id=sid,
        )
        if notice["title"] == "Untitled notice":
            continue
        tenders.append(notice)
        bucket = by_source.setdefault(sid, {"count": 0, "detail": "", "walls": []})
        bucket["count"] = int(bucket.get("count") or 0) + 1

    for sid, bucket in by_source.items():
        count = int(bucket.get("count") or 0)
        walls = list(bucket.get("walls") or [])
        detail = f"web_search + scrape on official hosts: {count} notice(s)"
        if walls:
            detail = f"{detail}; skipped wall: {', '.join(walls[:4])}"
        if count == 0 and not walls:
            detail = "web_search + scrape on official hosts: 0 notices (honest empty)"
        bucket["detail"] = detail[:240]
    return {"tenders": tenders, "by_source": by_source, "queries_run": [q.get("query") for q in picked]}
