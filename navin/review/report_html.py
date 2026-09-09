# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Self-contained HTML code-review report."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navin.report.html_common import EXPERT_CSS, deliverables_table, esc
from navin.review.schema import (
    dedupe_review_findings,
    filter_by_confidence,
    normalize_review_finding,
    severity_counts,
)

_SEV_COLORS = {
    "critical": "#b91c1c",
    "high": "#c2410c",
    "medium": "#a16207",
    "low": "#1d4ed8",
    "info": "#475569",
    "nit": "#64748b",
}


def _chip(severity: str) -> str:
    sev = (severity or "medium").lower()
    color = _SEV_COLORS.get(sev, _SEV_COLORS["medium"])
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'background:{color};color:#fff;font-size:12px;font-weight:600;'
        f'text-transform:uppercase">{esc(sev)}</span>'
    )


def render_review_report_html(
    *,
    findings: list[dict[str, Any]],
    root: str | Path,
    files: list[str] | None = None,
    verdict: str = "request_changes",
    title: str = "Code review",
    effort: int | None = None,
    summary_bullets: list[str] | None = None,
    report_name: str | None = None,
) -> str:
    cleaned = filter_by_confidence(
        dedupe_review_findings([normalize_review_finding(f) for f in findings]),
        min_confidence=0.55,
    )
    counts = severity_counts(cleaned)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    verdict_label = {
        "approve": "Approve",
        "request_changes": "Request changes",
        "comment": "Comment only",
    }.get(verdict, verdict)
    report_file = report_name or "review-report-*.html"

    bullets = summary_bullets or []
    if not bullets:
        bullets = [
            f"{counts.get('critical', 0)} critical / {counts.get('high', 0)} high / "
            f"{counts.get('medium', 0)} medium / {counts.get('low', 0)} low findings",
            f"Scope files: {len(files or [])}",
        ]

    cards = []
    for idx, item in enumerate(cleaned, start=1):
        loc = item.get("file_path") or "n/a"
        start = item.get("start_line")
        end = item.get("end_line")
        if start and end and end != start:
            loc_s = f"{loc}:{start}-{end}"
        elif start:
            loc_s = f"{loc}:{start}"
        else:
            loc_s = str(loc)
        existing = item.get("existing_code") or ""
        suggested = item.get("suggested_code") or ""
        proof = item.get("explanation") or ""
        proof_block = f"""
<div class="proof">
  <p><strong>Real example</strong></p>
  <pre>{esc(existing) if existing else esc(proof[:800] or '(add code excerpt or failing evidence)')}</pre>
  {"<p><strong>Suggested</strong></p><pre>" + esc(suggested) + "</pre>" if suggested else ""}
</div>"""
        cards.append(
            f"""
<article class="card">
  <header>{_chip(str(item.get('severity')))} <h3>#{idx} {esc(item.get('summary'))}</h3></header>
  <p class="muted"><code>{esc(loc_s)}</code> · {esc(item.get('category'))}
    · confidence {esc(item.get('confidence'))}</p>
  <p><strong>Impact:</strong> {esc(item.get('explanation'))}</p>
  {proof_block}
  <p><strong>Fix:</strong> {esc(item.get('recommendation'))}</p>
</article>"""
        )

    remediations = []
    for i, item in enumerate(
        [f for f in cleaned if f["severity"] in {"critical", "high", "medium"}][:12]
        or cleaned[:5],
        start=1,
    ):
        remediations.append(
            f"""
<div class="choice">
  <h3>#{i} - {esc(item.get('summary'))}</h3>
  <p>{_chip(str(item.get('severity')))} · effort M · {esc(item.get('file_path'))}</p>
  <p><strong>Risk if delayed:</strong> {esc((item.get('explanation') or '')[:220])}</p>
  <p><strong>First step:</strong> {esc(item.get('recommendation') or 'Apply suggested patch.')}</p>
</div>"""
        )

    file_list = "".join(f"<li><code>{esc(p)}</code></li>" for p in (files or [])[:80])
    findings_json = html.escape(json.dumps(cleaned, ensure_ascii=False, indent=2)[:100_000])
    count_line = (
        f"Critical {counts.get('critical', 0)} · High {counts.get('high', 0)} · "
        f"Medium {counts.get('medium', 0)} · Low {counts.get('low', 0)} · "
        f"Info {counts.get('info', 0)}"
    )
    deliv = deliverables_table(
        [
            (report_file, "HTML", "Review report with findings and remediation plan"),
            (
                report_file.replace(".html", ".json") if report_file.endswith(".html") else "findings.json",
                "JSON",
                "Machine-readable findings (embedded below if no sidecar)",
            ),
        ]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{esc(title)}</title>
<style>{EXPERT_CSS}</style>
</head>
<body>
<div class="wrap">
  <p class="muted">Navin Review</p>
  <h1>{esc(title)}</h1>
  <p class="muted">Scope: <code>{esc(root)}</code> · {esc(ts)}
    {" · effort " + esc(effort) + "/5" if effort else ""}</p>
  <p><span class="verdict">{esc(verdict_label)}</span></p>

  <h2>Executive summary</h2>
  <div class="panel">
    <p><strong>{esc(count_line)}</strong></p>
    <ul>{''.join(f'<li>{esc(b)}</li>' for b in bullets)}</ul>
  </div>

  <h2>Files in scope</h2>
  <div class="panel"><ul>{file_list or '<li class="muted">No file list</li>'}</ul></div>

  <h2>Findings</h2>
  {''.join(cards) or '<p class="muted">No findings above confidence threshold.</p>'}

  <h2>Remediation plan - choose where to start</h2>
  <p class="muted">Reply in chat with <strong>Start with #N</strong> (e.g. Start with #1).</p>
  {''.join(remediations) or '<p class="muted">Nothing to remediate.</p>'}

  <h2>Deliverables</h2>
  <div class="panel">{deliv}</div>

  <h2>Machine-readable findings</h2>
  <div class="panel"><pre style="white-space:pre-wrap;font-size:12px;margin:0">{findings_json}</pre></div>
  <footer>Generated by Navin code_review. Precision over recall - low-confidence nits filtered.</footer>
</div>
</body>
</html>
"""


def write_review_report(
    root: Path,
    *,
    findings: list[dict[str, Any]],
    files: list[str] | None = None,
    verdict: str = "request_changes",
    title: str = "Code review",
    effort: int | None = None,
    summary_bullets: list[str] | None = None,
) -> Path:
    root = root.expanduser().resolve(strict=False)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"review-report-{stamp}.html"
    path = root / name
    path.write_text(
        render_review_report_html(
            findings=findings,
            root=root,
            files=files,
            verdict=verdict,
            title=title,
            effort=effort,
            summary_bullets=summary_bullets,
            report_name=name,
        ),
        encoding="utf-8",
    )
    return path
