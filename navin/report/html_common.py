# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared CSS / fragments for Review, Security, and Debug HTML reports."""

from __future__ import annotations

import html
from typing import Any


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


EXPERT_CSS = """
:root { color-scheme: light dark; }
body { margin:0; font:15px/1.55 system-ui,Segoe UI,sans-serif;
  background:#0b1220; color:#e2e8f0; }
.wrap { max-width:920px; margin:0 auto; padding:2rem 1.25rem 4rem; }
h1 { font-size:1.75rem; margin:0 0 .35rem; }
h2 { font-size:1.15rem; margin:2rem 0 .75rem; border-bottom:1px solid #334155;
  padding-bottom:.35rem; }
h3 { font-size:1rem; margin:.2rem 0; }
.muted { color:#94a3b8; font-size:.9rem; }
.panel, .card, .choice { background:#111827; border:1px solid #1f2937;
  border-radius:14px; padding:1rem 1.1rem; margin:0 0 .85rem; }
.card header { display:flex; flex-wrap:wrap; gap:.6rem; align-items:center; }
.proof { background:#0b1220; border:1px dashed #334155; border-radius:10px;
  padding:.75rem .9rem; margin:.6rem 0; }
.proof pre, pre { white-space:pre-wrap; background:#0b1220; padding:.6rem .75rem;
  border-radius:8px; font-size:12px; margin:.35rem 0; }
.summary { display:grid; grid-template-columns:1fr 1fr; gap:1rem; }
@media (max-width:720px) { .summary { grid-template-columns:1fr; } }
table.deliv { width:100%; border-collapse:collapse; font-size:.9rem; }
table.deliv th, table.deliv td { text-align:left; padding:.45rem .5rem;
  border-bottom:1px solid #1f2937; vertical-align:top; }
.verdict { display:inline-block; padding:.35rem .8rem; border-radius:999px;
  background:#1d4ed8; color:#fff; font-weight:600; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.86em;
  word-break:break-all; }
footer { margin-top:2.5rem; color:#64748b; font-size:.8rem; }
@media print {
  body { background:#fff; color:#000; }
  .panel, .card, .choice, .proof { background:#fff; border:1px solid #ccc;
    color:#000; }
  .muted, footer { color:#444; }
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
