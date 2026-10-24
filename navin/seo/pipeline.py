# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Full SEO pipeline: crawl, on-page audit, schema, robots/sitemap, performance.

Shared by the ``seo`` agent tool and the SEO desk API so both produce the same
audit for the same site.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

from navin.agent.tools.scrape_ops import parse_sitemap_urls
from navin.seo.crawler import SeoCrawler
from navin.seo.models import AuditResult, Confidence, Evidence, Finding, PageRecord, Severity
from navin.seo.onpage import audit_site
from navin.seo.performance import PerformanceClient
from navin.seo.structured import validate_jsonld


def reachable(page: PageRecord | None) -> bool:
    """A fetched file that answered: status 0 is a network failure, not a page."""
    return page is not None and not page.error and 0 < page.status < 400


def declared_sitemaps(robots_text: str, origin: str) -> list[str]:
    """Absolute ``Sitemap:`` URLs listed in robots.txt, in order, deduplicated."""
    found: list[str] = []
    for line in (robots_text or "").splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() != "sitemap":
            continue
        target = urljoin(origin, value.strip())
        if target.startswith(("http://", "https://")) and target not in found:
            found.append(target)
    return found


def _finding(code: str, severity: Severity, title: str, message: str, url: str,
             evidence: Evidence, recommendation: str) -> Finding:
    return Finding(
        code=code, category="indexability", severity=severity, title=title,
        message=message, url=url, evidence=[evidence], recommendation=recommendation,
        confidence=Confidence.HIGH,
    )


def indexability_findings(crawler: SeoCrawler, origin: str) -> list[Finding]:
    """robots.txt and sitemap checks, each problem reported once.

    The sitemap is the one robots.txt declares; without a declaration the
    usual locations (/sitemap.xml, /sitemap_index.xml) are tried.
    """
    findings: list[Finding] = []
    robots_url = urljoin(origin, "robots.txt")
    robots = next(iter(crawler.crawl(robots_url, max_pages=1, max_depth=0)), None)
    declared: list[str] = []
    if not reachable(robots):
        status = robots.status if robots is not None else 0
        findings.append(_finding(
            "robots_unavailable", Severity.HIGH, "Robots Unavailable",
            f"{robots_url} was not available"
            + (" (network error)." if status == 0 else f" (HTTP {status})."),
            robots_url, Evidence(kind="http_status", value=status, source=robots_url),
            "Publish a valid robots.txt and reference the sitemap in it.",
        ))
    else:
        declared = declared_sitemaps(robots.text or robots.html, origin)
        if not declared:
            findings.append(_finding(
                "robots_sitemap_missing", Severity.LOW, "Sitemap not declared in robots.txt",
                "robots.txt contains no Sitemap directive.", robots_url,
                Evidence(kind="robots_text", value=robots.text, source=robots_url),
                "Add an absolute Sitemap directive to robots.txt.",
            ))
    candidates = declared or [urljoin(origin, "sitemap.xml"), urljoin(origin, "sitemap_index.xml")]
    sitemap: PageRecord | None = None
    tried: list[tuple[str, int]] = []
    for candidate in candidates:
        page = next(iter(crawler.crawl(candidate, max_pages=1, max_depth=0)), None)
        if reachable(page):
            sitemap = page
            break
        tried.append((candidate, page.status if page is not None else 0))
    if sitemap is None:
        first_url, first_status = tried[0] if tried else (candidates[0], 0)
        findings.append(_finding(
            "sitemap_unavailable", Severity.HIGH, "Sitemap Unavailable",
            "No sitemap answered: " + ", ".join(
                f"{url} ({'network error' if status == 0 else f'HTTP {status}'})"
                for url, status in tried
            ) + ".",
            first_url, Evidence(kind="http_status", value=first_status, source=first_url),
            "Publish valid sitemap XML and declare it in robots.txt.",
        ))
    elif not parse_sitemap_urls(sitemap.html or sitemap.text):
        findings.append(_finding(
            "sitemap_empty", Severity.HIGH, "Sitemap contains no URLs",
            f"No valid loc entries were parsed from {sitemap.final_url or sitemap.url}.",
            sitemap.url, Evidence(kind="sitemap_url_count", value=0, source=sitemap.url),
            "Publish valid sitemap XML with absolute loc entries.",
        ))
    return findings


def performance_snapshot(performance: PerformanceClient, url: str, origin: str) -> dict[str, Any]:
    """PSI and CrUX, each an honest data_gap when a key is missing or the call fails."""
    try:
        psi = performance.psi(url)
    except RuntimeError as exc:
        psi = {"status": "data_gap", "service": "psi_v5", "reason": str(exc), "metrics": None}
    try:
        crux = performance.crux(origin=origin.rstrip("/"))
    except RuntimeError as exc:
        crux = {"status": "data_gap", "service": "crux_v1", "reason": str(exc), "metrics": None}
    return {"psi": psi, "crux": crux}


def run_pipeline(
    url: str,
    *,
    crawler: SeoCrawler,
    performance: PerformanceClient,
    max_pages: int,
    max_depth: int,
) -> AuditResult:
    pages = crawler.crawl(url, max_pages=max_pages, max_depth=max_depth)
    findings = audit_site(pages)
    for page in pages:
        findings.extend(validate_jsonld(page.html, page.final_url))
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}/"
    findings.extend(indexability_findings(crawler, origin))
    perf = performance_snapshot(performance, url, origin)
    data_gaps = [
        {"service": str(item["service"]), "reason": str(item["reason"])}
        for item in perf.values()
        if item.get("status") == "data_gap"
    ]
    return AuditResult(
        pages=pages,
        findings=findings,
        data_gaps=data_gaps,
        metadata={"performance": perf, "url": url, "origin": origin},
    )
