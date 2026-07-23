---
name: buying-signals
description: Detect and score buying signals — funding, hiring, tech changes, leadership moves, expansion, regulation. Use to prioritize which accounts to contact now and with what angle.
metadata: {"navin":{"emoji":"📡","category":"sales"}}
---

# Buying Signals

## Overview

Timing wins deals. Scan open sources for events that indicate budget, urgency, or change at target accounts, then rank accounts by signal strength and map each signal to an outreach angle.

## Signal catalog

| Signal | Where to find it | Why it matters | Typical angle |
|--------|------------------|----------------|---------------|
| Funding round | funding news, press releases, registries | fresh budget, growth mandate | "as you scale after the raise…" |
| Hiring spree | careers pages, job boards (`web_search` "{company} jobs {role}") | team growth, new initiatives, tooling needs | role-specific pain |
| Job posting content | the posting text itself | reveals stack, projects, pains verbatim | quote their own needs |
| Leadership change | press, LinkedIn announcements | new execs change vendors in their first 100 days | "new priorities" opener |
| Expansion | new offices/markets/products in press | operational strain, new needs | localized offer |
| Tech change | job posts, engineering blogs, public repos | migration windows | integration/switch pitch |
| Regulation | sector news, official texts | compliance deadlines = forced budget | deadline-driven pitch |
| Public pain | reviews, forums, social complaints, incident pages | active dissatisfaction | empathetic fix |

## Workflow

1. Take the account list (or build one via `lead-prospector`).
2. For each account, run targeted searches per signal type; collect evidence with URL + date.
3. Score: signal strength (1-5) × recency decay (this week ×1, this month ×0.7, this quarter ×0.4).
4. Deliver a ranked table saved to the workspace: `account, signal, evidence_url, date, score, suggested_angle, suggested_timing`.
5. Flag the top 5 "contact this week" accounts with a ready-to-send opening line each.

## Rules

- Evidence or it did not happen: every signal carries a link and a date.
- Distinguish facts (raised €2M on {date}) from inference (likely migrating CRM).
- Refresh honestly: mark signals older than one quarter as stale.
