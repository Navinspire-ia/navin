# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic JSON, Markdown and HTML paid-media reports."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from navin.ads.models import PLATFORM_LABELS, AdsAnalysis
from navin.ads.scoring import health_score

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_TOP_ENTITIES = 15


def report_payload(analysis: AdsAnalysis) -> dict[str, Any]:
    findings = sorted(
        analysis.findings,
        key=lambda item: (_SEVERITY_ORDER[item.severity.value], -item.impact.get("cost", 0.0), item.code, item.entity.get("campaign", "")),
    )
    payload = analysis.model_copy(update={"findings": findings}).model_dump(mode="json", exclude_none=True)
    payload["health"] = health_score(analysis)
    payload["summary"] = summary_lines(analysis)
    return payload


def summary_lines(analysis: AdsAnalysis) -> list[str]:
    totals = analysis.totals
    currency = analysis.currency

    def money(value: float) -> str:
        return f"{value:,.2f} {currency}".strip()

    period = analysis.period
    lines = [
        f"Platforms: {', '.join(PLATFORM_LABELS.get(p, p) for p in analysis.platforms) or 'n/a'}",
        (
            f"Period: {period.get('start')} to {period.get('end')} ({period.get('days')} days)"
            if period.get("dated") else "Period: undated export"
        ),
        f"Spend: {money(totals.cost)} | Clicks: {totals.clicks:,.0f} | Impressions: {totals.impressions:,.0f}",
        f"Conversions: {totals.conversions:,.2f} | Value: {money(totals.conversion_value)}",
        "CTR: " + _pct(totals.ctr) + " | CPC: " + _num(totals.cpc) + " | CPA: " + _num(totals.cpa)
        + " | ROAS: " + _num(totals.roas),
    ]
    return lines


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def render_json(analysis: AdsAnalysis) -> str:
    return json.dumps(report_payload(analysis), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_markdown(analysis: AdsAnalysis) -> str:
    payload = report_payload(analysis)
    health = payload["health"]
    lines = [
        "# Paid media audit report",
        "",
        f"Health score: {health['score']}/100 ({health['grade']})",
        f"Wasted spend: {health['wasted_cost']:,.2f} ({health['waste_share']:.1%} of spend)",
        f"Estimated monthly savings if approved: {health['estimated_monthly_savings']:,.2f}",
        f"Sources: {', '.join(payload.get('sources') or []) or 'rows'}",
        "",
        "## Account",
        "",
        *[f"- {line}" for line in payload["summary"]],
    ]
    campaigns = payload["entities"].get("campaign") or []
    if campaigns:
        lines.extend(["", "## Campaigns (top spend)", "", "| Campaign | Spend | Share | Clicks | CTR | CPC | Conv. | CPA | ROAS |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
        for entity in campaigns[:_TOP_ENTITIES]:
            m = entity["metrics"]
            lines.append(
                f"| {entity['key']} | {m['cost']:,.2f} | {_pct(m.get('spend_share'))} | {m['clicks']:,.0f} | "
                f"{_pct(m.get('ctr'))} | {_num(m.get('cpc'))} | {m['conversions']:,.2f} | {_num(m.get('cpa'))} | {_num(m.get('roas'))} |"
            )
    lines.extend(["", "## Findings"])
    if not payload["findings"]:
        lines.extend(["", "No findings."])
    for finding in payload["findings"]:
        lines.extend([
            "",
            f"### [{finding['severity'].upper()}] {finding['title']}",
            f"- Code: `{finding['code']}`",
            f"- Category: {finding['category']}",
            f"- Entity: {json.dumps(finding.get('entity') or {}, ensure_ascii=False, sort_keys=True)}",
            f"- {finding['message']}",
            f"- Evidence: `{json.dumps(finding['evidence'], ensure_ascii=False, sort_keys=True)[:1200]}`",
            f"- Recommendation: {finding['recommendation']}",
        ])
        if finding.get("change_ids"):
            lines.append(f"- Changes: {', '.join(finding['change_ids'])}")
    lines.extend(["", "## Proposed changes (approval required)", ""])
    if not payload["changes"]:
        lines.append("No change proposed.")
    else:
        lines.extend(["| Id | Status | Action | Level | Target | Params | Est. monthly savings |", "|---|---|---|---|---|---|---:|"])
        for change in payload["changes"]:
            lines.append(
                f"| {change['id']} | {change['status']} | {change['action']} | {change['level']} | "
                f"{_target_text(change.get('target') or {})} | {json.dumps(change.get('params') or {}, ensure_ascii=False, sort_keys=True)} | "
                f"{_savings_text(change)} |"
            )
    if payload.get("data_gaps"):
        lines.extend(["", "## Data gaps", ""])
        lines.extend(f"- {gap.get('service', 'data')}: {gap.get('reason', 'unavailable')}" for gap in payload["data_gaps"])
    return "\n".join(lines) + "\n"


def _target_text(target: dict[str, str]) -> str:
    return " / ".join(f"{key}={value}" for key, value in target.items())


def _savings_text(change: dict[str, Any]) -> str:
    savings = change.get("estimated_monthly_savings")
    return "" if savings is None else f"{float(savings):,.2f}"


def render_html(analysis: AdsAnalysis) -> str:
    payload = report_payload(analysis)
    health = payload["health"]
    esc = html.escape
    summary = "".join(f"<li>{esc(line)}</li>" for line in payload["summary"])
    campaign_rows = "".join(
        "<tr>"
        f"<td>{esc(entity['key'])}</td><td>{entity['metrics']['cost']:,.2f}</td>"
        f"<td>{esc(_pct(entity['metrics'].get('spend_share')))}</td><td>{entity['metrics']['clicks']:,.0f}</td>"
        f"<td>{esc(_pct(entity['metrics'].get('ctr')))}</td><td>{esc(_num(entity['metrics'].get('cpc')))}</td>"
        f"<td>{entity['metrics']['conversions']:,.2f}</td><td>{esc(_num(entity['metrics'].get('cpa')))}</td>"
        f"<td>{esc(_num(entity['metrics'].get('roas')))}</td>"
        "</tr>"
        for entity in (payload["entities"].get("campaign") or [])[:_TOP_ENTITIES]
    )
    finding_rows = "".join(
        "<tr>"
        f"<td class=\"sev-{esc(item['severity'])}\">{esc(item['severity'])}</td>"
        f"<td>{esc(item['category'])}</td>"
        f"<td><strong>{esc(item['title'])}</strong><br>{esc(item['message'])}</td>"
        f"<td><code>{esc(json.dumps(item['evidence'], ensure_ascii=False, sort_keys=True)[:600])}</code></td>"
        f"<td>{esc(item['recommendation'])}</td>"
        "</tr>"
        for item in payload["findings"]
    )
    change_rows = "".join(
        "<tr>"
        f"<td><code>{esc(change['id'])}</code></td><td>{esc(change['status'])}</td>"
        f"<td>{esc(change['action'])}</td><td>{esc(change['level'])}</td>"
        f"<td>{esc(_target_text(change.get('target') or {}))}</td>"
        f"<td><code>{esc(json.dumps(change.get('params') or {}, ensure_ascii=False, sort_keys=True))}</code></td>"
        f"<td>{esc(_savings_text(change))}</td>"
        "</tr>"
        for change in payload["changes"]
    )
    gaps = "".join(
        f"<li>{esc(str(gap.get('service', 'data')))}: {esc(str(gap.get('reason', 'unavailable')))}</li>"
        for gap in payload.get("data_gaps") or []
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Paid media audit report</title>"
        "<style>body{font-family:system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #ddd;padding:.5rem;vertical-align:top}"
        "th{text-align:left;background:#f5f5f5}code{font-size:.8em;word-break:break-all}"
        ".sev-high,.sev-critical{color:#b00020;font-weight:600}.sev-medium{color:#b26a00}.sev-low{color:#555}"
        ".kpi{display:flex;gap:1rem;flex-wrap:wrap}.kpi div{border:1px solid #ddd;border-radius:8px;padding:.75rem 1rem}"
        "</style></head><body>"
        "<h1>Paid media audit report</h1>"
        "<div class=\"kpi\">"
        f"<div><strong>Health</strong><br>{health['score']}/100 ({esc(health['grade'])})</div>"
        f"<div><strong>Wasted spend</strong><br>{health['wasted_cost']:,.2f} ({health['waste_share']:.1%})</div>"
        f"<div><strong>Est. monthly savings</strong><br>{health['estimated_monthly_savings']:,.2f}</div>"
        f"<div><strong>Findings</strong><br>{health['finding_count']}</div>"
        f"<div><strong>Changes to approve</strong><br>{health['change_count']}</div>"
        "</div>"
        f"<h2>Account</h2><ul>{summary}</ul>"
        f"<p>Sources: {esc(', '.join(payload.get('sources') or []) or 'rows')}</p>"
        "<h2>Campaigns (top spend)</h2>"
        "<table><thead><tr><th>Campaign</th><th>Spend</th><th>Share</th><th>Clicks</th><th>CTR</th>"
        f"<th>CPC</th><th>Conv.</th><th>CPA</th><th>ROAS</th></tr></thead><tbody>{campaign_rows}</tbody></table>"
        "<h2>Findings</h2>"
        "<table><thead><tr><th>Severity</th><th>Category</th><th>Finding</th><th>Evidence</th>"
        f"<th>Recommendation</th></tr></thead><tbody>{finding_rows}</tbody></table>"
        "<h2>Proposed changes (approval required)</h2>"
        "<table><thead><tr><th>Id</th><th>Status</th><th>Action</th><th>Level</th><th>Target</th>"
        f"<th>Params</th><th>Est. monthly savings</th></tr></thead><tbody>{change_rows}</tbody></table>"
        + (f"<h2>Data gaps</h2><ul>{gaps}</ul>" if gaps else "")
        + "</body></html>\n"
    )


def write_report(analysis: AdsAnalysis, path: str | Path, format: str) -> Path:  # noqa: A002
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    renderers = {"json": render_json, "md": render_markdown, "markdown": render_markdown, "html": render_html}
    if format not in renderers:
        raise ValueError("format must be json, md, or html")
    output.write_text(renderers[format](analysis), encoding="utf-8", newline="\n")
    return output
