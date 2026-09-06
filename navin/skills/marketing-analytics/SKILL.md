---
name: marketing-analytics
description: Measure CAC, conversion rates, attribution, ROAS, CPL, and pipeline contribution from marketing data. Use to evaluate channels and build marketing reports.
metadata: {"navin":{"emoji":"📊","category":"marketing"}}
---

# Marketing Analytics

## Overview

Turn exports (GA4, ads platforms, CRM, spreadsheets) into decisions: which channel earns its budget, where the funnel leaks.

## Core metrics

| Metric | Formula |
|--------|---------|
| CPL | spend ÷ leads |
| CAC | spend ÷ new customers |
| Conversion rate | step N+1 ÷ step N |
| ROAS | revenue ÷ ad spend |
| Payback | CAC ÷ monthly gross margin per customer |
| Pipeline velocity | opportunities × win rate × deal size ÷ cycle length |

## Workflow

1. Get the data: user exports CSVs (GA4, ads, CRM) into the workspace, or connect via available tools.
2. Analyze with `exec` + Python (pandas): clean, join on UTM/campaign, compute the metrics table.
3. Build the funnel: visitors → leads → MQL → opportunities → won, with conversion % per step.
4. Attribution honestly: first-touch and last-touch views side by side; flag dark-social gaps.
5. Deliver: monthly scoreboard + 3 insights + 3 recommended actions (`kpi-reporter` for recurring versions).

## Report skeleton

```markdown
## Marketing scoreboard - <month>
| Channel | Spend | Leads | CPL | Opps | Won | CAC | Notes |
### Insights
### Actions
```

## Rules

- Distinguish correlation from causation explicitly.
- If data is missing or dirty, say so - no invented precision.
- Trends over single data points; always show the previous period.
