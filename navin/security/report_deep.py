# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTML and PDF renderers for the deep security scan result.

The HTML report is self-contained (inline CSS, no script) and print-ready.
The PDF is produced by WeasyPrint from that HTML when the library and its
native dependencies are present, otherwise by ReportLab from the structured
result, so a PDF comes out on every platform that ships one of the two.
"""

from __future__ import annotations

import importlib.util
import io
import re
from datetime import datetime, timezone
from typing import Any

from navin.report.html_common import esc

_SEV_ORDER = ("critical", "high", "medium", "low", "info")
_SEV_COLORS = {
    "critical": "#b91c1c",
    "high": "#c2410c",
    "medium": "#a16207",
    "low": "#1d4ed8",
    "info": "#475569",
}
_GRADE_COLORS = {"A": "#15803d", "B": "#65a30d", "C": "#ca8a04", "D": "#ea580c", "F": "#b91c1c"}
_CHECK_COLORS = {"pass": "#15803d", "warn": "#ca8a04", "fail": "#b91c1c", "info": "#475569"}

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; font:14px/1.55 system-ui, Segoe UI, Roboto, sans-serif; color:#0f172a; background:#f8fafc; }
.wrap { max-width: 1040px; margin: 0 auto; padding: 32px 28px 64px; }
.cover { background:#0f172a; color:#f8fafc; border-radius:20px; padding:32px; display:flex; gap:32px; align-items:center; flex-wrap:wrap; }
.cover h1 { margin:0 0 6px; font-size:28px; letter-spacing:-.01em; }
.cover .sub { color:#cbd5e1; font-size:13px; }
.cover .meta { margin-top:14px; display:flex; gap:18px; flex-wrap:wrap; font-size:12px; color:#cbd5e1; }
.cover .meta b { color:#f8fafc; }
.ring { flex: 0 0 150px; text-align:center; }
.ring svg { display:block; margin: 0 auto; }
.ring .grade { font-size:12px; letter-spacing:.14em; text-transform:uppercase; color:#cbd5e1; margin-top:6px; }
h2 { font-size:18px; margin:36px 0 12px; padding-bottom:6px; border-bottom:2px solid #e2e8f0; }
h3 { font-size:15px; margin:0; }
.grid { display:grid; grid-template-columns: repeat(5, 1fr); gap:10px; }
.kpi { background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:14px 16px; }
.kpi .n { font-size:26px; font-weight:700; line-height:1; }
.kpi .l { font-size:11px; text-transform:uppercase; letter-spacing:.1em; color:#64748b; margin-top:6px; }
table { width:100%; border-collapse:collapse; background:#fff; border:1px solid #e2e8f0; border-radius:12px; overflow:hidden; font-size:13px; }
th, td { text-align:left; padding:9px 12px; border-bottom:1px solid #e2e8f0; vertical-align:top; }
th { background:#f1f5f9; font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:#475569; }
tr:last-child td { border-bottom:none; }
.bar { height:8px; background:#e2e8f0; border-radius:999px; overflow:hidden; min-width:120px; }
.bar > i { display:block; height:100%; background:#0f172a; border-radius:999px; }
.chip { display:inline-block; padding:2px 10px; border-radius:999px; color:#fff; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.05em; }
.tag { display:inline-block; padding:2px 8px; border-radius:6px; background:#f1f5f9; color:#334155; font-size:11px; margin-right:6px; }
.card { background:#fff; border:1px solid #e2e8f0; border-left-width:5px; border-radius:14px; padding:16px 18px; margin:0 0 12px; page-break-inside: avoid; }
.card header { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }
.card .loc { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; color:#334155; background:#f1f5f9; padding:2px 8px; border-radius:6px; }
.proof { background:#0f172a; color:#e2e8f0; border-radius:10px; padding:10px 14px; margin:10px 0; font-size:12.5px; }
.proof code { color:#fbbf24; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.fix { background:#ecfdf5; border:1px solid #a7f3d0; border-radius:10px; padding:10px 14px; font-size:13px; }
.muted { color:#64748b; font-size:12.5px; }
.plan li { margin: 0 0 10px; }
.check { display:flex; gap:10px; align-items:flex-start; padding:8px 0; border-bottom:1px solid #e2e8f0; }
.check:last-child { border-bottom:none; }
.dot { width:10px; height:10px; border-radius:999px; margin-top:5px; flex:none; }
footer { margin-top:40px; font-size:11.5px; color:#64748b; text-align:center; }
@media print {
  body { background:#fff; }
  .wrap { padding: 0; max-width:none; }
  .cover { border-radius:0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  h2 { page-break-after: avoid; }
  .card, .kpi, table { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
@page { size: A4; margin: 16mm 14mm; }
"""


def _chip(severity: str) -> str:
    sev = (severity or "medium").lower()
    color = _SEV_COLORS.get(sev, _SEV_COLORS["medium"])
    return f'<span class="chip" style="background:{color}">{esc(sev)}</span>'


def _score_ring(score: int, grade: str) -> str:
    radius = 54
    circ = 2 * 3.14159 * radius
    filled = circ * max(0, min(100, int(score))) / 100
    color = _GRADE_COLORS.get(grade, "#64748b")
    return (
        f'<svg width="140" height="140" viewBox="0 0 140 140" role="img" aria-label="Score {score}">'
        f'<circle cx="70" cy="70" r="{radius}" fill="none" stroke="#1e293b" stroke-width="12"/>'
        f'<circle cx="70" cy="70" r="{radius}" fill="none" stroke="{color}" stroke-width="12" '
        f'stroke-linecap="round" stroke-dasharray="{filled:.1f} {circ:.1f}" transform="rotate(-90 70 70)"/>'
        f'<text x="70" y="66" text-anchor="middle" font-size="34" font-weight="700" fill="#f8fafc">{int(score)}</text>'
        f'<text x="70" y="90" text-anchor="middle" font-size="13" fill="#cbd5e1">/ 100</text>'
        "</svg>"
    )


def _fmt_duration(ms: int) -> str:
    seconds = max(0, int(ms)) / 1000
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{int(seconds // 60)} min {int(seconds % 60):02d} s"


def render_deep_report_html(result: dict[str, Any], *, title: str = "Security assessment") -> str:
    """Self-contained HTML report (inline CSS, no JS) for one deep scan result."""
    findings = [f for f in (result.get("findings") or []) if isinstance(f, dict)]
    counts = dict(result.get("counts") or {})
    score = int(result.get("score") or 0)
    grade = str(result.get("grade") or "F")
    root = str(result.get("root") or "")
    ts_raw = str(result.get("started_at") or "")
    try:
        ts = datetime.fromisoformat(ts_raw).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    kpis = "".join(
        f'<div class="kpi"><div class="n" style="color:{_SEV_COLORS[sev]}">{int(counts.get(sev, 0))}</div>'
        f'<div class="l">{esc(sev)}</div></div>'
        for sev in _SEV_ORDER
    )

    owasp_rows = ""
    total = max(1, len(findings))
    for row in result.get("by_owasp") or []:
        n = int(row.get("count") or 0)
        pct = round(n * 100 / total)
        owasp_rows += (
            f"<tr><td><b>{esc(row.get('id'))}</b></td><td>{esc(row.get('name'))}</td>"
            f"<td>{n}</td><td><div class='bar'><i style='width:{pct}%'></i></div></td></tr>"
        )

    cat_rows = "".join(
        f"<tr><td>{esc(row.get('category'))}</td><td>{int(row.get('count') or 0)}</td></tr>"
        for row in (result.get("by_category") or [])[:16]
    ) or "<tr><td colspan='2'>No finding</td></tr>"

    file_rows = "".join(
        f"<tr><td><code>{esc(row.get('file_path'))}</code></td><td>{int(row.get('count') or 0)}</td></tr>"
        for row in (result.get("top_files") or [])[:10]
    ) or "<tr><td colspan='2'>No finding</td></tr>"

    checks_html = ""
    for check in result.get("project_checks") or []:
        status = str(check.get("status") or "info")
        detail = check.get("detail") or ""
        checks_html += (
            f'<div class="check"><span class="dot" style="background:{_CHECK_COLORS.get(status, "#475569")}"></span>'
            f"<div><b>{esc(check.get('title'))}</b> <span class='tag'>{esc(status)}</span>"
            + (f"<div class='muted'>{esc(detail)}</div>" if detail else "")
            + "</div></div>"
        )
    if not checks_html:
        checks_html = "<p class='muted'>No project check recorded.</p>"

    tool_rows = ""
    for tool in result.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        state = "available" if tool.get("available") else "not installed"
        extra = tool.get("count", tool.get("error", ""))
        tool_rows += f"<tr><td>{esc(tool.get('tool'))}</td><td>{esc(state)}</td><td>{esc(extra)}</td></tr>"
    if not tool_rows:
        tool_rows = "<tr><td colspan='3'>No tool metadata</td></tr>"

    cards = []
    for idx, item in enumerate(findings, start=1):
        sev = str(item.get("severity") or "medium").lower()
        loc = str(item.get("file_path") or "")
        line = item.get("line")
        loc_s = f"{loc}:{line}" if loc and isinstance(line, int) else (loc or "n/a")
        tags = "".join(
            f"<span class='tag'>{esc(v)}</span>"
            for v in (
                item.get("category"),
                f"OWASP {item.get('owasp')} {item.get('owasp_name')}" if item.get("owasp") else None,
                item.get("cwe"),
                f"rule {item.get('id')}",
                f"source {item.get('source')}",
            )
            if v
        )
        cards.append(
            f"""
<article class="card" style="border-left-color:{_SEV_COLORS.get(sev, '#a16207')}">
  <header>{_chip(sev)}<h3>#{idx} {esc(item.get('summary') or item.get('id'))}</h3></header>
  <div><span class="loc">{esc(loc_s)}</span></div>
  <p style="margin:8px 0 4px">{tags}</p>
  <p style="margin:6px 0"><b>Impact:</b> {esc(item.get('explanation'))}</p>
  <div class="proof"><b>Proof of exploitability</b><br/>Payload: <code>{esc(item.get('malicious_input_example'))}</code><br/>{esc(item.get('poc_sketch'))}</div>
  <div class="fix"><b>Fix:</b> {esc(item.get('recommendation'))}</div>
</article>"""
        )
    if not cards:
        cards.append(
            "<div class='card' style='border-left-color:#15803d'><h3>No finding kept</h3>"
            "<p class='muted'>Every layer ran and nothing proven reached the report. Keep the host scanners "
            "installed and rerun after each release.</p></div>"
        )

    plan_items = ""
    for step in result.get("remediation_plan") or []:
        files = ", ".join(str(f) for f in (step.get("files") or [])[:4])
        plan_items += (
            f"<li><b>{esc(step.get('title'))}</b> {_chip(str(step.get('severity')))} "
            f"<span class='tag'>{int(step.get('count') or 0)} occurrence(s)</span>"
            f"<span class='tag'>effort {esc(step.get('effort'))}</span>"
            f"<div class='muted'>{esc(step.get('action'))}</div>"
            + (f"<div class='muted'><code>{esc(files)}</code></div>" if files else "")
            + "</li>"
        )
    if not plan_items:
        plan_items = "<li class='muted'>Nothing to fix from this pass.</li>"

    kinds = result.get("file_kinds") or {}
    kinds_s = ", ".join(f"{esc(k)} {int(v)}" for k, v in sorted(kinds.items(), key=lambda kv: -int(kv[1])))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{esc(title)} - {esc(root)}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<section class="cover">
  <div class="ring">{_score_ring(score, grade)}<div class="grade">Grade <b style="color:{_GRADE_COLORS.get(grade, '#fff')};font-size:16px">{esc(grade)}</b></div></div>
  <div style="flex:1;min-width:280px">
    <h1>{esc(title)}</h1>
    <div class="sub"><code>{esc(root)}</code></div>
    <div class="meta">
      <span>Date <b>{esc(ts)}</b></span>
      <span>Mode <b>{esc(result.get('kind'))}</b></span>
      <span>Files scanned <b>{int(result.get('files_scanned') or 0)}</b></span>
      <span>Duration <b>{esc(_fmt_duration(int(result.get('duration_ms') or 0)))}</b></span>
      <span>Findings <b>{len(findings)}</b></span>
      <span>Dropped as unproven <b>{int(result.get('dropped_count') or 0)}</b></span>
    </div>
  </div>
</section>

<h2>1. Executive summary</h2>
<div class="grid">{kpis}</div>
<p class="muted" style="margin-top:12px">Score 100 minus weighted deductions per severity (critical 20, high 10, medium 4, low 1), each capped.
File kinds covered: {kinds_s or 'none'}. Findings without a proof or with speculative wording were removed before this report.</p>

<h2>2. OWASP Top 10 (2021) coverage</h2>
<table><thead><tr><th>Id</th><th>Class</th><th>Findings</th><th>Share</th></tr></thead><tbody>{owasp_rows}</tbody></table>

<h2>3. Categories and hot spots</h2>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
<table><thead><tr><th>Category</th><th>Findings</th></tr></thead><tbody>{cat_rows}</tbody></table>
<table><thead><tr><th>File</th><th>Findings</th></tr></thead><tbody>{file_rows}</tbody></table>
</div>

<h2>4. Project checks</h2>
<div class="kpi">{checks_html}</div>

<h2>5. Findings ({len(findings)})</h2>
{''.join(cards)}

<h2>6. Remediation plan</h2>
<ol class="plan">{plan_items}</ol>

<h2>7. Method and tools</h2>
<table><thead><tr><th>Tool</th><th>State</th><th>Findings / note</th></tr></thead><tbody>{tool_rows}</tbody></table>
<p class="muted">{esc(result.get('note'))}</p>

<footer>Generated by Navin Security - {esc(ts)} - contact@navinspire.com</footer>
</div></body></html>
"""


# PDF ---------------------------------------------------------------------------


def pdf_backends_available() -> list[str]:
    """Installed PDF backends, in preference order.

    Only a missing module counts as "not installed": any other import error
    (a broken native library for WeasyPrint, for instance) propagates so the
    host problem is visible instead of silently degrading the report.
    """
    out: list[str] = []
    for name in ("weasyprint", "reportlab"):
        if importlib.util.find_spec(name) is not None:
            out.append(name)
    return out


def _pdf_with_weasyprint(html_doc: str) -> bytes:
    from weasyprint import HTML

    return bytes(HTML(string=html_doc).write_pdf())


_TAG = re.compile(r"<[^>]+>")


def _plain(value: Any, limit: int = 600) -> str:
    text = _TAG.sub("", str(value or ""))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text[:limit]


def _pdf_with_reportlab(result: dict[str, Any], *, title: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    h1 = styles["Title"]
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=11, textColor=colors.HexColor("#475569"))
    mono = ParagraphStyle("mono", parent=body, fontName="Courier", fontSize=8.5, leading=11)

    findings = [f for f in (result.get("findings") or []) if isinstance(f, dict)]
    counts = dict(result.get("counts") or {})
    score = int(result.get("score") or 0)
    grade = str(result.get("grade") or "F")

    story: list[Any] = [Paragraph(_plain(title), h1)]
    story.append(Paragraph(_plain(result.get("root")), small))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"Score <b>{score}/100</b> (grade <b>{_plain(grade)}</b>). "
            f"Mode {_plain(result.get('kind'))}, {int(result.get('files_scanned') or 0)} files scanned in "
            f"{_plain(_fmt_duration(int(result.get('duration_ms') or 0)))}, {len(findings)} findings kept, "
            f"{int(result.get('dropped_count') or 0)} dropped as unproven.",
            body,
        )
    )
    story.append(Spacer(1, 8))

    sev_table = Table(
        [[s.capitalize() for s in _SEV_ORDER], [str(int(counts.get(s, 0))) for s in _SEV_ORDER]],
        colWidths=[32 * mm] * 5,
    )
    sev_style = TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 1), (-1, 1), 14),
            ("TOPPADDING", (0, 1), (-1, 1), 6),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
        ]
    )
    for i, sev in enumerate(_SEV_ORDER):
        sev_style.add("TEXTCOLOR", (i, 1), (i, 1), colors.HexColor(_SEV_COLORS[sev]))
        sev_style.add("BACKGROUND", (i, 0), (i, 0), colors.HexColor("#f1f5f9"))
    sev_table.setStyle(sev_style)
    story.append(sev_table)

    story.append(Paragraph("OWASP Top 10 (2021)", h2))
    owasp_rows = [["Id", "Class", "Findings"]]
    for row in result.get("by_owasp") or []:
        owasp_rows.append([_plain(row.get("id")), _plain(row.get("name")), str(int(row.get("count") or 0))])
    owasp = Table(owasp_rows, colWidths=[16 * mm, 120 * mm, 24 * mm], repeatRows=1)
    owasp.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (2, 1), (2, -1), "CENTER"),
            ]
        )
    )
    story.append(owasp)

    story.append(Paragraph("Project checks", h2))
    for check in result.get("project_checks") or []:
        detail = f" - {_plain(check.get('detail'))}" if check.get("detail") else ""
        story.append(
            Paragraph(
                f"<b>[{_plain(str(check.get('status') or 'info').upper())}]</b> {_plain(check.get('title'))}{detail}",
                body,
            )
        )

    story.append(PageBreak())
    story.append(Paragraph(f"Findings ({len(findings)})", h2))
    if not findings:
        story.append(Paragraph("No finding kept: every layer ran and nothing proven reached the report.", body))
    for idx, item in enumerate(findings, start=1):
        loc = _plain(item.get("file_path"))
        line = item.get("line")
        loc_s = f"{loc}:{line}" if loc and isinstance(line, int) else (loc or "n/a")
        sev = str(item.get("severity") or "medium").upper()
        story.append(
            Paragraph(
                f"<font color='{_SEV_COLORS.get(sev.lower(), '#a16207')}'><b>{_plain(sev)}</b></font> "
                f"#{idx} <b>{_plain(item.get('summary') or item.get('id'))}</b>",
                body,
            )
        )
        story.append(Paragraph(loc_s, mono))
        story.append(
            Paragraph(
                f"{_plain(item.get('category'))} | OWASP {_plain(item.get('owasp'))} {_plain(item.get('owasp_name'))} | "
                f"{_plain(item.get('cwe'))} | rule {_plain(item.get('id'))} | source {_plain(item.get('source'))}",
                small,
            )
        )
        story.append(Paragraph(f"<b>Impact:</b> {_plain(item.get('explanation'))}", body))
        story.append(
            Paragraph(
                f"<b>Proof:</b> payload <font face='Courier'>{_plain(item.get('malicious_input_example'), 200)}</font>. "
                f"{_plain(item.get('poc_sketch'))}",
                body,
            )
        )
        story.append(Paragraph(f"<b>Fix:</b> {_plain(item.get('recommendation'))}", body))
        story.append(Spacer(1, 7))

    story.append(PageBreak())
    story.append(Paragraph("Remediation plan", h2))
    for step in result.get("remediation_plan") or []:
        files = ", ".join(_plain(f, 120) for f in (step.get("files") or [])[:4])
        story.append(
            Paragraph(
                f"<b>{int(step.get('order') or 0)}. {_plain(step.get('title'))}</b> "
                f"({_plain(step.get('severity'))}, {int(step.get('count') or 0)} occurrence(s), effort {_plain(step.get('effort'))})",
                body,
            )
        )
        story.append(Paragraph(_plain(step.get("action")), small))
        if files:
            story.append(Paragraph(files, mono))
        story.append(Spacer(1, 4))

    story.append(Paragraph("Method and tools", h2))
    for tool in result.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        state = "available" if tool.get("available") else "not installed"
        story.append(
            Paragraph(
                f"{_plain(tool.get('tool'))}: {state} {_plain(tool.get('count', tool.get('error', '')))}",
                small,
            )
        )
    story.append(Paragraph(_plain(result.get("note"), 800), small))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
        author="Navin Security",
    )
    doc.build(story)
    return buf.getvalue()


def render_deep_report_pdf(result: dict[str, Any], *, title: str = "Security assessment") -> bytes:
    """PDF bytes: WeasyPrint from the HTML when present, else ReportLab.

    Raises RuntimeError when no backend is installed.
    """
    backends = pdf_backends_available()
    errors: list[str] = []
    if "weasyprint" in backends:
        try:
            return _pdf_with_weasyprint(render_deep_report_html(result, title=title))
        except Exception as exc:
            # WeasyPrint needs native libs (pango, cairo); when they are broken
            # the error is kept and reported, and ReportLab takes over.
            errors.append(f"weasyprint: {exc}")
    if "reportlab" in backends:
        try:
            return _pdf_with_reportlab(result, title=title)
        except Exception as exc:
            errors.append(f"reportlab: {exc}")
    detail = "; ".join(errors) if errors else "install weasyprint or reportlab"
    raise RuntimeError(f"no PDF backend available ({detail})")
