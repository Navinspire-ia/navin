# Leads desk - one store

The Leads desk is **local-first**. Every surface reads and writes the same directory: `~/.navin/leads/` (`get_runtime_subdir("leads")`).

| Surface | Entry |
| --- | --- |
| Studio | `#/leads`, `#/leads?pane=book`, `#/leads?lead=` |
| Tauri (Linux, Windows, macOS) | WebView to the gateway, same hash |
| Terminal | `navin leads …` |
| Vite / older gateway fallback | `python -m navin.leads.desk_cli …` |
| HTTP | `GET/POST /api/leads?action=` |
| Agent | `leads` tool (`action=start\|stop\|schedule\|…`) |

Desk chat is hidden by default (same as other GTM desks). Work goes through the API and the loop, not a chat cron.

## Waterfall: open data first

Fixed order:

1. **Open data / registries** - SIRENE (FR), Companies House, and public catalogs.
2. **Public web** - search and company pages. Never scrape LinkedIn, never hit a login wall.
3. **BYOK** (Hunter, Apollo, Pappers, Places, …) **only** for fields that are still empty.

Every datum stays sourced or `unverified`. `email_status=verified` only from a proof API.

## BANT-F scoring (desk)

Desk qualify (`navin/leads/qualify.py`) writes `score`, `tier`, `bant`, `why`, `signals`, `next_action`:

| Tier | Threshold |
| --- | --- |
| A | score >= 80 |
| B | score >= 55 |
| C | otherwise |

BANT-F axes are `fit`, `need`, `timing`, `authority`, `budget`.

CSV `/leads` scripts (`score_leads.py` / `engine.py`) keep 70 / 40 thresholds. That is intentional: the desk and the CSV report are not the same grid.

## Official links

`openOfficialLeadUrl` opens the OS browser (`openInOsBrowser`). Never `window.open`: in the Tauri WebView that is a no-op or a blank page.

Internal hashes (`#/leads`, `http://tauri.localhost/#/leads`, `tauri://localhost/#/leads`) stay in the app. Bare hosts (`acme.com`) are accepted as official URLs.

## Lock

The desk `FileLock` is **not reentrant**. A hunt that holds the lock must not call `peek_loop` (deadlock). Pause / schedule changes during a hunt go through `loop.intent.json` and apply when the hunt finishes.

See also: [Start loop](./loop.md), [Heartbeat](./heartbeat.md), [Desktop](./desktop.md), [CLI and API](./cli-api.md).
