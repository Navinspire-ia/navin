---
name: kpi-reporter
description: Build recurring KPI reports, dashboards summaries, and alerts from multiple data sources. Use for daily/weekly business reporting.
metadata: {"navin":{"emoji":"📉","category":"data"}}
---

# KPI Reporter

## Overview

Define metrics once, compute reliably, narrate briefly.

## Workflow

1. Lock metric definitions with the user (formulas, filters, TZ).
2. Pull from DBs/APIs/sheets; reuse `sql-analyst` patterns.
3. Compute current period vs previous; call out anomalies.
4. Output a short narrative + table; optional file under `reports/`.
5. Schedule with `cron` if they want recurring delivery.

## Rules

- Show as-of timestamps.
- Separate “data missing” from “metric is zero”.
