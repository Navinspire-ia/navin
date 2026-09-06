---
name: outreach-sequencer
description: Design multi-touch outreach sequences - email, LinkedIn, phone - with timing, angles, and stop conditions. Use to systematize prospect follow-up.
metadata: {"navin":{"emoji":"⏱️","category":"sales"}}
---

# Outreach Sequencer

Most replies come from touches 2-5. Sequence them with a NEW angle each time - a follow-up that adds nothing teaches the prospect to ignore you. This skill designs cadences; it does not auto-send.

On the Leads desk, `leads action=sequence` starts j0/j3/j7. Heartbeat `watch` alerts when a step is due and never sends. The desk loop never sends. Human or `leads action=outreach` sends.

## When to use

- Multi-touch email / social / phone plans
- Segment-specific cadences after qualification

## When not to use

- Single one-off email (use `cold-email-writer`)
- Guaranteed send automation (out of scope; prepare + human send)

## Reference sequence (B2B)

| Touch | Day | Channel | Angle |
|-------|-----|---------|-------|
| 1 | 0 | Email | researched hook |
| 2 | 3 | LinkedIn | short connection note |
| 3 | 6 | Email | new value (case / insight) |
| 4 | 10 | LinkedIn/phone | comment or call |
| 5 | 15 | Email | different pain angle |
| 6 | 22 | Email | polite breakup |

Adapt: enterprise = slower + more research; SMB = tighter.

## Stop conditions

- Any reply → human takes over
- Opt-out / not interested → stop + log
- Trigger event changes context → re-personalize

## Workflow

1. Segment from `lead-qualification` tiers - sequence per segment.
2. Write all touches upfront with `cold-email-writer` quality bar.
3. Schedule reminders via `cron` if useful; log touches in `sales/outreach-log.md` or CRM (`crm-update-agent` / HubSpot MCP).
4. Weekly: reply rate per touch/angle → rewrite the weakest.

## Rules

- Sends require human validation (`human-approval`) - agent prepares and reminds.
- Max 1 active sequence per prospect; 90-day cooldown after completion.
- Breakup emails are polite and final - no guilt-tripping.
- Compliance: honest identity, honor opt-out instantly.

## Anti-patterns

- "Just bumping this" with no new value
- Parallel sequences to the same person
