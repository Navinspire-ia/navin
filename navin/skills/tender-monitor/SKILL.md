---
name: tender-monitor
description: Watch public tenders, RFPs, and consultations across portals - filter by fit, alert on deadlines, and prep bid decisions. Use to never miss a relevant appel d'offres.
metadata: {"navin":{"emoji":"📯","category":"sales"}}
---

# Tender Monitor

## Overview

Tenders are time-boxed opportunities: found late = lost. Systematic watch on the right portals, fit-filtered, deadline-tracked.

## Watch setup

1. **Profile**: sectors/CPV-like categories, keywords (FR/EN/AR), geographies, size range, disqualifiers (required certs we lack)
2. **Sources per market**:
   - Algeria: BAOSEM, portails ministériels, ANEP press announcements
   - Gulf: Etimad (KSA), national procurement portals
   - EU/France: BOAMP, TED (ted.europa.eu)
   - Private: target companies' procurement pages, sector newsletters

## Workflow

1. Build the watch profile with the user on the live desk (`tenders action=profile`). The book is `~/.navin/tenders/` (same store as Studio `#/tenders`, Tauri, `navin tenders`, and `python -m navin.tenders.desk_cli`).
2. Recurring hunt is the Studio Start loop (`tenders action=start` / `stop` / `schedule`). Do not create a chat cron that collects or ticks. The gateway hunts collect then watch on that calendar.
3. Heartbeat uses `tenders action=follow` and stays silent when `watch.count` is 0. Never collect, start or tick from heartbeat. Do not create a chat cron that collects or ticks.
4. Fit-score each notice with `tenders action=qualify`. Report only GO or a deadline under 7 days.
5. For pursued tenders: `tenders action=write` then `rfp-writer`. Deadline reminders stay on heartbeat, never a buyer mail.
6. Hand leftover public listings to `/scrape` or `scrape` + `web_search` on official hosts only.

## Alert format

```markdown
## Nouveaux AO - <date>
| Réf | Acheteur | Objet | Deadline | Fit | Caution | Lien |
```

## Rules

- Deadlines in the alert are submission deadlines minus logistics margin.
- Track outcomes (won/lost/no-bid + price when published) - the history sharpens future bid decisions.
- Portal access requiring accounts: flag to the user, don't create accounts silently.
- LinkedIn MCP (`linkedin-mcp-server`, Tools > Tenders MCP) is buyer research on the live desk, never a notice source. Do not collect LinkedIn jobs or posts into the watch list.
