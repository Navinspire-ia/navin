---
name: lead-generation
description: Identify target companies and decision-makers matching the ICP — build clean, sourced prospect lists. Use to fill the top of the sales pipeline.
metadata: {"navin":{"emoji":"🧲","category":"sales"}}
---

# Lead Generation

## Overview

Build prospect lists that sales can actually use: right companies, right people, verified context, sourced.

## List quality bar

Per lead: company, website, size estimate, sector, country, decision-maker name + role, LinkedIn URL, trigger/context signal, source of each datum.

## Sourcing plays

| Play | How |
|------|-----|
| ICP search | `web_search` "{sector} {geography} companies", directories, chambers, industry associations |
| Trigger events | funding news, expansions, job postings for related roles, new regulations — these companies have budget + urgency |
| Ecosystem mining | competitors' case studies, event exhibitor lists, partner directories |
| Lookalikes | best current clients → who resembles them (`entity-research` per company) |
| Inbound signals | site visitors' companies, content engagers, event attendees |

## Workflow

1. Load the ICP (`customer-persona-builder`) — disqualifiers matter as much as criteria.
2. Run 2–3 sourcing plays; collect into `sales/leads-<date>.md` or CSV.
3. Enrich each company: `web_fetch` their site (size signals, tech, news); find the right role's holder on LinkedIn.
4. Dedupe against existing CRM/pipeline; score A/B/C by ICP fit + trigger strength.
5. Hand A-leads to `lead-qualification` → `account-research` → `cold-email-writer`.

## Rules

- Every email/name verified from a public source; no scraped-and-prayed data.
- Respect data protection rules (GDPR for EU contacts) — business contact data, lawful basis, easy opt-out.
- 50 researched leads beat 5,000 scraped rows.
