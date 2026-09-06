---
name: buying-signals
description: Detect and score buying signals - funding, hiring, tech changes, leadership moves, expansion, regulation. Use to prioritize which accounts to contact now and with what angle.
metadata: {"navin":{"emoji":"📡","category":"sales"}}
---

# Buying Signals

Timing wins deals. Scan open sources for events that indicate budget, urgency, or change, then rank accounts by signal strength and map each signal to an outreach angle.

The live book is Studio `#/leads` (`leads action=status`). Recurring scan is the desk loop (`leads action=start` / `stop` / `schedule`). Heartbeat already ran `leads action=watch` and stays silent when `watch.count` is 0. Never hunt or send from heartbeat. Do not create a chat cron.

## When to use

- Prioritizing who to contact this week from an account list
- Finding trigger-based angles for cold outreach

## When not to use

- Building the company list from scratch (start with `lead-prospector`)
- Inventing "intent scores" without evidence

## Signal catalog

| Signal | Where | Why | Angle |
|--------|-------|-----|-------|
| Funding | press, registries | fresh budget | "as you scale after the raise…" |
| Hiring spree | careers, job boards | new initiatives | role-specific pain |
| Job posting text | posting body | stack/pains verbatim | quote their needs |
| Leadership change | press, public posts | vendor reset window | new priorities opener |
| Expansion | offices/markets news | operational strain | localized offer |
| Tech change | jobs, eng blogs, repos | migration windows | integration/switch |
| Regulation | sector/official news | forced budget | deadline-driven |
| Public pain | reviews, forums, status pages | active dissatisfaction | empathetic fix |

## Workflow

1. Take accounts from `lead-prospector` or user list.
2. Targeted searches per signal; evidence = URL + date.
3. Score: strength (1-5) × recency (week ×1, month ×0.7, quarter ×0.4). Older than a quarter → `stale`.
4. Save ranked table: `account, signal, evidence_url, date, score, suggested_angle, suggested_timing`.
5. Flag top 5 "contact this week" with a ready opening line each.
6. Feed angles into `cold-email-writer` / `outreach-sequencer`.

## Rules

- Evidence or it did not happen.
- Separate facts from inference (mark inference clearly).
- Refresh honestly; do not recycle stale signals as urgent.

## Anti-patterns

- "They are probably buying" with no link
- Same generic angle for every signal type
