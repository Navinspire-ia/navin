---
name: job-search-agent
description: Search, filter, and rank freelance and job opportunities on authorized sources with the career tool. Use to run a structured search or a watch.
metadata: {"navin":{"emoji":"🔎","category":"careers","default_for":"career"}}
---

# Job Search Agent

The live index is the Career desk (`#/career`) and the `career` tool. Do not invent a posting.

## Search setup

1. Criteria live in the Career profile: titles, track, primary / secondary / excluded countries, stack, min_rate / min_salary, work_mode.
2. Sources that index live follow the user setup (`career action=status` shows `Source ids` and `Official APIs`). Search always launches web search (company career pages + public ATS boards) + scrape on open hosts + the LinkedIn official pack. Remotive and published ATS slugs run with those live families. Keyed APIs (Adzuna, Jooble, USAJOBS) are extra: only when that id is in source_ids and a key is on file (wizard secrets or env). Closed boards stay official-open or paste. Use `scrape-operator` / `scrapling` for a public listing Search missed. Never invent a scrape because a key is missing. Never point scrape or Scrapling at LinkedIn or another closed board.
3. Sources that stay official-open without MCP: LinkedIn tabs, Bayt, Welcome to the Jungle, Apec, Indeed, Malt. User opens the tab, copies, `career action=import`.
4. Recommended LinkedIn option when enabled: MCP `linkedin` (user session). Call `search_jobs` / `get_saved_jobs` / `get_job_details`, then `career action=ingest` or `import`. Never scrape the URL. Never Easy Apply.

## Workflow

1. `career action=status` then confirm the full profile from the local book (titles, countries, stack, pay, CV facts).
2. `career action=search` (or find with a brief). Deduplicate is already in the store.
3. Present Perfect then Good. Skip list pages unless the user asks.
4. For a closed URL the user cares about: do **not** `web_fetch`. Ask them to paste title + description, then import.
5. Selected offers: `ats-analyzer` → `career action=prepare` → `cover-letter-writer`.
6. Autonomous hunt is the Career desk loop (Studio Start loop, Tauri `#/career`, `navin career start`, `python -m navin.career.desk_cli start`). Use `career action=start` / `stop` / `schedule` / `tick`. Do not create a chat cron that searches. Never apply. Never scrape LinkedIn.
7. Heartbeat (silent unless useful): the gateway already ran `career action=watch`. If `watch.count` is 0, stop. Never search, collect, start or tick from heartbeat. Log with `application-tracker`.

## Rules

- Never scrape LinkedIn. Never Easy Apply. Never fetch a closed job URL.
- Freshness: flag postings older than 30 days when the date is known.
- Scam signals: no company, pay-to-apply, generic gmail only.
- Human submits. Preparation is allowed.
