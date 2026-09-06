"""Collect from official APIs first, then web_search + scrape on official hosts."""

from __future__ import annotations

import json
import os
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

from navin.tenders.brief import FetchBrief, build_brief
from navin.tenders.fetchers import (
    FETCHERS,
    country_code,
    from_sam,
    from_ted,
    from_world_bank,
    localized,
    run_fetcher,
)
from navin.tenders.http import get_json, post_json
from navin.tenders.normalize import host_of, looks_like_notice, normalize_tender
from navin.tenders.profile import normalize_custom_sources
from navin.tenders.scrape_net import scrape_official_net
from navin.tenders.sources import (
    catalog,
    enrich_source,
    source_by_id,
    sources_for_countries,
    web_search_queries,
)

# Back-compat aliases used by unit tests.
_get_json = get_json
_post_json = post_json
_country_code = country_code
_localized = localized
_from_ted = from_ted
_from_world_bank = from_world_bank


def _report(
    source_id: str,
    *,
    ok: bool,
    detail: str,
    count: int = 0,
    kind: str,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "ok": ok,
        "detail": detail,
        "count": count,
        "kind": kind,
    }


def collect(
    *,
    countries: list[str],
    extra_enabled: list[str] | None = None,
    crafts: list[str] | None = None,
    tender_types: list[str] | None = None,
    project_types: list[str] | None = None,
    use_tools: bool = True,
    source_ids: list[str] | None = None,
    custom_sources: list[dict[str, Any]] | None = None,
    sam_key: str | None = None,
    brief: FetchBrief | None = None,
    collect_days: int | None = None,
) -> dict[str, Any]:
    """Fetch every wired official feed, then scrape official public hosts.

    ``use_tools=False`` keeps the search-net reports as query hints only
    (live API isolation tests). Default Collect uses web_search + scrape.
    Official APIs are queried with the brief (CPV, full text, country,
    recency) so the book fills with on-craft notices, not the newest 25 of
    a whole country.
    """
    if brief is None:
        brief = build_brief(
            countries=list(countries or []),
            crafts=list(crafts or []),
            project_types=list(project_types or []),
            tender_types=list(tender_types or []),
            days=int(collect_days or 30),
        )
    wanted = [enrich_source(row) for row in sources_for_countries(countries)]
    enabled = {row["id"] for row in wanted}
    if extra_enabled:
        enabled.update(s.strip() for s in extra_enabled if s.strip())
    selected = {s.strip() for s in (source_ids or []) if str(s).strip()}
    tenders: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    fetched: set[str] = set()

    fetchers = dict(FETCHERS)
    sam = (sam_key or os.environ.get("SAM_API_KEY") or os.environ.get("SAM_GOV_API_KEY") or "").strip()
    if sam and not (os.environ.get("SAM_API_KEY") or os.environ.get("SAM_GOV_API_KEY")):
        os.environ["SAM_API_KEY"] = sam
    if not (os.environ.get("SAM_API_KEY") or os.environ.get("SAM_GOV_API_KEY")):
        fetchers.pop("sam-gov", None)
    else:
        fetchers["sam-gov"] = from_sam
    jobs: list[tuple[str, Any]] = []
    for source_id, fetcher in fetchers.items():
        meta = enrich_source(source_by_id(source_id) or {"id": source_id, "country": ""})
        if source_id not in enabled and meta.get("country") not in {"INTL", "EU"}:
            continue
        if selected and source_id not in selected:
            continue
        fetched.add(source_id)
        jobs.append((source_id, fetcher))
    fetched_rows: dict[str, tuple[bool, str, list[dict[str, Any]]]] = {}
    if jobs:
        def _one(fetcher: Any) -> tuple[bool, str, list[dict[str, Any]]]:
            try:
                rows, detail = run_fetcher(fetcher, brief)
                return True, str(detail), list(rows or [])
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                return False, str(exc)[:240], []

        workers = min(8, max(1, len(jobs)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="navin-tenders-fetch") as pool:
            futs = {source_id: pool.submit(_one, fetcher) for source_id, fetcher in jobs}
            for source_id, fut in futs.items():
                try:
                    fetched_rows[source_id] = fut.result()
                except Exception as exc:
                    logger.warning("tenders collect {} failed: {}", source_id, exc)
                    fetched_rows[source_id] = (False, str(exc)[:240], [])
    for source_id, _fetcher in jobs:
        ok, detail, rows = fetched_rows.get(source_id, (False, "fetch missed", []))
        if ok:
            tenders.extend(rows)
            reports.append(_report(source_id, ok=True, detail=detail, count=len(rows), kind="fetch"))
        else:
            logger.warning("tenders collect {} failed: {}", source_id, detail)
            reports.append(_report(source_id, ok=False, detail=detail, count=0, kind="fetch"))

    queries = web_search_queries(
        countries=list(countries or []),
        crafts=list(crafts or []),
        tender_types=list(tender_types or []),
        project_types=list(project_types or []),
    )
    wanted_countries = {str(row.get("country") or "").upper() for row in wanted}
    for row in catalog():
        sid = str(row["id"])
        if sid in fetched:
            continue
        country = str(row.get("country") or "").upper()
        if country not in wanted_countries and country not in {"INTL", "EU"}:
            reports.append(
                _report(
                    sid,
                    ok=True,
                    detail=f"catalogued; {country} is outside the current profile (no silent drop)",
                    kind="catalog",
                )
            )
            continue
        if selected and sid not in selected:
            reports.append(
                _report(
                    sid,
                    ok=True,
                    detail="catalogued; not selected in company setup (no silent drop)",
                    kind="catalog",
                )
            )
            continue
        coverage = str(row.get("coverage") or "search")
        if coverage == "covered":
            cover = str(row.get("covered_by") or "ted")
            reports.append(
                _report(
                    sid,
                    ok=True,
                    detail=f"covered by {cover} ingest (national portal is not a second feed)",
                    kind="covered",
                )
            )
            continue
        if coverage == "key":
            reports.append(
                _report(
                    sid,
                    ok=False,
                    detail="official API needs SAM_API_KEY; portal stays public, no invented notices",
                    kind="key",
                )
            )
            continue
        iso = str(row.get("country") or "")
        local_q = [q["query"] for q in queries if q.get("country") == iso][:2]
        hint = "; ".join(local_q) if local_q else "official-host web_search + scrape"
        reports.append(
            _report(
                sid,
                ok=True,
                detail=f"search net: {hint}"[:240],
                kind="search",
            )
        )

    if use_tools:
        net = scrape_official_net(
            countries=list(countries or []),
            crafts=list(crafts or []),
            tender_types=list(tender_types or []),
            project_types=list(project_types or []),
            queries=queries,
        )
        tenders.extend(net.get("tenders") or [])
        by_source = net.get("by_source") or {}
        for report in reports:
            if report.get("kind") != "search":
                continue
            info = by_source.get(report["source_id"]) or {}
            if info.get("detail"):
                report["detail"] = str(info["detail"])[:240]
            report["count"] = int(info.get("count") or 0)

    custom_reports = _collect_custom(custom_sources, tenders)

    merged: dict[str, dict[str, Any]] = {}
    for row in tenders:
        if not row.get("id") or not looks_like_notice(row):
            continue
        merged[row["id"]] = row
    if selected:
        custom_ids = {str(row.get("id") or "") for row in normalize_custom_sources(custom_sources)}
        extra_hosts = {str(item).strip().lower() for item in (extra_enabled or []) if str(item).strip()}
        filtered: dict[str, dict[str, Any]] = {}
        for row in merged.values():
            sid = str(row.get("source_id") or "")
            host = host_of(str(row.get("source_url") or ""))
            if sid in selected or sid in custom_ids or host in extra_hosts:
                filtered[row["id"]] = row
        merged = filtered
    discovered: list[dict[str, Any]] = []
    known_hosts = {host_of(str(s.get("url") or "")) for s in wanted}
    known_hosts.update(host_of(str(s.get("api") or "")) for s in wanted)
    for row in merged.values():
        host = host_of(str(row.get("source_url") or ""))
        if host and host not in known_hosts and not any(host.endswith(k) for k in known_hosts if k):
            discovered.append(
                {
                    "host": host,
                    "url": f"https://{host}/",
                    "hits": 1,
                    "title": row.get("title"),
                }
            )
    return {
        "tenders": list(merged.values()),
        "reports": reports,
        "custom_reports": custom_reports,
        "discovered_sources": discovered,
        "queries": queries,
        "brief": brief.public(),
    }


def _collect_custom(
    custom_sources: list[dict[str, Any]] | None,
    tenders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for row in normalize_custom_sources(custom_sources):
        sid = str(row.get("id") or "custom")
        ingest = str(row.get("ingest") or "html")
        endpoint = str(row.get("api") or row.get("url") or "").strip()
        if ingest != "api" or not endpoint:
            reports.append(
                _report(
                    sid,
                    ok=True,
                    detail=f"custom HTML source stored; collect uses official scrape on {row.get('url')}",
                    kind="custom",
                )
            )
            continue
        try:
            payload = get_json(endpoint)
            rows = _notices_from_payload(payload, sid)
            tenders.extend(rows)
            reports.append(_report(sid, ok=True, detail=f"custom API {len(rows)} notice(s)", count=len(rows), kind="custom"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("tenders custom source {} failed: {}", sid, exc)
            reports.append(_report(sid, ok=False, detail=str(exc)[:240], count=0, kind="custom"))
    return reports


def _notices_from_payload(payload: Any, source_id: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        raw = payload.get("results") or payload.get("notices") or payload.get("data") or payload.get("items")
        items = raw if isinstance(raw, list) else []
    else:
        items = []
    out: list[dict[str, Any]] = []
    for item in items[:80]:
        if not isinstance(item, dict):
            continue
        row = normalize_tender(item, source_id=source_id)
        title = str(row.get("title") or "").strip()
        if not title or title.lower() == "untitled notice":
            continue
        out.append(row)
    return out
