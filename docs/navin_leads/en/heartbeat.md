# Leads heartbeat

Heartbeat **only alerts**. It does not hunt, does not arm the loop, and never sends a sequence.

`tick_heartbeat_desks` calls `navin.leads.heartbeat.tick_watch` **before** the LLM turn. The **Lead scores** section in `.navin/HEARTBEAT.md` (and `navin/templates/HEARTBEAT.md`) says: « The Leads desk loop (Studio Start loop) hunts on its own saved schedule. That is not this heartbeat. »

## Allowed actions

On a heartbeat turn, only these actions pass:

`status`, `snapshot`, `watch`, `follow`, `rescore`, `score`

Refused (400): `start`, `stop`, `schedule`, `tick`, `hunt`, `enrich`, `sequence`, `lookalike`, `keys`, `crm`, `outreach`.

Tools already denied on heartbeat: `cron`, `web_search`, `scrape`.

## Watch policy

| Condition | Result |
| --- | --- |
| Live hunt (`hunt_is_live`) | immediate skip `loop_hunting` |
| Recent watch (`max(loop.last_watch, profile.last_watch)` < 90 s) | skip `loop_just_watched` |
| Profile not armed (`wizard_ready` + ICP) | `None` (no check) |
| Watch OK | digest; if `count` is 0 the turn replies `HEARTBEAT_OK` |
| Watch fails | `skipped_reason=watch_error`, no `last_watch` stamp |

Heartbeat watch deadline: **20 s** (`HEARTBEAT_WATCH_S`). Never `wait_s=8` on the lock. 90 s grace avoids a double digest from loop + heartbeat.

## What the LLM may do

1. Call `leads action=watch` again (idempotent).
2. If the prompt already has a digest: report the count, tier A accounts, signals, due follow-ups.
3. If `watch.count` is 0 and there is no digest: `HEARTBEAT_OK`.

Never: hunt, start, stop, schedule, tick, scrape LinkedIn, send a sequence, spend Apollo / Hunter / Pappers credits.

See also: [Start loop](./loop.md), [Desk](./desk.md).
