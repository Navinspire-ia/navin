# Desk loop (Career, Trading, Tenders, Marketing)

Career (`#/career`), Trading (`#/trading`), Tenders (`#/tenders`) and Marketing (`#/marketing`) share one autonomy contract: **two clocks**, **one store**, **never a third chat cron**.

## Two clocks

| Clock | Who runs it | Work | Never |
| --- | --- | --- | --- |
| **Desk loop** | Gateway supervisor (~20 s): `navin-career-loop`, `navin-trading-loop`, `navin-tenders-loop`, `navin-marketing-loop` | Recurring hunt or cycle on the saved wall-clock calendar while the gateway is up | Invent an apply, a broker fill, a buyer email, or a published post |
| **Heartbeat** | Gateway tick before the LLM turn | Silent `watch` on the local book | start, stop, schedule, tick, search, collect, write, send, pipeline, publish |

Start / stop / schedule live on the desk, Tauri, `navin career` / `navin trading` / `navin tenders` / `navin marketing`, and the agent tool. Same JSON store.

Do not ask the agent to create a chat cron that searches or ticks. That is a third clock and it fights the desk.

## Stop always wins

Pause written while a hunt or cycle is running is kept. The cycle applies that intent when it finishes. A force tick while paused does one pass and **stays paused**.

## Heartbeat stays quiet

The gateway already ran `career action=watch`, `trading action=watch`, `tenders action=follow`, or `marketing action=watch`. If `watch.count` is 0, nothing to report.

Career watch: strong matches and due follow-ups. Never scrape LinkedIn. Never apply.

Trading watch: pending paper approvals. Paper only. Never invent a fill. Heartbeat snapshot does not fetch live quotes.

Tenders watch: silent follow on the local book. Never write or send.

Marketing watch: new winners and competitor fingerprint changes. Never publish. Never spend ad budget. Never start the growth loop.

## Self-heal

A crashed hunt or cycle does not leave the desk stuck on `hunt` / `scan` / `measure`. The next peek or heartbeat recovers it. A hung collect, quote fetch or growth cycle cannot freeze the gateway (wall-clock deadline). A failed cycle retries in minutes, not only on the next daily slot.

CLI `navin career stop` / `navin trading stop` / `navin tenders stop` / `navin marketing stop` does not block on a terminal TTY.

## Docs

- [Career](../navin_career/en/README.md) · [Career loop](../navin_career/en/loop.md)
- [Trading](../navin_trading/en/README.md) · [Trading loop](../navin_trading/en/loop.md)
- [Marketing](../navin_marketing/en/README.md) · [Growth loop](../navin_marketing/en/loop.md) · [Heartbeat](../navin_marketing/en/heartbeat.md)
- [Tenders](../navin_tenders/en/README.md)
- [Automations / Heartbeat](../automations.md)
