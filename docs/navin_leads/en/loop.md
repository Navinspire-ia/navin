# Leads Start loop

The Studio **Start loop** hunts then watches on a wall-clock schedule. It is **not** the heartbeat, and it is **not** a chat cron.

The gateway runs a `navin-leads-loop` supervisor (every 20 s) that calls `maybe_tick`. While Navin is up, the loop honors the saved calendar. There is no `leads-loop` cron job.

## Start / stop / schedule

| Action | Effect |
| --- | --- |
| `start` | Arms the loop. **Without `run_now`, it does not hunt.** |
| `stop` (`pause`) | `enabled=false`. The supervisor sleeps. Stop always wins, even during a hunt. |
| `schedule` | Changes `kind` / hour / timezone. Persists during a hunt via `loop.intent.json`. |
| `tick` | One cycle now (`force` to ignore pause). |

Schedule `kind` values:

- `daily` - every day
- `weekdays` - Monday-Friday
- `weekend` - Saturday-Sunday
- `weekly` - one weekday (`weekday` 1-7)
- `monthly` - one day of the month (`day` 1-28 or `last`)

Fields: `--hour` 0-23, `--minute` 0-59, `--tz` IANA.

Studio UI: **Start loop**, **Pause**, schedule, **Run cycle**. The card shows phase, cycle, next due (`formatNextDue`), `last_result`, stats. The UI polls every 8 s when the loop is on, in hunt phase, or busy.

## Hunt contract

1. The supervisor calls `maybe_tick`. If a hunt is already live: `busy`, no wait.
2. Hunt is capped at **8 minutes** (`MAX_HUNT_S`). A stale hunt is recovered and the lock is released.
3. After the hunt: watch (signals, tier A, due follow-ups). Watch inside the hunt: `run_watch(..., already_locked=True)`.
4. Watch error: `skipped_reason=watch_error`, `last_watch` is not stamped (heartbeat can still alert).
5. Never send a sequence from the loop. Never scrape LinkedIn.

Typical phases: `armed`, `hunt`, `watch`, `paused`, `busy`.

## Agent and seeds

If the loop is ON, the agent must not hunt again. `status` / `snapshot` expose `loop` and `loop_brief`. The tool forwards `schedule`, `run_now`, `tz`, `force`.

Chat seeds and the `/leads` prompt: « Start the Leads desk loop ». **Do not** create a chat cron that hunts or ticks.

See also: [Heartbeat](./heartbeat.md), [Desk](./desk.md), [CLI and API](./cli-api.md).
