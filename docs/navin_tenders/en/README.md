# Tenders module - Overview

The **Tenders** module (sidebar → **Tenders**, route `#/tenders`) is a **public-procurement desk**: official collect, 0-100 score, Go/No-Go, dossier draft, pipeline to Won/Lost.

The live book is the local store (`~/.navin/tenders/`). Studio, Tauri (Linux / Windows / macOS), `navin tenders`, `python -m navin.tenders.desk_cli` and the `tenders` tool share that **same** store. Do not invent a notice that is not in it.

## How it works

1. Open **Tenders** (`#/tenders`). Finish the company wizard (name, country, currency, specialty, target countries, crafts, sources).
2. File references, Word/PPT models and reuse slides (see [Write quality](./write.md)).
3. **Start loop** (daily / weekdays / weekend / month + hour) or a one-shot Collect. The loop hunts then watches on that calendar while the gateway is up. It never sends a buyer mail.
4. Qualify. On GO, `write` delivers a ready dossier (text + Word/PPT + matrix). You study it, remark if needed, then approve. Submission is on the **buyer portal**, not inside Navin.
5. Heartbeat is follow / watch only. Silent when nothing is new. Never collect, write, start or send.

```
/tenders
tenders action=status
tenders action=write id=tn-...
```

## Loop and heartbeat (Career contract)

| Piece | Role |
| --- | --- |
| One loop | Gateway supervisor `navin-tenders-loop` (every 20 s). No chat cron `tenders-loop`. |
| Stop always wins | `action=start` / `stop` / `schedule`. Pause is written at once (intent) and applied when the cycle ends. |
| Silent heartbeat | `tick_watch` before the LLM turn. Digest injected only when `count > 0`. |

The loop self-heals: stale hunt, corrupt JSON, retry after error, timeout if a portal hangs. See `navin/tenders/loop.py` and `navin/tenders/heartbeat.py`.

Do **not** create a chat cron that collects or ticks.

## The `/tenders` command

| | |
| --- | --- |
| Command | `/tenders` |
| Tool | `tenders` (same store as Studio) |
| Skills | `tender-agent`, `rfp-writer`, `tender-monitor`, proposal / contract, scrape, `archify` |
| Output | Notices in the store + drafts + journal |

## Sources (open, no paid aggregator)

Fixed order: official API → open data → structured HTML → `web_search` + `scrape` on official hosts. A login / captcha / Cloudflare wall is not bypassed.

SAM.gov: API v2 with `SAM_API_KEY` / `SAM_GOV_API_KEY`. Without a key, no invented US federal notices.

## Rules

- Cite `source_url`. Never invent a title or notice.
- Never post a public bid unless `send_mode=autonomous` **and** confirmed. Default is **approval**.
- `write` never invents a figure, client or certificate that is not on file.
- No Unicode em dash (U+2014 / U+2013) in outputs.

## Next

- [Write quality](./write.md) - references, slide models, examples
- [Actions](./actions.md)
- [Skills](./skills.md)
- [Desktop (Tauri)](./desktop.md) - Linux, Windows, macOS
