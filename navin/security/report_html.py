# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Self-contained HTML security report from structured findings."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navin.report.html_common import EXPERT_CSS, deliverables_table, esc
from navin.security.findings import severity_counts
from navin.security.poc import enrich_findings_with_poc

_SEV_COLORS = {
    "critical": "#b91c1c",
    "high": "#c2410c",
    "medium": "#a16207",
    "low": "#1d4ed8",
    "info": "#475569",
}

_EFFORT = {
    "critical": "M",
    "high": "M",
    "medium": "S",
    "low": "S",
    "info": "S",
}


def _chip(severity: str) -> str:
    sev = (severity or "medium").lower()
    color = _SEV_COLORS.get(sev, _SEV_COLORS["medium"])
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'background:{color};color:#fff;font-size:12px;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:.03em">{esc(sev)}</span>'
    )


def render_security_report_html(
    *,
    findings: list[dict[str, Any]],
    root: str | Path,
    kind: str = "full",
    tools: list[dict[str, Any]] | None = None,
    title: str = "Security review",
    report_name: str | None = None,
) -> str:
    """Build a self-contained HTML security report (inline CSS, no JS)."""
    enriched = enrich_findings_with_poc(list(findings))
    counts = severity_counts(enriched)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    root_s = str(root)
    report_file = report_name or "security-report-*.html"

    summary_bits = []
    for sev in ("critical", "high", "medium", "low", "info"):
        n = counts.get(sev, 0)
        if n:
            summary_bits.append(f"<li><strong>{n}</strong> {esc(sev)}</li>")
    if not summary_bits:
        summary_bits.append("<li>No structured findings in this scan pass.</li>")

    tool_rows = ""
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        avail = "yes" if tool.get("available") else "no"
        tool_rows += (
            "<tr>"
            f"<td>{esc(tool.get('tool'))}</td>"
            f"<td>{esc(avail)}</td>"
            f"<td>{esc(tool.get('count', tool.get('error', '')))}</td>"
            "</tr>"
        )
    if not tool_rows:
        tool_rows = "<tr><td colspan='3'>No tool metadata</td></tr>"

    cards = []
    for idx, item in enumerate(enriched, start=1):
        loc = str(item.get("file_path") or "")
        line = item.get("line")
        loc_s = f"{loc}:{line}" if loc and isinstance(line, int) else (loc or "n/a")
        cards.append(
            f"""
<article class="card">
  <header>
    {_chip(str(item.get('severity')))}
    <h3>#{idx} {esc(item.get('summary') or item.get('id'))}</h3>
  </header>
  <p class="muted"><strong>Location:</strong> <code>{esc(loc_s)}</code>
    · <strong>Category:</strong> {esc(item.get('category'))}
    · <strong>Source:</strong> {esc(item.get('source'))}
    · <strong>Rule:</strong> <code>{esc(item.get('id'))}</code></p>
  <p><strong>Impact:</strong> {esc(item.get('explanation'))}</p>
  <div class="proof">
    <p><strong>Real example</strong></p>
    <p><strong>PoC / payload:</strong> <code>{esc(item.get('malicious_input_example'))}</code></p>
    <p>{esc(item.get('poc_sketch'))}</p>
  </div>
  <p><strong>Fix:</strong> {esc(item.get('recommendation'))}</p>
</article>
"""
        )

    remediations = []
    plan_items = [
        f
        for f in enriched
        if str(f.get("severity") or "").lower() in {"critical", "high", "medium"}
    ][:12]
    if not plan_items:
        plan_items = enriched[:5]
    for i, item in enumerate(plan_items, start=1):
        sev = str(item.get("severity") or "medium").lower()
        remediations.append(
            f"""
<div class="choice">
  <h3>#{i} - {esc(item.get('summary') or item.get('id'))}</h3>
  <p>{_chip(sev)} · effort {esc(_EFFORT.get(sev, 'M'))}
    · {esc(item.get('file_path') or 'n/a')}</p>
  <p><strong>Risk if delayed:</strong> {esc(item.get('explanation') or '')[:220]}</p>
  <p><strong>First step:</strong> {esc(item.get('recommendation') or 'Review and patch.')}</p>
</div>
"""
        )

    findings_json = html.escape(
        json.dumps(enriched, ensure_ascii=False, indent=2)[:120_000],
        quote=True,
    )
    deliv = deliverables_table(
        [
            (report_file, "HTML", "Security report with PoC cards and hardening plan"),
        ]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{esc(title)}</title>
<style>{EXPERT_CSS}
table {{ width:100%; border-collapse:collapse; font-size:.9rem; }}
th,td {{ text-align:left; padding:.45rem .5rem; border-bottom:1px solid #1f2937; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="muted">Navin Security · scan kind={esc(kind)}</p>
    <h1>{esc(title)}</h1>
    <p class="muted">Scope: <code>{esc(root_s)}</code> · {esc(ts)}</p>
  </header>

  <h2>Executive summary</h2>
  <div class="summary">
    <div class="panel">
      <p><strong>{len(enriched)}</strong> finding(s) after dedupe</p>
      <ul>{''.join(summary_bits)}</ul>
    </div>
    <div class="panel">
      <p><strong>Scanners</strong></p>
      <table>
        <thead><tr><th>Tool</th><th>Available</th><th>Detail</th></tr></thead>
        <tbody>{tool_rows}</tbody>
      </table>
    </div>
  </div>

  <h2>Findings</h2>
  {''.join(cards) if cards else '<p class="muted">No findings.</p>'}

  <h2>Remediation plan - choose where to start</h2>
  <p class="muted">Reply in chat with <strong>Start with #N</strong> to begin a fix.</p>
  {''.join(remediations) if remediations else '<p class="muted">Nothing to remediate.</p>'}

  <h2>Deliverables</h2>
  <div class="panel">{deliv}</div>

  <h2>Machine-readable findings</h2>
  <div class="panel"><pre style="white-space:pre-wrap;margin:0;font-size:12px">{findings_json}</pre></div>

  <footer>Generated by Navin security_scan. PoC sketches are lab-oriented templates - verify before production use. Do not paste live secrets into tickets.</footer>
</div>
</body>
</html>
"""


def write_security_report(
    root: Path,
    *,
    findings: list[dict[str, Any]],
    kind: str = "full",
    tools: list[dict[str, Any]] | None = None,
    title: str = "Security review",
) -> Path:
    """Write ``security-report-<timestamp>.html`` under ``root`` and return the path."""
    root = root.expanduser().resolve(strict=False)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"security-report-{stamp}.html"
    path = root / name
    html_doc = render_security_report_html(
        findings=findings,
        root=root,
        kind=kind,
        tools=tools,
        title=title,
        report_name=name,
    )
    path.write_text(html_doc, encoding="utf-8")
    return path
