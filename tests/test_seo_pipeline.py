# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""robots.txt / sitemap checks of the SEO pipeline, one finding per problem."""

from __future__ import annotations

from navin.seo.crawler import SeoCrawler
from navin.seo.performance import PerformanceClient
from navin.seo.pipeline import declared_sitemaps, indexability_findings, run_pipeline

ORIGIN = "https://site.test/"
SITEMAP = '<?xml version="1.0"?><urlset><url><loc>https://site.test/</loc></url></urlset>'


def _crawler(responses: dict[str, tuple[int, str]]) -> SeoCrawler:
    """Each URL answers (status, body); status 0 is a network failure."""

    def fetch(_client, url):
        status, body = responses.get(url, (404, "not found"))
        record = {"url": url, "finalUrl": url, "status": status, "text": body, "html": body}
        if status == 0:
            record.update(status=0, error="network unreachable", text="", html="")
        return record

    return SeoCrawler(fetch=fetch, respect_robots=False)


def _codes(findings) -> list[str]:
    return sorted(item.code for item in findings)


def test_network_failure_is_one_finding_each_not_two() -> None:
    findings = indexability_findings(_crawler({
        ORIGIN + "robots.txt": (0, ""),
        ORIGIN + "sitemap.xml": (0, ""),
        ORIGIN + "sitemap_index.xml": (0, ""),
    }), ORIGIN)
    assert _codes(findings) == ["robots_unavailable", "sitemap_unavailable"]
    assert "network error" in findings[0].message


def test_sitemap_declared_in_robots_is_used_instead_of_the_default_path() -> None:
    findings = indexability_findings(_crawler({
        ORIGIN + "robots.txt": (200, "User-agent: *\nSitemap: /wp-sitemap.xml\n"),
        ORIGIN + "wp-sitemap.xml": (200, SITEMAP),
    }), ORIGIN)
    assert findings == []


def test_sitemap_index_is_tried_when_robots_declares_none() -> None:
    findings = indexability_findings(_crawler({
        ORIGIN + "robots.txt": (200, "User-agent: *\nDisallow:\n"),
        ORIGIN + "sitemap_index.xml": (200, SITEMAP),
    }), ORIGIN)
    assert _codes(findings) == ["robots_sitemap_missing"]


def test_empty_sitemap_and_http_errors_are_reported_once() -> None:
    empty = indexability_findings(_crawler({
        ORIGIN + "robots.txt": (200, "Sitemap: https://site.test/sitemap.xml"),
        ORIGIN + "sitemap.xml": (200, "<urlset></urlset>"),
    }), ORIGIN)
    assert _codes(empty) == ["sitemap_empty"]
    missing = indexability_findings(_crawler({ORIGIN + "robots.txt": (500, "boom")}), ORIGIN)
    assert _codes(missing) == ["robots_unavailable", "sitemap_unavailable"]
    assert "HTTP 500" in missing[0].message


def test_declared_sitemaps_are_absolute_and_unique() -> None:
    text = "Sitemap: /a.xml\nsitemap:https://site.test/a.xml\nSitemap: https://cdn.test/b.xml\n"
    assert declared_sitemaps(text, ORIGIN) == ["https://site.test/a.xml", "https://cdn.test/b.xml"]


def test_pipeline_reports_performance_gaps_honestly(monkeypatch) -> None:
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    monkeypatch.delenv("CRUX_API_KEY", raising=False)
    audit = run_pipeline(
        ORIGIN,
        crawler=_crawler({
            ORIGIN: (200, "<html><head><title>Home page title here</title></head><body><h1>Hi</h1></body></html>"),
            ORIGIN + "robots.txt": (200, "Sitemap: https://site.test/sitemap.xml"),
            ORIGIN + "sitemap.xml": (200, SITEMAP),
        }),
        performance=PerformanceClient(psi_api_key=None, crux_api_key=None),
        max_pages=3,
        max_depth=0,
    )
    services = {gap["service"] for gap in audit.data_gaps}
    assert {"psi_v5", "crux_v1"} <= services
    assert not any(f.code.startswith(("robots_", "sitemap_")) for f in audit.findings)
