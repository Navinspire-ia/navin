---
name: cron
description: Schedule reminders and recurring tasks.
---

# Cron

Use the `cron` tool to schedule reminders or recurring tasks that should report back to the originating chat/session when they run.

Do not use `cron` for periodic background checks that should stay quiet when there is nothing useful to report. For those, update `.navin/HEARTBEAT.md`; the protected heartbeat job runs those checks and only delivers results that pass the notification gate.

## Three Modes

1. **Reminder** - message is sent directly to user
2. **Task** - message is a task description, agent executes and sends result
3. **One-time** - runs once at a specific time, then auto-deletes

## Examples

Fixed reminder:
```
cron(action="add", message="Time to take a break!", every_seconds=1200)
```

Dynamic task (agent executes each time):
```
cron(action="add", message="Check Navinspire-ia/navin GitHub stars and report", every_seconds=600)
```

One-time scheduled task (compute ISO datetime from current time):
```
cron(action="add", message="Remind me about the meeting", at="<ISO datetime>")
```

Timezone-aware cron:
```
cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="America/Vancouver")
```

List/remove:
```
cron(action="list")
cron(action="remove", job_id="abc123")
```

## Time Expressions

| User says | Parameters |
|-----------|------------|
| every 20 minutes | every_seconds: 1200 |
| every hour | every_seconds: 3600 |
| every day at 8am | cron_expr: "0 8 * * *" |
| weekdays at 5pm | cron_expr: "0 17 * * 1-5" |
| 9am Vancouver time daily | cron_expr: "0 9 * * *", tz: "America/Vancouver" |
| at a specific time | at: ISO datetime string (compute from current time) |

## Timezone

Use `tz` with `cron_expr` to schedule in a specific IANA timezone. Without `tz`, the server's local timezone is used.

## Loops

`/loop [interval] <prompt>` runs the prompt once now, then keeps running it in this chat. Loop jobs are named `loop-<slug>` (same prompt, same name); `/loop list` shows them and `/loop stop <name|all>` ends them.

- **Fixed** (`/loop 5m check the deploy`, `run tests every 10 minutes`, `toutes les 10 minutes`, `every weekday at 9am standup`): Navin creates a recurring task job (`every_seconds` or `cron_expr`). Starting the same loop again replaces it. During a fixed tick, do the task and report; never create, change or remove its cron job.
- **Dynamic** (`/loop watch the CI run`, no interval): after each tick you arm the next one yourself with `cron(action="add", name="loop-<slug>", at="<ISO now + delay>", message=<the loop message verbatim>)`. Use 1-5 minutes while something is changing, 15-60 minutes when idle, and stop (do not re-arm) once the work is done. Re-arming a `loop-*` name replaces its pending tick.

A loop reports on every tick. For silent checks that should only speak up when something actionable changes, add a task to `.navin/HEARTBEAT.md` instead of starting a loop.
