"""Open-host scrape net: real web_search + scrape tools. Never LinkedIn.

Closed boards stay snippets or paste-import. A login / captcha / Cloudflare
wall is recorded and skipped. This module never bypasses a wall.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from navin.career.sources import (
    host_of,
    is_closed_job_url,
    is_linkedin_url,
    is_open_scrape_url,
    scrape_search_queries,
    stable_job_id,
)

MAX_QUERIES = 10
MAX_RESULTS = 5
MAX_FETCHES = 8

_HIT_RE = re.compile(
    r"^\s*\d+\.\s+(?P<title>.+?)\s*\n\s+(?P<url>https?://\S+)",
    re.M,
)
_JOB_RE = re.compile(
    r"job|hiring|career|engineer|developer|offre|emploi|mission|freelance|"
    r"contract|recrut|vacanc|poste|data|remote",
    re.I,
)

SearchFn = Callable[[str, int], str]
FetchFn = Callable[[list[str]], list[dict[str, Any]]]


def parse_search_hits(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    if not text or str(text).startswith("No results"):
        return hits
    for match in _HIT_RE.finditer(text):
        title = " ".join(match.group("title").split())
        url = match.group("url").rstrip(").,;")
        if title and url:
            hits.append({"title": title, "url": url})
    return hits


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
        logger.warning("career web_search failed: {}", exc)
        return f"No results for: {query}"


def default_scrape_fetch(urls: list[str]) -> list[dict[str, Any]]:
    """Fetch public open-host pages with the scrape tool. Walls stay walls."""
    from navin.agent.tools.scrape import ScrapeTool, ScrapeToolConfig

    allowed = [url for url in urls if is_open_scrape_url(url)]
    blocked = [url for url in urls if url not in allowed]
    if blocked:
        logger.info("career scrape refused closed urls: {}", blocked[:6])
    if not allowed:
        return [{"url": url, "error": "closed_board"} for url in blocked]
    workspace = Path(tempfile.mkdtemp(prefix="navin-career-scrape-"))
    try:
        tool = ScrapeTool(
            workspace=workspace,
            config=ScrapeToolConfig(
                max_pages=min(len(allowed), MAX_FETCHES),
                concurrency=min(4, max(1, len(allowed))),
                timeout_seconds=15,
            ),
        )
        raw = _run(
            tool.execute(
                action="fetch",
                urls="\n".join(allowed),
                max_pages=len(allowed),
            )
        )
        if getattr(raw, "is_error", False):
            logger.warning("career scrape fetch error: {}", raw)
            return [{"url": url, "error": str(raw)[:200]} for url in allowed]
        payload = json.loads(str(raw)) if isinstance(raw, str) or not isinstance(raw, dict) else raw
        pages = list(payload.get("pages") or []) if isinstance(payload, dict) else []
        return pages
    except Exception as exc:
        logger.warning("career scrape fetch failed: {}", exc)
        return [{"url": url, "error": str(exc)[:200]} for url in allowed]
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


def _source_for_host(url: str) -> str:
    host = host_of(url)
    if "greenhouse" in host:
        return "greenhouse"
    if "lever" in host:
        return "lever"
    if "ashby" in host:
        return "ashby"
    if "workable" in host:
        return "workable"
    if "smartrecruiters" in host:
        return "smartrecruiters"
    if "usajobs" in host:
        return "usajobs"
    if "francetravail" in host:
        return "france-travail"
    if "remotive" in host:
        return "remotive"
    if "weworkremotely" in host:
        return "weworkremotely"
    if "remoteok" in host:
        return "remoteok"
    if "arbeitnow" in host:
        return "arbeitnow"
    if "himalayas" in host:
        return "himalayas"
    return "scrape"


def _job_from_page(page: dict[str, Any], *, fallback_title: str, country: str, track: str) -> dict[str, Any] | None:
    url = str(page.get("url") or page.get("finalUrl") or "").strip()
    if not url or not is_open_scrape_url(url):
        return None
    if is_closed_job_url(url) or is_linkedin_url(url):
        return None
    title = " ".join(str(page.get("title") or fallback_title or "").split())[:180]
    text = str(page.get("text") or page.get("markdown") or "")[:4000]
    if not title or not _JOB_RE.search(f"{title} {text}"):
        return None
    source = _source_for_host(url)
    return {
        "id": stable_job_id(source, url, title),
        "source": source,
        "title": title,
        "company": "",
        "location": "",
        "country": country,
        "description": text,
        "url": url,
        "track": track,
        "stage": "discovered",
        "remote": "remote" if "remote" in f"{title} {text}".lower() else "",
        "ingest": "scrape_open",
        "attribution": "scrape tool",
    }


def scrape_open_net(
    *,
    titles: list[str],
    countries: list[str],
    track: str = "freelance",
    search_fn: SearchFn | None = None,
    fetch_fn: FetchFn | None = None,
    max_queries: int = MAX_QUERIES,
    max_fetches: int = MAX_FETCHES,
) -> dict[str, Any]:
    """web_search then scrape on open public hosts. Never invent an offer."""
    search = search_fn or default_web_search
    fetch = fetch_fn or default_scrape_fetch
    wanted_track = track if track in {"freelance", "jobs"} else "freelance"
    picked = scrape_search_queries(
        titles=titles or ["Data Engineer"],
        countries=countries or ["FR"],
        track=wanted_track,
    )[:max_queries]
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    refused: list[str] = []
    for row in picked:
        text = search(str(row.get("query") or ""), MAX_RESULTS)
        country = str(row.get("country") or "")
        for hit in parse_search_hits(str(text)):
            url = hit["url"]
            if url in seen:
                continue
            seen.add(url)
            if is_closed_job_url(url) or is_linkedin_url(url) or not is_open_scrape_url(url):
                refused.append(url)
                continue
            candidates.append({**hit, "country": country})

    to_fetch = [row["url"] for row in candidates[:max_fetches]]
    pages = fetch(to_fetch) if to_fetch else []
    page_by_url: dict[str, dict[str, Any]] = {}
    walls: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        wall = _page_wall(page)
        if wall:
            walls.append(str(wall.get("kind") or "wall"))
            continue
        for key in (page.get("url"), page.get("finalUrl")):
            if key:
                page_by_url[str(key)] = page

    jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for cand in candidates:
        page = page_by_url.get(cand["url"])
        if not page:
            continue
        job = _job_from_page(
            page,
            fallback_title=cand.get("title") or "",
            country=cand.get("country") or "",
            track=wanted_track,
        )
        if not job or job["id"] in seen_ids:
            continue
        seen_ids.add(job["id"])
        jobs.append(job)

    return {
        "jobs": jobs,
        "queries_run": [row.get("query") for row in picked],
        "refused": refused[:12],
        "walls": walls[:8],
        "fetched": len(page_by_url),
    }
