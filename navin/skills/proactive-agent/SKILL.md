---
name: proactive-agent
description: Run periodic checks, anticipate follow-ups, resume after interruptions, and keep quiet when nothing changed. Use with cron, .navin/HEARTBEAT.md, and sustained goals (/goal) for autonomous monitoring.
metadata: {"navin":{"emoji":"🛰️","category":"intelligence"}}
---

# Proactive Agent

## Overview

Operate as a reliable background partner: schedule work, recover from interruptions, and only notify when there is signal.

## Tools

- `cron` - reminders and recurring agent tasks that report back to the chat
- `.navin/HEARTBEAT.md` - quiet periodic checks (update the file; the heartbeat job runs it)
- `/goal` / goal tools - sustained objectives across turns
- `spawn` - long independent tracks

## When to use cron vs HEARTBEAT

| Need | Mechanism |
|------|-----------|
| User-facing reminder / report | `cron` |
| Quiet “check and only speak if useful” | `.navin/HEARTBEAT.md` |
| Multi-turn project with state | `/goal` |

## Workflow - schedule

1. Confirm cadence and timezone.
2. Write a **self-contained** task message (the future turn has limited chat context).
3. Prefer `cron_expr` + `tz` for calendar schedules; `every_seconds` for short loops.
4. List jobs with `cron(action="list")` before adding duplicates.

## Workflow - resume after interruption

1. Re-read the last plan / goal state / relevant files.
2. Restate “where we left off” in one sentence.
3. Continue from the next incomplete step - do not restart completed work.
4. If blocked, say what is blocked and the cheapest unblock.

## Notification discipline

- Heartbeat / monitors: **no news is silent**.
- Failures, security issues, and deadline slips: **notify immediately**.
- Deduplicate: if the same alert fired recently, summarize the delta only.

## Recovery checklist

After an error or restart:

1. What was the objective?
2. What already succeeded? (check artifacts)
3. What is the next safe action?
4. Do we need user approval before destructive steps?
