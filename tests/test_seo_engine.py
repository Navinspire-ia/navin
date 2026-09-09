# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""SEO engine contracts with mocked transports and a small site fixture."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import httpx

from navin.agent.loop import AgentLoop
from navin.agent.tools.seo import SeoTool, SeoToolConfig
from navin.seo.crawler import SeoCrawler
from navin.seo.models import AuditResult, Confidence, Finding, PageRecord, Severity
from navin.seo.onpage import audit_site
from navin.seo.performance import PerformanceClient
from navin.seo.report import render_html, render_json, render_markdown
from navin.seo.scoring import health_score
from navin.seo.serp import (
    DataForSeoAdapter,
    SemrushAdapter,
    SerpResult,
    SerpSnapshot,
    SerpStore,
)
from navin.seo.structured import extract_jsonld, validate_jsonld

SITE = {
    "/": """<html lang="en"><head><title>Fixture home page</title>
    <meta name="description" content="Fixture description">
    <link rel="canonical" href="https://site.test/">
    <link rel="alternate" hreflang="fr" href="/fr"></head>
    <body><h1>Home</h1><a href="/about">About</a></body></html>""",
    "/about": """<html><head><title>Fixture about page</title></head>
    <body><h1>About</h1></body></html>""",
}


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = SITE.get(request.url.path)
        return httpx.Response(
            200 if body else 404,
            text=body or "not found",
            headers={"content-type": "text/html"},
            request=request,
        )

    return httpx.MockTransport(handler)


def test_crawler_uses_mocked_transport_and_discovers_internal_links() -> None:
    pages = SeoCrawler(
        transport=_transport(), respect_robots=False, allow_private_network=True
    ).crawl("https://site.test/", max_pages=5, max_depth=1)
    assert [page.url for page in pages] == ["https://site.test/", "https://site.test/about"]
    assert pages[0].canonical == "https://site.test/"
    assert pages[0].hreflang == {"fr": "https://site.test/fr"}


def test_audit_covers_onpage_canonical_h1_and_internal_links() -> None:
    pages = SeoCrawler(
        transport=_transport(), respect_robots=False, allow_private_network=True
    ).crawl("https://site.test/", max_pages=5, max_depth=1)
    codes = {finding.code for finding in audit_site(pages)}
    assert "canonical_missing" in codes
    assert "meta_description_missing" in codes
    assert "internal_orphan" not in codes


def test_schema_rules_include_parser_and_type_specific_evidence() -> None:
    html = """<script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Article","headline":"Hello"}
    </script><script type="application/ld+json">{bad}</script>"""
    documents, errors = extract_jsonld(html)
    assert documents[0]["@type"] == "Article"
    assert errors
    findings = validate_jsonld(html, "https://site.test/article")
    assert {finding.code for finding in findings} == {
        "jsonld_invalid", "jsonld_required_missing"
    }
    assert all(finding.evidence for finding in findings)


def test_performance_data_gap_and_mocked_psi_crux() -> None:
    missing = PerformanceClient(psi_api_key="", crux_api_key="")
    assert missing.psi("https://site.test")["status"] == "data_gap"
    assert missing.crux(url="https://site.test")["status"] == "data_gap"

    def handler(request: httpx.Request) -> httpx.Response:
        if "pagespeedonline" in request.url.path:
            return httpx.Response(200, json={
                "lighthouseResult": {
                    "categories": {"performance": {"score": 0.91}},
                    "audits": {"largest-contentful-paint": {"numericValue": 1234}},
                }
            })
        return httpx.Response(200, json={"record": {"metrics": {"largest_contentful_paint": {}}}})

    client = PerformanceClient(
        psi_api_key="test", crux_api_key="test", transport=httpx.MockTransport(handler)
    )
    assert client.psi("https://site.test")["performance_score"] == 0.91
    assert "largest_contentful_paint" in client.crux(url="https://site.test")["metrics"]


def test_serp_store_is_append_only_and_requires_provenance(tmp_path: Path) -> None:
    snapshot = SerpSnapshot(
        captured_at="2026-01-01T00:00:00+00:00",
        keyword="fixture",
        location="US",
        language="en",
        source="fixture-provider",
        confidence=Confidence.HIGH,
        results=[SerpResult(
            position=1, url="https://site.test/", source="fixture-provider",
            confidence=Confidence.HIGH,
        )],
    )
    store = SerpStore(tmp_path / "history.jsonl")
    store.append(snapshot)
    store.append(snapshot.model_copy(update={"captured_at": "2026-01-02T00:00:00+00:00"}))
    assert len(store.history(keyword="fixture")) == 2
    assert (tmp_path / "history.jsonl").read_text(encoding="utf-8").count("\n") == 2


def test_serp_adapters_keep_provider_source_and_observed_position() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "dataforseo" in request.url.host:
            return httpx.Response(200, json={"tasks": [{"result": [{"items": [{
                "rank_absolute": 3, "url": "https://site.test/", "title": "Fixture",
                "type": "organic",
            }]}]}]})
        return httpx.Response(
            200, text="Domain;Url;Position;Title\nsite.test;https://site.test/;4;Fixture\n"
        )

    transport = httpx.MockTransport(handler)
    dataforseo = DataForSeoAdapter("login", "password", transport=transport).snapshot(
        "fixture", location="United States", language="en"
    )
    semrush = SemrushAdapter("key", transport=httpx.MockTransport(handler)).snapshot(
        "fixture", location="us", language="en"
    )
    assert (dataforseo.results[0].position, dataforseo.results[0].source) == (3, "dataforseo")
    assert (semrush.results[0].position, semrush.results[0].source) == (4, "semrush")


def test_scoring_and_reports_are_deterministic(tmp_path: Path) -> None:
    finding = Finding(
        code="title_missing", category="onpage", severity=Severity.HIGH,
        title="Missing title", message="Missing", url="https://site.test/",
        evidence=[{"kind": "title", "value": "", "source": "https://site.test/"}],
        recommendation="Add one.",
    )
    audit = AuditResult(
        pages=[PageRecord(url="https://site.test/", final_url="https://site.test/", status=200)],
        findings=[finding],
    )
    assert health_score(audit.findings, page_count=1)["score"] == 90
    assert render_json(audit) == render_json(audit)
    assert "Health score: 90/100" in render_markdown(audit)
    assert "<!doctype html>" in render_html(audit)


def test_tool_surface_and_non_hallucination_without_serp_credentials(tmp_path: Path) -> None:
    tool = SeoTool(workspace=tmp_path, config=SeoToolConfig())
    schema = tool.to_schema()
    actions = schema["function"]["parameters"]["properties"]["action"]["enum"]
    assert actions == [
        "crawl", "audit", "schema", "psi", "crux", "serp_snapshot",
        "serp_history", "score", "report", "pipeline",
    ]
    with patch.dict("os.environ", {"SEMRUSH_API_KEY": ""}):
        result = asyncio.run(tool.execute(
            action="serp_snapshot", keyword="fixture", provider="semrush"
        ))
    payload = json.loads(result)
    assert payload["status"] == "data_gap"
    assert payload["results"] is None
    assert "position" not in payload
    assert "volume" not in payload


def test_tool_is_scoped_to_seo_product_module() -> None:
    assert "seo" not in AgentLoop._denied_tools(None, {"product_module": "seo"})
    assert "seo" in AgentLoop._denied_tools(None, {"product_module": "code"})
    assert "seo" in AgentLoop._denied_tools(None, {})
