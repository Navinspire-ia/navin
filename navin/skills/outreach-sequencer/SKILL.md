---
name: outreach-sequencer
description: Design multi-touch outreach sequences — email, LinkedIn, phone — with timing, angles, and stop conditions. Use to systematize prospect follow-up.
metadata: {"navin":{"emoji":"⏱️","category":"sales"}}
---

# Outreach Sequencer

## Overview

Most replies come from touches 2–5. Sequence them with a NEW angle each time — a follow-up that adds nothing teaches the prospect to ignore you.

## Reference sequence (B2B)

| Touch | Day | Channel | Angle |
|-------|-----|---------|-------|
| 1 | 0 | Email | researched hook (`cold-email-writer`) |
| 2 | 3 | LinkedIn | connection request, short note |
| 3 | 6 | Email | new value: relevant case study or insight |
| 4 | 10 | LinkedIn/phone | comment on their content / direct call |
| 5 | 15 | Email | different pain angle |
| 6 | 22 | Email | polite breakup ("je clos de mon côté — si le sujet revient…") |

Adapt density to deal size: enterprise = slower + more research; SMB = tighter.

## Stop conditions (immediate)

- Any reply (positive or negative) → sequence stops, human takes over
- Opt-out / "not interested" → stop + log, no breakup email
- Trigger event changes context → re-personalize, don't continue blindly

## Workflow

1. Segment the list (from `lead-qualification`): sequence per segment, not per universe.
2. Write all touches upfront; each must stand alone AND escalate value.
3. Execution: schedule reminders with `cron`; log every touch + outcome in `sales/outreach-log.md` (or CRM via `crm-update-agent`).
4. Weekly stats: reply rate per touch and per angle → rewrite the weakest touch.

## Rules

- Sends require human validation (`human-approval`) — the agent prepares and reminds.
- Max 1 sequence per prospect at a time; 90-day cooldown after a completed sequence.
- Breakup emails are polite and final — no guilt-tripping.
