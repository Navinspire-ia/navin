# Navin Trading - Overview

The **Trading** module (sidebar → **Trading**, route `#/trading`) is a **paper Trading Agent OS**: mandate, public-API quotes, specialist debate, deterministic risk, journal, backtest. Tagline: *Paper. Real quotes. No invented fill.*

The live book is the Studio desk and the `trading` tool (same store as Tauri on Linux / Windows / macOS, `navin trading`, and `python -m navin.trading.desk_cli`). Do not invent a position or a broker fill that is not in that store.

## How it works

1. Open **Trading** in the sidebar (`#/trading`).
2. Set the mandate (domains, countries, risk, target, paper capital).
3. Start the **desk loop** (calendar). The gateway scans, debates, applies risk, journals, then watches pending paper approvals.
4. Approve or reject paper orders on the desk. Alerts go to Telegram, WhatsApp or email when channels are on.

```
/trading
trading action=status
trading action=start
trading action=watch
```

## The `/trading` command

| | |
| --- | --- |
| Command | `/trading` |
| Tool | `trading` (`status`, `start`, `stop`, `schedule`, `tick`, `watch`, `strategy`, `mandate`, …) |
| Skills | `trading-agent` and domain skills (equities, crypto, NFT, REITs) |
| Output | Paper book in `~/.navin/trading` + journal + alerts |

## Desk loop vs heartbeat

| Clock | Work |
| --- | --- |
| Desk loop | Scan / debate / risk / journal on the saved schedule |
| Heartbeat | `watch` only (pending paper approvals). No quote fetch. |

Do not create a chat cron that ticks. Details: [Trading loop](./loop.md) · [shared contract](../../studio/desk-loop.md).

## Guardrails

- V1 is paper / journal / backtest only.
- Never invent a broker fill. If risk blocks, stop.
- Heartbeat never starts, stops, schedules or ticks.
- Heartbeat snapshot is local (no live quotes).

## Related

- Product: `/trading` on navin.live
- Desktop (Tauri): [desktop.md](./desktop.md)
- Compare: `/compare/tradingview`, `/compare/etoro`
- Career uses the same two-clock contract: [Career loop](../navin_career/en/loop.md)
