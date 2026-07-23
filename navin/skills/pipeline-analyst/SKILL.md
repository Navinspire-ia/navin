---
name: pipeline-analyst
description: Analyze the sales pipeline — stuck deals, forecast quality, conversion by stage, and next best actions. Use for weekly pipeline reviews and forecasting.
metadata: {"navin":{"emoji":"📉","category":"sales"}}
---

# Pipeline Analyst

## Overview

Read the pipeline like a doctor reads vitals: where deals stall, what the forecast is really worth, what to do Monday morning.

## Health checks

| Check | Red flag |
|-------|----------|
| Stage age | deal in stage > 2× median for that stage |
| Next steps | any open deal without a dated next step |
| Coverage | pipeline < 3× target for the period |
| Slippage | close dates pushed 2+ times |
| Concentration | one deal > 40% of the forecast |
| Stage conversion | a stage leaking well below historical rate |

## Analyses

1. **Stuck deal review** — for each: last buyer action, blocking objection (`objection-handler`), champion status → recommended move (re-engage angle, multithread, or kill honestly)
2. **Forecast scrub** — per deal: commit/best-case/pipeline based on buyer evidence, not rep optimism; output a weighted number with assumptions listed
3. **Funnel diagnosis** — conversion per stage over time; the weakest stage gets the process fix (discovery quality? proposal timing? pricing?)

## Workflow

1. Source data: CRM export/API (`crm-update-agent` backends) or `sales/crm/` files; analyze with `exec` + Python for anything non-trivial.
2. Run the health checks; build the review doc.
3. Weekly cadence via `cron`: Monday pipeline brief — top risks, top opportunities, 5 recommended actions.

## Review format

```markdown
## Pipeline review — <date>
Total: X deals / Y € weighted
### 🔴 At risk (action needed)
### 🟡 Watch
### Forecast: commit / best case
### This week's 5 actions
```

## Rules

- Every "at risk" call cites evidence (dates, silence, stage age).
- Killing zombie deals is a recommendation the analyst must dare to make.
