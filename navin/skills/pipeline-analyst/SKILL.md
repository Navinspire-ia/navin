---
name: pipeline-analyst
description: Analyze the sales pipeline - stuck deals, forecast quality, conversion by stage, and next best actions. Use for weekly pipeline reviews and forecasting.
metadata: {"navin":{"emoji":"📉","category":"sales"}}
---

# Pipeline Analyst

Read the pipeline like vitals: where deals stall, what the forecast is worth, what to do Monday morning. Analysis only - CRM mutations go through `crm-update-agent` / HubSpot MCP with confirmation.

## When to use

- Weekly pipeline reviews
- Forecast scrub before leadership commits a number

## When not to use

- Building net-new lead lists
- Blind optimism forecasts without stage evidence

## Health checks

| Check | Red flag |
|-------|----------|
| Stage age | deal in stage > 2× median |
| Next steps | open deal without dated next step |
| Coverage | pipeline < 3× period target |
| Slippage | close date pushed 2+ times |
| Concentration | one deal > 40% of forecast |
| Conversion | stage leak vs historical |

## Analyses

1. **Stuck deals** - last buyer action, objection, champion → re-engage, multithread, or kill
2. **Forecast scrub** - commit / best-case / pipeline from buyer evidence, not hope
3. **Funnel diagnosis** - weakest stage gets the process fix

## Workflow

1. Source: CRM export/API (`crm-update-agent`, HubSpot MCP) or `sales/crm/` files; analyze with `exec` + Python when non-trivial.
2. Run health checks; build the review doc.
3. Optional Monday `cron` brief: top risks, opportunities, 5 actions.
4. Save `sales/pipeline-review-<date>.md` (+ chart/CSV if useful).

## Review format

```markdown
## Pipeline review - <date>
Total: X deals / Y weighted
### At risk (action needed)
### Watch
### Forecast: commit / best case (assumptions)
### This week's 5 actions
### Data gaps
```

## Rules

- Every "at risk" call cites evidence (dates, silence, stage age).
- Dare to recommend killing zombies.
- Label forecast confidence; never invent CRM amounts.

## Anti-patterns

- Sandbagging or hockey-stick without stage math
- Reviews with no next actions
