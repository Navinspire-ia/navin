---
name: tender-agent
description: Find, qualify, answer and win public tenders. Official portals, company profile, semantic match, 0-100 score, Go/No-Go, dossier writer, pipeline to Won/Lost. Use for Appels d'offres, RFP, RFQ, DAO, TED, SAM.gov, Etimad, UNGM.
metadata: {"navin":{"emoji":"🏛️","category":"sales","default_for":"tenders"}}
---

# Navin Tenders

Find. Qualify. Answer. Win public tenders with AI.

The Tenders module (`product_module=tenders`) uses Settings -> Models -> Task routing (`deep`), with or without the `/tenders` slash. Switch with `/pilot <task>` when the turn needs Planning, Docs, or Search.

The live book is local (`~/.navin/tenders/`) and the `tenders` tool. Do not invent a notice that is not in that store.

On disk, always up to date: `profile.json`, `tenders.json`, `index.json`, `INDEX.md`, `book.md`, `dossier.md`, `notices.md`, `files/`. `tenders action=status` returns the full book. That is the only fact source.

## Wiring

- Tool: `tenders` (same store as Studio `#/tenders`, Tauri, HTTP `/api/tenders`, `navin tenders`, and `python -m navin.tenders.desk_cli`).
- Skills preloaded: this file plus `tender-monitor`, `rfp-writer`, proposal / contract specialists, `scrape-operator`, `scrapling`, `web-extractor`, `archify`.
- Desk loop: Studio Start loop hunts (`tenders action=start` / `schedule` / `tick`) with collect then watch on the saved wall-clock calendar while the gateway is up. That is the autonomous hunt. Never send a buyer mail. Do not create a chat cron that collects or ticks.
- Heartbeat: the gateway already ran `tenders action=follow`. Silent when `watch.count` is 0. Never collect, write, start, schedule or tick from heartbeat.
- Start loop needs the full company wizard. Heartbeat follow runs as soon as a company name is on file (deadlines / GO).
- Sandbox: `~/.navin/tenders` is writable. Never write config.json or the machine key.
- MCP: LinkedIn (recommended buyer research), Exa on `#/tools`. Never invent a notice from LinkedIn.

## Workflow

Find. Decide. Send. You keep the money.

1. `tenders action=status` before any claim about the pipeline. It lists every notice, the company dossier, knowledge and file paths.
2. Filter with `tenders action=search query=...` (optional `country`, `stage`, `go`). Open one notice with `tenders action=get id=tn-...`. Confirm the company profile (countries, crafts, min budget, min deadline, min score, send_mode, Telegram / WhatsApp / email / Teams). Default send_mode is **approval** for public AO.
3. `tenders action=collect` pulls official APIs (TED, World Bank, BOAMP, UK OCDS, CanadaBuys) then runs `web_search` + `scrape` on official public hosts for portals without an API. Do not invent a notice. Do not bypass a login / captcha / Cloudflare wall. If Collect skipped a wall, ask the user to open the public URL with the `browser` tool, or run `/scrape` / `scrape` on that listing.
4. `tenders action=qualify id=...` scores 0-100 and returns GO / NO-GO with effort days.
5. On GO, `tenders action=write id=...` then adapt with `rfp-writer` and facts from `tenders action=knowledge` (methodology, team, price_book). `write` produces a full bid (cover, contents, company, need, approach, vision, functional, technical, method, KPIs, RACI, governance, planning, budget, references, matrix) in the language of the notice. Never claim a certification or reference that is not in the profile.
6. Advance stages with `tenders action=stage`. Mail drafts: `action=mail kind=clarification|ack|relance`. Follow-up: `action=follow`. CRM: `action=crm-sync`. Wizard setup: `upload`, `custom-source`, `secret`, `notify`, `discover-accept`.
7. Recurring hunt: `tenders action=start` / `stop` / `schedule` / `tick` (same store as Studio, Tauri, `navin tenders`). The gateway supervisor collects then watches on that calendar. Do not create a chat cron that collects or ticks.
8. Heartbeat: `tenders action=follow` only. Silent when `watch.count` is 0. Never collect, write, mail, start, schedule or tick from heartbeat. Never send a buyer mail from the loop or from heartbeat. Do not create a chat cron that collects or ticks.
9. After Won/Lost, update the journal: which countries, sectors, amounts and consortia actually convert.
10. Keep `scrape`, `web_search`, `browser`, `cron` and `open_preview`. Career and trading desks stay denied. Load `scrapling` with `scrape-operator` when a public listing needs custom fetch code. Exa MCP is optional public research on official hosts. LinkedIn MCP (`linkedin`, Tools > Tenders MCP, [stickerdaniel/linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server)) is the recommended buyer-research option after the user enables it and signs in (`uvx mcp-server-linkedin@latest --login` or `--import-from-browser`). Official tools: `get_company_profile`, `get_company_posts`, `search_companies`, `get_company_employees`, `search_people`, `get_person_profile`, `get_my_profile`, `get_sidebar_profiles`, `get_feed`, `search_posts`, `get_inbox`, `get_conversation`, `search_conversations`, `close_session`. Job tools (`search_jobs`, `get_saved_jobs`, `get_job_details`) are hiring context only, never a notice. Never invent a notice from a LinkedIn post. `connect_with_person` and `send_message` need `confirm=true` after an explicit user confirmation. No paid aggregator.

## Sources (open, no paid aggregator)

Official API / Open Data first, then national portals, then ministries/SOEs, then UN/World Bank/AfDB/EBRD, then targeted web search per country (EN + local language). See the Sources pane for TED, PLACE, BOAMP, Find a Tender, SAM.gov, CanadaBuys, Etimad, UAE, Monaqasat, HAICOP, Maroc, UNGM, World Bank, AfDB.

## Rules

- Never scrape a login wall. Prefer the public notice URL.
- Never auto-send a bid in autonomous mode unless the user set send_mode=autonomous **and** confirmed. Default is approval.
- Cite `source_url`. No em dash characters.
- When asked for a report, save a Track A tenders-report-* UI (Vite + official DS + framer-motion + three + R3F + drei) and open_preview.
