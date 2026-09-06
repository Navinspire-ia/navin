"""Deterministic JSON, Markdown and HTML SEO reports."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from navin.seo.models import AuditResult
from navin.seo.scoring import health_score

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def report_payload(audit: AuditResult) -> dict[str, Any]:
    findings = sorted(audit.findings, key=lambda item: (
        _SEVERITY_ORDER[item.severity.value], item.category, item.url or "", item.code
    ))
    payload = audit.model_copy(update={"findings": findings}).model_dump(
        mode="json", exclude_none=True
    )
    payload["health"] = health_score(findings, page_count=len(audit.pages))
    return payload


def render_json(audit: AuditResult) -> str:
    return json.dumps(report_payload(audit), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_markdown(audit: AuditResult) -> str:
    payload = report_payload(audit)
    health = payload["health"]
    lines = [
        "# SEO audit report",
        "",
        f"Health score: {health['score']}/100 ({health['grade']})",
        f"Pages crawled: {len(payload['pages'])}",
        f"Findings: {len(payload['findings'])}",
        "",
        "## Findings",
    ]
    if not payload["findings"]:
        lines.extend(["", "No findings."])
    for finding in payload["findings"]:
        lines.extend([
            "",
            f"### [{finding['severity'].upper()}] {finding['title']}",
            f"- Code: `{finding['code']}`",
            f"- Category: {finding['category']}",
            f"- URL: {finding.get('url', 'n/a')}",
            f"- Evidence: `{json.dumps(finding['evidence'], ensure_ascii=False, sort_keys=True)}`",
            f"- Recommendation: {finding['recommendation']}",
        ])
    if payload["data_gaps"]:
        lines.extend(["", "## Data gaps", ""])
        lines.extend(
            f"- {gap.get('service', 'data')}: {gap.get('reason', 'unavailable')}"
            for gap in payload["data_gaps"]
        )
    return "\n".join(lines) + "\n"


def render_html(audit: AuditResult) -> str:
    payload = report_payload(audit)
    health = payload["health"]
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(item['severity'])}</td>"
        f"<td>{html.escape(item['category'])}</td>"
        f"<td>{html.escape(item['title'])}</td>"
        f"<td>{html.escape(item.get('url') or '')}</td>"
        f"<td><code>{html.escape(json.dumps(item['evidence'], ensure_ascii=False, sort_keys=True))}</code></td>"
        f"<td>{html.escape(item['recommendation'])}</td>"
        "</tr>"
        for item in payload["findings"]
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>SEO audit report</title>"
        "<style>body{font-family:system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:.5rem}"
        "th{text-align:left;background:#f5f5f5}</style></head><body>"
        f"<h1>SEO audit report</h1><p>Health score: {health['score']}/100 "
        f"({health['grade']})</p><p>Pages crawled: {len(payload['pages'])}</p>"
        "<table><thead><tr><th>Severity</th><th>Category</th><th>Finding</th>"
        f"<th>URL</th><th>Evidence</th><th>Recommendation</th></tr></thead><tbody>{rows}</tbody></table>"
        "</body></html>\n"
    )


def write_report(audit: AuditResult, path: str | Path, format: str) -> Path:  # noqa: A002
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    renderers = {"json": render_json, "md": render_markdown, "markdown": render_markdown, "html": render_html}
    if format not in renderers:
        raise ValueError("format must be json, md, or html")
    output.write_text(renderers[format](audit), encoding="utf-8", newline="\n")
    return output
