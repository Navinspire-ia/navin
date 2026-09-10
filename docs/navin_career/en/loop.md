# Career desk loop and heartbeat

Same store as Studio `#/career`, Tauri, `navin career`, `python -m navin.career.desk_cli`, and the `career` tool.

## Start the loop

```text
navin career start --kind daily --hour 9
navin career stop
navin career schedule --kind weekdays --hour 8 --minute 30
```

In Studio: Start / Pause / Tick. Tick with an empty body forces one hunt and **keeps the loop paused**.

`start` without `--run-now` only arms the calendar. The gateway hunts (`collect` then `watch`) on that schedule while it is up.

## Heartbeat

Silent unless useful. The gateway already ran `career action=watch`. If `watch.count` is 0, stop.

Allowed on heartbeat: `status`, `dossier`, `snapshot`, `read`, `book`, `watch`.

Never search, collect, start, schedule, tick, prepare or apply from heartbeat. Never scrape LinkedIn. Never write a CV pack.

## What the hunt does

1. Collect official and open sources (public job APIs and RSS such as Remotive, Jobicy, Remote OK, Himalayas, We Work Remotely, Arbeitnow and Hacker News Who is hiring, ATS JSON incl. Workable, JSON-LD JobPosting, Free-Work public listings, country portals, open web).
2. Watch the local book: Perfect/Good matches and J3/J7 follow-ups.
3. Alert channels you switched on. The same alert is marked so it does not fire twice.

A hung collect is cut at 8 minutes. Heartbeat watch is cut at 20 seconds so the LLM turn is not blocked.

## Guardrails

- Do not create a chat cron that searches or ticks.
- LinkedIn is official open + paste. No scrape. No Easy Apply bot.
- Stop during a hunt always wins when the hunt finishes.
