# Marketing heartbeat

Heartbeat for Studio `#/marketing` is a **silent watch**. It is not the growth loop.

The gateway already ran `marketing action=watch` before the LLM turn (`tick_heartbeat_desks`). The agent may call watch again (idempotent). It must never start, schedule, tick, understand, publish, or spend.

## Policy (`HEARTBEAT.md`)

Live file: `.navin/HEARTBEAT.md`. Template: `navin/templates/HEARTBEAT.md`. Both share the **Marketing winners** block.

If `watch.count` is 0 and the prompt has no Marketing digest, the agent replies `HEARTBEAT_OK` and stops.

If count > 0, report only that digest. The note appended to the prompt says:

```
Never publish. Never spend ad budget. Never start the growth loop from heartbeat.
```

## Allowed actions on a heartbeat turn

`status`, `snapshot`, `watch`.

Enforced twice:

1. `handle_marketing_action` (HTTP / CLI / desk)
2. `MarketingTool.execute` (agent tool)

Any other action returns a heartbeat refusal.

## Watch behavior

| Case | Result |
| --- | --- |
| Brand / product not armed | `None` (skip this desk) |
| Loop watched less than 90s ago | `skipped=loop_just_watched`, count 0 |
| Cycle live (lock or PID) | `skipped=busy`, count 0 |
| Watch longer than 20s | `skipped=error`, gateway stays free |
| New winners or competitor fingerprint | Events + digest |
| Same winner already alerted | Not re-emitted (`alerts_sent`) |
| Notify channels all closed | `delivered=false`, `last_watch` not stamped, next heartbeat retries |

Alerts fan out to the WebUI centre and optional `settings.channels` (Telegram, WhatsApp, email). A closed channel never fails watch.

Competitor events fire when the analytics / competitor fingerprint changes, and only for the latest competitor row that was not already marked for that fingerprint.

## Isolation

One desk crash never skips the others. Order in `tick_heartbeat_desks`: Tenders → Career → Leads → **Marketing** → Trading.

Heartbeat does not hold the desk lock longer than one watch pass (`wait_s=0`).

## What stays on the desk

Understand, pipeline, approve, content, creative, metrics, improve, launch, start, stop, schedule, tick.

See [Growth loop](./loop.md).
