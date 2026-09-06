---
name: competitor-intelligence
description: Monitor competitors' offers, pricing, campaigns, hiring, and positioning changes over time. Use for ongoing competitive awareness, not one-shot analysis.
metadata: {"navin":{"emoji":"🔭","category":"marketing"}}
---

# Competitor Intelligence

## Overview

Systematic watch on competitors: what changed, what it means, what to do. Complements the one-shot `competitor-seo-analysis`.

## What to watch

| Signal | Where | Meaning |
|--------|-------|---------|
| Pricing page changes | their site | positioning/margin moves |
| New features/pages | site, changelog, blog | roadmap direction |
| Job postings | careers, LinkedIn | strategy (new market? new tech?) |
| Campaigns/ads | ad libraries, their social | messaging bets |
| Reviews | G2/Capterra/Google | their weaknesses = your angles |
| News/funding | press, LinkedIn | resources, urgency |

## Workflow

1. Define the watchlist: 3-7 competitors, which signals matter for the user.
2. Baseline snapshot per competitor stored in `intel/<competitor>.md` (offer, pricing, messaging, strengths/weaknesses).
3. Schedule recurring checks with `cron` (weekly/biweekly), using `web_fetch` + `website-monitor` techniques for change detection.
4. Only report *changes* + interpretation + suggested response.
5. Quarterly battlecard refresh: how to win against each competitor (feeds `objection-handler`).

## Digest format

```markdown
## Competitive digest - <date>
| Competitor | Change | So what | Suggested action |
```

## Rules

- Public sources only - no pretexting, no fake accounts.
- Interpretation is clearly separated from observed fact.
