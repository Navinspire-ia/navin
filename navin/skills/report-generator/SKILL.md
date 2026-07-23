---
name: report-generator
description: Assemble recurring or one-off business reports — data, charts, narrative, and formatted output (PDF/DOCX/Markdown) — from multiple sources. Use for weekly/monthly reports and study deliverables.
metadata: {"navin":{"emoji":"📊","category":"documents"}}
---

# Report Generator

## Overview

A report = data + narrative + format. Automate the pipeline so the recurring version costs minutes, not days.

## Report anatomy

1. **Executive summary** — the 5 numbers and 3 messages a busy reader needs (written last)
2. **Sections** — per topic: chart/table + 2–4 sentences of interpretation ("so what", not "the chart shows")
3. **Appendix** — methodology, data sources + dates, detailed tables

## Pipeline

```
sources (CSV/API/DB/notes) → collect (exec/`database-explorer`)
  → compute (pandas, `spreadsheet-analyst`)
  → charts (matplotlib → PNG)
  → narrative (drafted per section)
  → render (`pdf-generator` HTML route / `docx-generator` / Markdown)
```

Keep the whole pipeline as a script + config in `reports/<name>/` so reruns are one command.

## Workflow

1. Define with the user: audience, questions the report answers, sources, frequency, format.
2. Build the pipeline; hardcode nothing that changes per period (dates, paths → parameters).
3. First edition: validate numbers against sources manually; validate structure with the user.
4. Recurring: schedule via `cron`; each run regenerates data + charts, drafts fresh narrative on the new numbers, flags notable changes vs previous period.
5. Never ship silently — the narrative interpretation gets a human glance for sensitive reports.

## Chart discipline

- One message per chart, stated in the chart title ("CA +18% porté par le segment public")
- Same scales/colors across periods for comparability; label directly, avoid legend hunting

## Rules

- Every number traceable to a source and date; methodology in the appendix.
- Period-over-period comparison is mandatory — a number without reference is noise.
- Live KPI dashboards → `kpi-reporter`; this skill produces documents.
