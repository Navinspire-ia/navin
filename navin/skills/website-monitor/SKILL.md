---
name: website-monitor
description: Detect changes in price, content, tenders, or availability and alert only on meaningful deltas. Use with cron or HEARTBEAT.md for recurring checks.
metadata: {"navin":{"emoji":"📡","category":"navigation"}}
---

# Website Monitor

## Overview

Periodic watch with quiet success. Store a baseline fingerprint; notify on delta.

## Workflow

1. Define URL, CSS/text target, and what counts as a change.
2. Fetch current content (`web_fetch` / Playwright if needed).
3. Normalize (strip volatile bits: dates, CSRF tokens, ads).
4. Compare to baseline file e.g. `monitoring/<slug>.json` in the workspace:
   - hash or extracted fields (price, status, headline)
5. If changed: update baseline + notify with **old → new**.
6. Schedule via:
   - `cron` when the user wants reports in chat
   - `HEARTBEAT.md` when silence is preferred unless changed

## Baseline schema

```json
{
  "url": "...",
  "extracted": {"price": "12.00", "status": "in_stock"},
  "hash": "...",
  "checked_at": "..."
}
```

## Rules

- Do not spam on every HTML churn — compare extracted fields.
- Include the URL and timestamp in alerts.
