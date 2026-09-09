"""Paid media engine: ingestion, metrics, rules, scoring, reports, change store, tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from navin.ads.changes import ChangeStore, google_editor_csv, mcp_execution_plan, microsoft_bulk_csv
from navin.ads.ingest import (
    canonical_column,
    detect_platform,
    ingest_file,
    ingest_text,
    parse_number,
    parse_records,
)
from navin.ads.metrics import aggregate, period_of, primary_rows, sum_metrics
from navin.ads.models import AdRow, AdsAnalysis, Level, Platform, ProposedChange
from navin.ads.report import render_html, render_json, render_markdown
from navin.ads.rules import AdsThresholds, analyze_rows
from navin.ads.scoring import health_score
from navin.agent.loop import AgentLoop
from navin.agent.tools.ads import AdsTool, AdsToolConfig
from navin.config.schema import Config

# --- fixtures -----------------------------------------------------------------


def _google_campaign_export() -> bytes:
    """Google Ads download: UTF-16 TSV, report preamble, Total trailer, US numbers."""
    lines = [
        "Campaign report (1 Sep 2026 - 7 Sep 2026)",
        "",
        "Day\tCampaign\tAd group\tCampaign status\tImpr.\tClicks\tCost\tConversions\tConv. value\tSearch impr. share",
    ]
    for day in range(1, 8):
        lines += [
            f"2026-09-0{day}\tBrand - FR\tBrand exact\tEnabled\t3,200\t410\t120.50\t22.0\t2,400.00\t85.00%",
            f"2026-09-0{day}\tGeneric - Software\tCRM\tEnabled\t18,000\t260\t690.00\t3.0\t300.00\t22.00%",
            f"2026-09-0{day}\tDisplay prospecting\tAll\tEnabled\t120,000\t90\t95.00\t0.0\t0.00\t--",
            f"2026-09-0{day}\tRetargeting\tCart\tEnabled\t9,000\t150\t60.00\t9.0\t900.00\t--",
        ]
    lines.append("Total: Account\t\t\t\t1,051,400\t6,370\t6,758.50\t238\t25,200\t")
    return "\n".join(lines).encode("utf-16")


GOOGLE_SEARCH_TERMS = (
    "Search term\tMatch type\tCampaign\tAd group\tImpr.\tClicks\tCost\tConversions\n"
    "crm gratuit\tbroad\tGeneric - Software\tCRM\t4,000\t120\t310.00\t0.0\n"
    "crm logiciel\tphrase\tGeneric - Software\tCRM\t3,000\t80\t190.00\t2.0\n"
    "emploi crm\tbroad\tGeneric - Software\tCRM\t2,500\t45\t95.00\t0.0\n"
)

META_FR_EXPORT = (
    "Début des rapports;Nom de la campagne;Nom de l'ensemble de publicités;Impressions;"
    "Clics sur un lien;Montant dépensé (EUR);Résultats;Valeur de conversion des achats;Répétition\n"
    "2026-09-01;Prospection FR;Lookalike 1%;45 000;1 200;850,50;12;1 500,00;2,3\n"
    "2026-09-01;Prospection FR;Intérêts;80 000;900;1 200,00;0;0;5,1\n"
)

LINKEDIN_EXPORT = (
    "Start Date (in UTC),Campaign Group Name,Campaign Name,Impressions,Clicks,Total Spent,Leads,Conversions\n"
    "2026-09-01,EMEA,CTO whitepaper,12000,140,980.00,6,6\n"
    "2026-09-01,EMEA,Founders webinar,9000,60,640.00,0,0\n"
)

MICROSOFT_EXPORT = (
    "Account name,Campaign name,Ad group,Keyword,Impressions,Clicks,Spend,Conversions,Revenue,Quality score\n"
    "Acme,Search - Core,CRM,crm software,5000,300,450.00,12,3000,7\n"
    "Acme,Search - Core,CRM,free crm,4000,200,260.00,0,0,3\n"
)


@pytest.fixture
def google_files(tmp_path: Path) -> tuple[Path, Path]:
    campaigns = tmp_path / "google-campaigns.csv"
    campaigns.write_bytes(_google_campaign_export())
    terms = tmp_path / "google-search-terms.csv"
    terms.write_text(GOOGLE_SEARCH_TERMS, encoding="utf-8")
    return campaigns, terms


# --- ingestion ----------------------------------------------------------------


def test_parse_number_handles_locales_percentages_and_placeholders() -> None:
    assert parse_number("1,234.56") == 1234.56
    assert parse_number("1 234,56") == 1234.56
    assert parse_number("1.234,56") == 1234.56
    assert parse_number("1,234") == 1234.0
    assert parse_number("12.5%") == 0.125
    assert parse_number("<10%") == 0.1
    assert parse_number("--") is None
    assert parse_number("") is None
    assert parse_number("€12") == 12.0


def test_canonical_columns_cover_english_french_and_api_names() -> None:
    assert canonical_column("Impr.") == "impressions"
    assert canonical_column("Conv. value") == "conversion_value"
    assert canonical_column("Amount spent (EUR)") == "cost"
    assert canonical_column("Montant dépensé (EUR)") == "cost"
    assert canonical_column("Nom de l'ensemble de publicités") == "ad_group"
    assert canonical_column("metrics.cost_micros") == "cost"
    assert canonical_column("ad_group_criterion.keyword.text") == "keyword"
    assert canonical_column("Campaign Group Name") == "ad_group"
    assert canonical_column("Something odd") is None


def test_google_utf16_export_with_preamble_and_total_row(google_files: tuple[Path, Path]) -> None:
    result = ingest_file(google_files[0])
    assert result.platform is Platform.GOOGLE
    assert len(result.rows) == 28  # 4 campaigns x 7 days, Total row skipped
    assert result.columns["cost"] == "Cost"
    assert result.columns["impression_share"] == "Search impr. share"
    first = result.rows[0]
    assert first.campaign == "Brand - FR" and first.ad_group == "Brand exact"
    assert first.impressions == 3200 and first.cost == 120.5 and first.conversion_value == 2400
    assert first.extras["impression_share"] == 0.85
    assert first.date == "2026-09-01" and first.status == "enabled"
    assert result.data_gaps == []


def test_search_terms_are_a_distinct_level(google_files: tuple[Path, Path]) -> None:
    result = ingest_file(google_files[1], platform="google")
    assert [row.level() for row in result.rows] == [Level.SEARCH_TERM] * 3
    assert result.rows[0].search_term == "crm gratuit" and result.rows[0].match_type == "broad"


def test_meta_french_semicolon_export() -> None:
    result = ingest_text(META_FR_EXPORT, source="meta-export.csv")
    assert result.platform is Platform.META
    assert len(result.rows) == 2
    interests = result.rows[1]
    assert interests.ad_group == "Intérêts"
    assert interests.impressions == 80000 and interests.clicks == 900 and interests.cost == 1200.0
    assert interests.extras["frequency"] == 5.1
    assert result.rows[0].conversion_value == 1500.0


def test_linkedin_and_microsoft_detection() -> None:
    linkedin = ingest_text(LINKEDIN_EXPORT, source="campaign-performance.csv")
    assert linkedin.platform is Platform.LINKEDIN
    assert linkedin.rows[0].ad_group == "EMEA" and linkedin.rows[0].campaign == "CTO whitepaper"
    assert linkedin.rows[0].conversions == 6
    microsoft = ingest_text(MICROSOFT_EXPORT, source="report.csv")
    assert microsoft.platform is Platform.MICROSOFT
    assert microsoft.rows[1].keyword == "free crm" and microsoft.rows[1].extras["quality_score"] == 3
    assert microsoft.rows[0].conversion_value == 3000


def test_mcp_json_rows_with_google_api_field_paths() -> None:
    result = parse_records([
        {
            "campaign": {"name": "Brand"},
            "metrics": {"impressions": 1000, "clicks": 100, "cost_micros": 55_000_000, "conversions": 4, "conversions_value": 400},
            "segments": {"date": "2026-09-01"},
        }
    ])
    assert result.platform is Platform.GOOGLE
    row = result.rows[0]
    assert row.campaign == "Brand" and row.cost == 55.0 and row.date == "2026-09-01"


def test_detect_platform_uses_filename_hints() -> None:
    headers = ["Campaign", "Ad group", "Impressions", "Clicks", "Cost"]
    assert detect_platform(headers, filename="tiktok-ads-export.csv") is Platform.TIKTOK
    assert detect_platform(headers, filename="reddit_campaigns.csv") is Platform.REDDIT


def test_unrecognized_table_is_rejected() -> None:
    with pytest.raises(ValueError, match="No recognizable export header"):
        ingest_text("a,b,c\n1,2,3\n")


def test_xlsx_export_is_read(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Campaign", "Ad set name", "Impressions", "Link clicks", "Amount spent (USD)", "Results"])
    sheet.append(["Launch", "Broad", 10000, 250, 400.5, 5])
    path = tmp_path / "meta.xlsx"
    book.save(path)
    result = ingest_file(path)
    assert result.platform is Platform.META
    assert result.rows[0].cost == 400.5 and result.rows[0].conversions == 5


# --- metrics ------------------------------------------------------------------


def test_metrics_ratios_and_no_double_counting(google_files: tuple[Path, Path]) -> None:
    rows = ingest_file(google_files[0]).rows + ingest_file(google_files[1], platform="google").rows
    base = primary_rows(rows)
    assert all(row.level() is Level.AD_GROUP for row in base)
    totals = sum_metrics(base)
    assert totals.cost == 6758.5 and totals.clicks == 6370 and totals.conversions == 238
    assert totals.ctr == pytest.approx(6370 / 1_051_400, rel=1e-4)
    assert totals.cpa == pytest.approx(6758.5 / 238, rel=1e-4)
    assert totals.roas == pytest.approx(25200 / 6758.5, rel=1e-4)
    campaigns = aggregate(rows, Level.CAMPAIGN, total_cost=totals.cost)
    assert [item.key for item in campaigns][:2] == ["Generic - Software", "Brand - FR"]
    generic = campaigns[0]
    assert generic.metrics.cost == 4830.0 and generic.days == 7
    assert generic.extras["impression_share"] == pytest.approx(0.22)
    terms = aggregate(rows, Level.SEARCH_TERM)
    assert {item.search_term for item in terms} == {"crm gratuit", "crm logiciel", "emploi crm"}
    assert period_of(rows) == {"start": "2026-09-01", "end": "2026-09-07", "days": 7, "dated": True}


def test_zero_denominators_yield_none() -> None:
    metrics = sum_metrics([AdRow(campaign="x")])
    assert metrics.ctr is None and metrics.cpc is None and metrics.cpa is None and metrics.roas is None


# --- rules ----------------------------------------------------------------------


def _analysis(google_files: tuple[Path, Path], **kwargs) -> AdsAnalysis:
    campaigns = ingest_file(google_files[0])
    terms = ingest_file(google_files[1], platform="google")
    return analyze_rows(
        campaigns.rows + terms.rows,
        thresholds=AdsThresholds(),
        currency="EUR",
        sources=[campaigns.source, terms.source],
        columns={campaigns.source: campaigns.columns, terms.source: terms.columns},
        **kwargs,
    )


def test_rules_flag_waste_high_cpa_negatives_and_winners(google_files: tuple[Path, Path]) -> None:
    analysis = _analysis(google_files, monthly_budget=25_000)
    codes = {(item.code, item.entity.get("campaign", "")) for item in analysis.findings}
    assert ("zero_conversion_spend", "Display prospecting") in codes
    assert ("high_cpa", "Generic - Software") in codes
    assert ("scale_winner", "Brand - FR") in codes
    assert ("scale_winner", "Retargeting") in codes
    assert ("spend_concentration", "Generic - Software") in codes
    assert any(item.code == "search_term_waste" for item in analysis.findings)
    assert any(item.code == "budget_pacing" for item in analysis.findings)
    waste = next(item for item in analysis.findings if item.code == "search_term_waste")
    assert waste.impact["wasted_cost"] == 405.0
    negatives = [change for change in analysis.changes if change.action == "add_negative_keyword"]
    assert {change.params["keyword"] for change in negatives} == {"crm gratuit", "emploi crm"}
    assert all(change.target == {"campaign": "Generic - Software", "ad_group": "CRM"} for change in negatives)
    assert all(change.status == "proposed" and change.requires_approval for change in analysis.changes)
    for finding in analysis.findings:
        assert finding.evidence, finding.code
        assert all(change_id in {c.id for c in analysis.changes} for change_id in finding.change_ids)
    assert analysis.metadata["wasted_cost"] == 1070.0
    pacing = next(item for item in analysis.findings if item.code == "budget_pacing")
    assert pacing.evidence[0].value["ratio"] == pytest.approx(6758.5 / 7 * 30.4 / 25_000, rel=1e-3)


def test_campaign_flag_covers_its_ad_groups(google_files: tuple[Path, Path]) -> None:
    analysis = _analysis(google_files)
    zero = [item for item in analysis.findings if item.code == "zero_conversion_spend"]
    assert [item.level for item in zero] == [Level.CAMPAIGN]


def test_change_ids_are_deterministic() -> None:
    a = ProposedChange(platform=Platform.GOOGLE, action="pause", level=Level.AD_GROUP,
                       target={"campaign": "C", "ad_group": "G"}, rationale="r", finding_code="x")
    b = ProposedChange(platform=Platform.GOOGLE, action="pause", level=Level.AD_GROUP,
                       target={"campaign": "C", "ad_group": "G"}, rationale="other", finding_code="x")
    assert a.id == b.id and a.id.startswith("chg_")


def test_tracking_missing_mutes_efficiency_rules() -> None:
    rows = [
        AdRow(platform=Platform.META, campaign="A", ad_group="a", impressions=50000, clicks=900, cost=800),
        AdRow(platform=Platform.META, campaign="B", ad_group="b", impressions=40000, clicks=700, cost=600),
    ]
    analysis = analyze_rows(rows, columns={"x.csv": {"cost": "Amount spent", "conversions": "Results"}})
    codes = [item.code for item in analysis.findings]
    assert "tracking_missing" in codes
    assert "zero_conversion_spend" not in codes and "high_cpa" not in codes


def test_meta_fatigue_and_zero_conversion_ad_set() -> None:
    result = ingest_text(META_FR_EXPORT, source="meta.csv")
    analysis = analyze_rows(result.rows, sources=["meta.csv"], columns={"meta.csv": result.columns})
    codes = {item.code for item in analysis.findings}
    assert {"creative_fatigue", "zero_conversion_spend"} <= codes
    pause = next(change for change in analysis.changes if change.action == "pause")
    assert pause.target == {"campaign": "Prospection FR", "ad_group": "Intérêts"}
    assert "period" in {gap["service"] for gap in analysis.data_gaps} or analysis.period["dated"]


def test_low_quality_score_rule_on_microsoft_keywords() -> None:
    result = ingest_text(MICROSOFT_EXPORT, source="ms.csv")
    analysis = analyze_rows(result.rows, sources=["ms.csv"], columns={"ms.csv": result.columns})
    quality = next(item for item in analysis.findings if item.code == "low_quality_score")
    assert "free crm" in quality.entity["keywords"]
    assert any(change.action == "review_landing_page" for change in analysis.changes)


# --- scoring / reports ------------------------------------------------------------


def test_scoring_and_reports_are_deterministic(google_files: tuple[Path, Path]) -> None:
    analysis = _analysis(google_files)
    health = health_score(analysis)
    assert 0 <= health["score"] <= 100 and health["grade"] in "ABCDF"
    assert health["waste_share"] == pytest.approx(1070 / 6758.5, rel=1e-3)
    assert render_json(analysis) == render_json(analysis)
    markdown = render_markdown(analysis)
    assert f"Health score: {health['score']}/100" in markdown
    assert "## Proposed changes (approval required)" in markdown
    assert "Generic - Software" in markdown
    html = render_html(analysis)
    assert "<!doctype html>" in html and "Changes to approve" in html
    for text in (markdown, html):
        assert "\u2014" not in text and "\u2013" not in text


# --- change store -------------------------------------------------------------------


def test_change_store_keeps_decisions_and_exports(tmp_path: Path, google_files: tuple[Path, Path]) -> None:
    analysis = _analysis(google_files)
    store = ChangeStore(tmp_path / "ads" / "changes.jsonl")
    first = store.upsert_proposals(analysis.changes)
    assert first["added"] == len(analysis.changes)
    negative = next(change for change in analysis.changes if change.action == "add_negative_keyword")
    result = store.set_status([negative.id, "chg_missing"], "approved", note="ok")
    assert result["updated"] == [negative.id] and result["missing"] == ["chg_missing"]
    again = store.upsert_proposals(analysis.changes)
    assert again["kept"] == 1 and again["refreshed"] == len(analysis.changes) - 1
    assert store.list(status="approved")[0]["note"] == "ok"
    with pytest.raises(ValueError, match="approved before"):
        store.set_status([analysis.changes[0].id if analysis.changes[0].id != negative.id else analysis.changes[1].id], "applied")
    name, text = store.export(format="google_editor")
    assert name == "google-ads-editor-import.csv"
    lines = text.strip().splitlines()
    assert lines[0].startswith("Campaign,Ad group,Keyword,Criterion Type")
    assert lines[1].startswith("Generic - Software,CRM,") and "Negative Exact" in lines[1]
    plan = mcp_execution_plan(store.list(status="approved"))
    assert plan[0]["mcp_server"] == "google-ads" and "negative keyword" in plan[0]["operation"]
    assert plan[0]["verify"]


def test_bulk_sheets_only_carry_their_platform() -> None:
    rows = [
        {"id": "chg_1", "platform": "microsoft", "action": "add_negative_keyword", "level": "search_term",
         "target": {"campaign": "C", "ad_group": "G"}, "params": {"keyword": "free", "match_type": "exact"}, "status": "approved"},
        {"id": "chg_2", "platform": "google", "action": "pause", "level": "campaign",
         "target": {"campaign": "Display"}, "params": {}, "status": "approved"},
    ]
    microsoft = microsoft_bulk_csv(rows).strip().splitlines()
    assert len(microsoft) == 2 and microsoft[1].startswith("Ad Group Negative Keyword,Active,C,G,,Exact,free")
    google = google_editor_csv(rows).strip().splitlines()
    assert len(google) == 2 and google[1].startswith("Display,,,,Paused")


# --- tool -----------------------------------------------------------------------------


def test_tool_pipeline_changes_and_exports(tmp_path: Path, google_files: tuple[Path, Path]) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for source in google_files:
        (workspace / source.name).write_bytes(source.read_bytes())
    tool = AdsTool(workspace=workspace, config=AdsToolConfig())
    actions = tool.to_schema()["function"]["parameters"]["properties"]["action"]["enum"]
    assert actions == ["ingest", "analyze", "score", "report", "changes", "export_changes", "pipeline"]

    ingested = json.loads(asyncio.run(tool.execute(action="ingest", paths="google-campaigns.csv")))
    assert ingested["exports"][0]["platform"] == "google" and ingested["exports"][0]["rows"] == 28

    payload = json.loads(asyncio.run(tool.execute(
        action="pipeline", paths="google-campaigns.csv, google-search-terms.csv",
        monthly_budget=25_000, currency="eur", format="md",
    )))
    assert payload["health"]["score"] == health_score(_analysis(google_files, monthly_budget=25_000))["score"]
    assert payload["currency"] == "EUR"
    assert Path(payload["path"]).read_text(encoding="utf-8").startswith("# Paid media audit report")
    assert payload["changes_store"]["added"] == len(payload["changes"])
    assert payload["entities"]["campaign"]["count"] == 4

    negative_ids = [change["id"] for change in payload["changes"] if change["action"] == "add_negative_keyword"]
    approved = json.loads(asyncio.run(tool.execute(action="changes", ids=",".join(negative_ids), status="approved")))
    assert set(approved["updated"]) == set(negative_ids) and len(approved["mcp_plan"]) == 2

    exported = json.loads(asyncio.run(tool.execute(action="export_changes", format="google_editor")))
    assert exported["rows"] == 2
    assert Path(exported["path"]).read_text(encoding="utf-8").count("Negative Exact") == 2

    queue = json.loads(asyncio.run(tool.execute(action="changes")))
    assert queue["by_status"] == {"approved": 2, "proposed": len(payload["changes"]) - 2}

    scored = json.loads(asyncio.run(tool.execute(action="score", data=json.dumps(payload))))
    assert scored["score"] == payload["health"]["score"]

    reported = json.loads(asyncio.run(tool.execute(action="report", data=json.dumps(payload), path="ads/again.html")))
    assert Path(reported["path"]).name == "again.html"


def test_tool_accepts_inline_csv_and_mcp_rows(tmp_path: Path) -> None:
    tool = AdsTool(workspace=tmp_path, config=AdsToolConfig())
    payload = json.loads(asyncio.run(tool.execute(action="analyze", data=META_FR_EXPORT)))
    assert payload["platforms"] == ["meta"] and payload["row_count"] == 2
    rows = json.dumps({"rows": [
        {"campaign": {"name": "Brand"}, "metrics": {"impressions": 1000, "clicks": 100, "cost_micros": 55_000_000, "conversions": 4}},
    ]})
    payload = json.loads(asyncio.run(tool.execute(action="analyze", data=rows, platform="google")))
    assert payload["totals"]["cost"] == 55.0


def test_tool_rejects_paths_outside_workspace_and_bad_input(tmp_path: Path) -> None:
    tool = AdsTool(workspace=tmp_path, config=AdsToolConfig())
    outside = asyncio.run(tool.execute(action="analyze", paths="../../etc/passwd"))
    assert str(outside).startswith("Error:")
    missing = asyncio.run(tool.execute(action="analyze"))
    assert "paths or data is required" in str(missing)
    unknown = asyncio.run(tool.execute(action="nope"))
    assert "unknown Ads action" in str(unknown)


def test_tool_is_scoped_to_ads_product_module_and_configured() -> None:
    assert "ads" not in AgentLoop._denied_tools(None, {"product_module": "ads"})
    assert "ads" in AgentLoop._denied_tools(None, {"product_module": "seo"})
    assert "ads" in AgentLoop._denied_tools(None, {"product_module": "code"})
    assert "ads" in AgentLoop._denied_tools(None, {})
    config = Config()
    assert config.tools.ads.enabled is True
    assert config.tools.ads.changes_store_path == "ads/changes.jsonl"
    assert config.tools.ads.thresholds.high_cpa_ratio == 1.8
