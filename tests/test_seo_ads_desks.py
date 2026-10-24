# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""SEO and Ads desk APIs on the real engines (network mocked)."""

from __future__ import annotations

import base64
import json

import pytest

from navin.config.loader import set_config_path
from navin.seo.crawler import SeoCrawler

GOOGLE_EXPORT = (
    "Campaign,Ad group,Clicks,Impr.,Cost,Conversions,Conv. value\n"
    "Brand,Brand core,120,2400,180.50,12,1800\n"
    "Generic,Shoes,40,9000,620.00,0,0\n"
    "Generic,Boots,15,3000,210.00,1,90\n"
)


@pytest.fixture()
def desk_env(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    set_config_path(config)
    monkeypatch.setattr("navin.config.paths.get_data_dir", lambda: tmp_path / "data")
    for name in ("PAGESPEED_API_KEY", "CRUX_API_KEY", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "SEMRUSH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path
    set_config_path(None)


def _fake_crawler(*_args, **_kwargs):
    pages = {
        "https://shop.test/": (200, '<html><head><title>Shop home for tests</title></head>'
                                    '<body><h1>Shop</h1></body></html>'),
        "https://shop.test/robots.txt": (200, "Sitemap: https://shop.test/sitemap.xml"),
        "https://shop.test/sitemap.xml": (200, "<urlset><url><loc>https://shop.test/</loc></url></urlset>"),
    }

    def fetch(_client, url):
        status, body = pages.get(url, (404, "missing"))
        return {"url": url, "finalUrl": url, "status": status, "text": body, "html": body}

    return SeoCrawler(fetch=fetch, respect_robots=False)


def test_seo_desk_audit_history_report_and_keys(desk_env, monkeypatch):
    from navin.webui import seo_api

    monkeypatch.setattr(seo_api, "SeoCrawler", _fake_crawler)
    first = seo_api.handle_seo_action("pipeline", {"url": "shop.test", "max_pages": 3})
    assert first["site"] == "shop.test" and first["report"]["health"]["score"] <= 100
    report = first["report"]
    assert all("html" not in page for page in report["pages"])
    assert {gap["service"] for gap in report["data_gaps"]} >= {"psi_v5", "crux_v1"}

    snap = seo_api.handle_seo_action("snapshot", {})
    assert snap["sites"][0]["site"] == "shop.test" and snap["keys"]["psi_ready"] is False
    seo_api.handle_seo_action("pipeline", {"url": "https://shop.test/"})
    assert len(seo_api.handle_seo_action("history", {"site": "shop.test"})["audits"]) == 2
    html = seo_api.handle_seo_action("report", {"site": "shop.test", "format": "html"})
    assert html["filename"].endswith(".html") and "<html" in html["content"].lower()

    saved = seo_api.handle_seo_action("keys.save", {"psi_api_key": "AIzaTEST1234"})
    assert saved["keys"]["psi_api_key"] == {"configured": True, "source": "settings", "hint": "****1234"}
    assert "AIzaTEST1234" not in json.dumps(seo_api.handle_seo_action("keys.get", {}))
    cleared = seo_api.handle_seo_action("keys.save", {"clear": ["psi_api_key"]})
    assert cleared["keys"]["psi_ready"] is False

    gap = seo_api.handle_seo_action("serp_snapshot", {"keyword": "running shoes"})
    assert gap["status"] == "data_gap"
    with pytest.raises(seo_api.SeoDeskError):
        seo_api.handle_seo_action("pipeline", {"url": ""})
    assert seo_api.handle_seo_action("site.delete", {"site": "shop.test"}) == {"deleted": "shop.test"}


def test_seo_desk_reports_an_unreachable_site(desk_env, monkeypatch):
    from navin.webui import seo_api

    def dead(*_a, **_k):
        return SeoCrawler(fetch=lambda _c, url: {"url": url, "status": 0, "error": "dns failure"},
                          respect_robots=False)

    monkeypatch.setattr(seo_api, "SeoCrawler", dead)
    with pytest.raises(seo_api.SeoDeskError, match="could not be reached"):
        seo_api.handle_seo_action("pipeline", {"url": "https://nowhere.test"})


def test_ads_desk_import_analyze_approve_apply_export(desk_env):
    from navin.webui import ads_api

    data = base64.b64encode(GOOGLE_EXPORT.encode()).decode()
    imported = ads_api.handle_ads_action("ingest", {"filename": "google-ads.csv", "data_b64": data})
    assert imported["export"]["rows"] == 3

    result = ads_api.handle_ads_action("analyze", {"currency": "eur", "monthly_budget": 1500})
    analysis = result["analysis"]
    assert analysis["currency"] == "EUR" and analysis["totals"]["cost"] == pytest.approx(1010.5)
    assert analysis["health"]["score"] <= 100
    changes = ads_api.handle_ads_action("changes.list", {})["changes"]
    assert changes, "the wasted Generic/Shoes spend should produce a proposal"
    target = changes[0]["id"]

    with pytest.raises(ads_api.AdsDeskError, match="approved"):
        ads_api.handle_ads_action("changes.mark_applied", {"ids": [target], "proof": "paused in UI"})
    with pytest.raises(ads_api.AdsDeskError, match="No approved"):
        ads_api.handle_ads_action("export_changes", {"format": "google_editor"})
    ads_api.handle_ads_action("changes.approve", {"ids": [target]})
    exported = ads_api.handle_ads_action("export_changes", {"format": "csv"})
    assert exported["rows"] >= 1 and exported["filename"].endswith(".csv")
    with pytest.raises(ads_api.AdsDeskError, match="read-back"):
        ads_api.handle_ads_action("changes.mark_applied", {"ids": [target]})
    applied = ads_api.handle_ads_action(
        "changes.mark_applied", {"ids": [target], "proof": "Campaign status shows Paused at 10:42"}
    )
    row = next(item for item in applied["changes"] if item["id"] == target)
    assert row["status"] == "applied" and row["applied_proof"].startswith("Campaign status")

    report = ads_api.handle_ads_action("report", {"format": "md"})
    assert report["filename"] == "ads-report.md" and report["content"]
    snap = ads_api.handle_ads_action("snapshot", {})
    assert snap["analysis"]["currency"] == "EUR" and snap["settings"]["monthly_budget"] == 1500

    with pytest.raises(ads_api.AdsDeskError, match="CSV"):
        ads_api.handle_ads_action("ingest", {"filename": "x.pdf", "data_b64": data})
    ads_api.handle_ads_action("exports.delete", {"id": imported["export"]["id"]})
    with pytest.raises(ads_api.AdsDeskError, match="Import at least one"):
        ads_api.handle_ads_action("analyze", {})
