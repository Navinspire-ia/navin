# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared CSS / fragments for Review, Security, and Debug HTML reports."""

from __future__ import annotations

import html
from typing import Any


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


EXPERT_CSS = """
:root { color-scheme: light dark; --bg:#f6f7f9; --fg:#1f2937; --muted:#5b6474; --card:#ffffff;
  --line:#e2e6ed; --code:#f1f4f8; --accent:#1d4ed8; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1420; --fg:#e5e9f0; --muted:#9aa4b5; --card:#161c2a; --line:#273043;
    --code:#0b1020; --accent:#6ea0ff; }
}
body { margin:0; font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;
  background:var(--bg); color:var(--fg); }
.wrap { max-width:960px; margin:0 auto; padding:2.25rem 1.25rem 4rem; }
h1 { font-size:1.8rem; margin:0 0 .4rem; letter-spacing:-.01em; }
h2 { font-size:1.2rem; margin:2.25rem 0 .8rem; border-bottom:1px solid var(--line);
  padding-bottom:.4rem; }
h3 { font-size:1.02rem; margin:.15rem 0; }
h4 { font-size:.78rem; margin:1rem 0 .35rem; text-transform:uppercase; letter-spacing:.05em;
  color:var(--muted); }
.muted { color:var(--muted); font-size:.9rem; }
.panel, .card, .choice { background:var(--card); border:1px solid var(--line);
  border-radius:14px; padding:1.1rem 1.25rem; margin:0 0 1rem;
  box-shadow:0 1px 2px rgba(15,23,42,.05); }
.card header { display:flex; flex-wrap:wrap; gap:.6rem; align-items:center; }
.meta { display:flex; flex-wrap:wrap; gap:.35rem 1rem; font-size:.88rem; color:var(--muted);
  margin:.5rem 0 .2rem; align-items:center; }
.proof { background:var(--code); border:1px solid var(--line); border-radius:10px;
  padding:.75rem .9rem; margin:.6rem 0; }
.proof pre, pre { white-space:pre-wrap; word-break:break-word; background:var(--code);
  color:var(--fg); border:1px solid var(--line); padding:.65rem .8rem; border-radius:8px;
  font-size:12.5px; line-height:1.5; margin:.35rem 0; overflow:auto; }
.fix { border-left:3px solid #15803d; background:rgba(21,128,61,.08); padding:.55rem .8rem;
  border-radius:6px; }
ol.steps { margin:.3rem 0 .4rem 1.3rem; padding:0; }
ol.steps li { margin:.2rem 0; }
.summary { display:grid; grid-template-columns:1fr 1fr; gap:1rem; }
@media (max-width:720px) { .summary { grid-template-columns:1fr; } }
table.deliv { width:100%; border-collapse:collapse; font-size:.9rem; }
table.deliv th, table.deliv td { text-align:left; padding:.45rem .5rem;
  border-bottom:1px solid var(--line); vertical-align:top; }
.verdict { display:inline-block; padding:.35rem .8rem; border-radius:999px;
  background:var(--accent); color:#fff; font-weight:600; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.86em;
  word-break:break-all; }
details > summary { cursor:pointer; color:var(--muted); }
footer { margin-top:2.5rem; color:var(--muted); font-size:.8rem; }
@media print {
  body { background:#fff; color:#000; }
  .panel, .card, .choice, .proof, pre { background:#fff; border:1px solid #ccc;
    color:#000; box-shadow:none; }
  .card { break-inside:avoid; }
  .muted, footer, h4 { color:#444; }
  .verdict { background:#ddd; color:#000; }
}
"""


def deliverables_table(rows: list[tuple[str, str, str]]) -> str:
    """rows: (path, format, description)."""
    body = "".join(
        "<tr>"
        f"<td><code>{esc(path)}</code></td>"
        f"<td>{esc(fmt)}</td>"
        f"<td>{esc(desc)}</td>"
        "</tr>"
        for path, fmt, desc in rows
    )
    if not body:
        body = '<tr><td colspan="3" class="muted">No deliverables listed.</td></tr>'
    return f"""
<table class="deliv">
  <thead><tr><th>File</th><th>Format</th><th>Contents</th></tr></thead>
  <tbody>{body}</tbody>
</table>
"""
