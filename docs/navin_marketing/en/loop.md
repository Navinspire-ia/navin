# Marketing growth loop

Studio `#/marketing` runs an autonomous cycle: **measure → learn → improve**. Heartbeat is not this loop. A chat cron is not this loop.

While the gateway is up, the supervisor `navin-marketing-loop` calls `maybe_tick` about every 20 seconds. A due tick runs one cycle. An early tick returns `sleep`. A paused desk returns `paused`. An empty brand returns `unarmed`.

## Operator controls

| Action | Effect |
| --- | --- |
| `start` | Arms the loop. Optional `schedule` JSON (`kind`, `hour`, `minute`, `weekday`, `day`, `tz`). `run_now=true` runs one cycle immediately. |
| `stop` | Pause. Survives a cycle that is already running. |
| `schedule` | Change hours. Does not enable a paused loop. |
| `tick` | Run now if due, or if `force=true`. API default for tick is `force=true`. |

Pause always wins. Start / stop / schedule written during a cycle go to `loop.intent.json`. The cycle applies that intent when it finishes. The UI reads `peek_loop`, so the screen shows the intent you just wrote.

## Schedule kinds

Same wall-clock kinds as Career / Trading / Tenders:

- `daily`
- `weekdays`
- `weekend`
- `weekly` (needs `weekday` 1-7, Monday = 1)
- `monthly` (day 1-28 or last day)

Timezone is IANA (`Europe/Paris`, `UTC`, ...). Next due is computed after each successful cycle.

## Isolation (no blocking)

| Guard | Behavior |
| --- | --- |
| `desk.lock` | One cycle at a time. `wait_s=0`: overlap returns `busy` immediately. |
| Supervisor inflight | Gateway does not stack two `maybe_tick` calls. |
| Watch lock | Heartbeat watch skips with `skipped=busy` if a cycle holds the lock. |

## Continuity and self-repair

| Event | Repair |
| --- | --- |
| Process crash mid `measure` / `learn` / `busy` | `heal_loop_state` after 120s, or `recover_stale_cycle` when no live PID |
| Corrupt `next_due` / `last_watch` / `cycle` | Coerced to numbers and persisted |
| Cycle exception | `error_streak` + `retry_due_after` backoff (2 min, 5 min, 15 min, 30 min), capped by the next slot |
| Cycle longer than 8 minutes | `call_with_deadline` fails the cycle, lock released, next due advanced |
| Operator Pause during a cycle | Intent kept, applied at the end, phase `paused` |

Live cycles store `cycle_pid` and `cycle_started_at`. `peek_loop` runs `recover_stale_cycle` first.

## Cycle body

1. Phase `measure`. Silent `run_watch` (already holding the lock).
2. Phase `learn`. `run_growth_cycle`: scoreboard, mark winners, clone variants on the winning hook.
3. Phase `idle` (or `paused` if Stop won). Journal line: winners, variants, alerts, next slot.
4. `error_streak` reset on success.

A force tick on a paused desk still stays **paused** after the cycle. It does not secretly enable the loop.

## Store files

Under `~/.navin/marketing/` (or the instance runtime dir):

| File | Role |
| --- | --- |
| `loop.json` | enabled, phase, schedule, next_due, last_tick, last_watch, cycle, last_result |
| `loop.intent.json` | Start / stop / schedule written while the lock is held |
| `desk.lock` | File lock |
| `brand.json`, `product.json`, `campaigns.json`, `content.json`, `creatives.json`, `analytics.json`, `competitors.json`, `journal.json` | The book |
| `harvest.json`, `seo.json` | Live-site snapshot and SEO keywords / measured rankings |
| `assets/`, `launch/` | Produced / harvested files and launch Markdown |

## CLI and supervisor

```
navin marketing
python -m navin.marketing.desk_cli start
python -m navin.marketing.desk_cli tick
python -m navin.marketing.desk_cli stop
```

Gateway task name: `navin-marketing-loop`. Failures are logged and isolated. They never skip Career, Trading, Tenders or Leads.

See also [Heartbeat](./heartbeat.md).
