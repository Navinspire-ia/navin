---
name: tender-monitor
description: Watch public tenders, RFPs, and consultations across portals — filter by fit, alert on deadlines, and prep bid decisions. Use to never miss a relevant appel d'offres.
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

1. Build the watch profile with the user; store in `sales/tenders/profile.md`.
2. Recurring scan (`cron`, daily or 2×/week): `web_fetch`/`web_search` the sources; extract new notices — title, buyer, deadline, caution/bond, docs link.
3. Fit-score each notice against the profile (fit/effort/win-probability); report only relevant ones.
4. For pursued tenders: create `sales/tenders/<ref>/` with the notice, deadline countdown reminders (`cron` at J-15/J-7/J-2), and document checklist.
5. Hand to `rfp-writer` for the bid/no-bid analysis and response.

## Alert format

```markdown
## Nouveaux AO — <date>
| Réf | Acheteur | Objet | Deadline | Fit | Caution | Lien |
```

## Rules

- Deadlines in the alert are submission deadlines minus logistics margin.
- Track outcomes (won/lost/no-bid + price when published) — the history sharpens future bid decisions.
- Portal access requiring accounts: flag to the user, don't create accounts silently.
