# Trading desk loop and heartbeat

Same store as Studio `#/trading`, Tauri, `navin trading`, `python -m navin.trading.desk_cli`, and the `trading` tool.

## Start the loop

```text
navin trading start --kind daily --hour 9
navin trading stop
navin trading schedule --kind weekdays --hour 8 --minute 30
```

In Studio: Start / Pause / Tick. Tick with an empty body forces one cycle and **keeps the loop paused**.

`start` without `--run-now` only arms the calendar. The gateway cycles on that schedule while it is up.

## Heartbeat

Silent unless useful. The gateway already ran `trading action=watch`. If `watch.count` is 0, stop.

Allowed on heartbeat: `status`, `snapshot`, `journal`, `watch`.

Never start, stop, schedule or tick from heartbeat. Paper only. Never invent a fill.

## What the cycle does

1. Scan public quotes (Yahoo, Binance, CoinGecko, listed REITs).
2. Screen, debate, apply deterministic risk, place **paper** orders if allowed.
3. Journal. Watch pending approvals. Alert channels you switched on.

A hung cycle is cut at 8 minutes. Heartbeat watch is cut at 20 seconds. After an error the loop retries in minutes, not only on the next daily slot.

## Guardrails

- Do not create a chat cron that ticks.
- Stop during a cycle always wins when the cycle finishes.
- Heartbeat snapshot does not fetch quotes (that would be a mini-scan).
