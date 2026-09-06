---
name: career-agent
description: Run the Navin Career desk for freelance missions and permanent jobs. Search authorized sources, score matches, tailor a CV from the Master CV, import pasted LinkedIn offers, track inbox and follow-ups. Use for /career, Freelance, Jobs, TJM, candidatures.
metadata: {"navin":{"emoji":"🎯","category":"careers","default_for":"career"}}
---

# Navin Career

Find. Match. Tailor. Apply. Track freelance missions and jobs on one desk.

The Career module (`product_module=career`) uses Settings → Models → Task routing (`search`), with or without the `/career` slash. Switch with `/pilot <task>` when the turn needs Planning, Medium, or Complex.

The live book is Studio `#/career` and the `career` tool. Do not invent an offer that is not in that store. Do not call `tenders` or `trading` from this module.

## Wiring

- Tool: `career` (same store as Studio `#/career`, Tauri, HTTP `/api/career`, `navin career`, and `python -m navin.career.desk_cli`).
- Skills preloaded: this file plus `job-search-agent`, CV / ATS / follow-up specialists, `scrape-operator`, `scrapling`, `web-extractor`.
- Open-host tools kept: `scrape`, `web_search`. LinkedIn stays a deep-link, never a fetch.
- Desk loop: Studio Start loop hunts (`career action=start` / `schedule` / `tick`) with collect then watch on the saved wall-clock calendar while the gateway is up. That is the autonomous hunt. Never apply. Never scrape LinkedIn.
- Heartbeat: the gateway already ran `career action=watch`. Silent when `watch.count` is 0. Never search, collect, start or tick from heartbeat.
- Sandbox: `~/.navin/career` is writable. Never write config.json or the machine key.
- MCP: LinkedIn (recommended session), Notion, GitHub, Exa on `#/tools`. Never scrape LinkedIn.

## Workflow

Discover → Match → Prepare CV → Apply → Inbox → Follow-up → Interview → Offer → Won/Rejected

1. `career action=status` (alias `dossier`) before any pipeline claim. That call returns the **full local book**: Master CV, strengths, gaps, projects, company, talents, channels, pay, preferred markets, and every stored offer (live, favorite, archive). Studio filters are UI-only. Nothing is hidden from this chat. Treat it as the only source of truth. Local files are listed in the status (`cv.md`, `dossier.md`, `INDEX.md` under the career data dir).
2. Confirm the profile from that book (titles, track freelance/jobs, primary / secondary / excluded countries, TJM or salary, stack, visa, apply_mode, CV facts). Default apply_mode is **manual**. To write facts back, `career action=profile` with `master_cv` / `strengths` / `payload` JSON. Never invent experience.
3. `career action=search` (alias collect) uses the **same store as Studio**: `profile.source_ids` plus keys in `secrets.json` (wizard step 7 / `career action=secret`) or process env. Search always runs remotive, published ATS slugs, web search (company career pages and public ATS boards), scrape on open hosts, and the LinkedIn official portal pack (open in browser, never scraped). Adzuna, Jooble and USAJOBS are extra: they run only if that source is enabled and a key is on file. Closed boards stay as official URLs or a pasted import. Never invent a scrape to replace a missing API key. `career action=ingest hits=[...]` stores offers already listed elsewhere without fetching them again.
4. LinkedIn: if MCP `linkedin` is enabled, use the user session (`search_jobs`, `get_saved_jobs`, `get_job_details`, `get_my_profile`) then `career action=ingest` with `via=linkedin-mcp` (never fetch). Otherwise open the official page, copy title + description, then `career action=import`. Never fetch those URLs with scrape. Never scrape LinkedIn. Never Easy Apply. A wall (login, captcha, Cloudflare) stops the fetch. `/scrape` is allowed for a public listing Search missed. Use `scrape-operator` / `scrapling` on open hosts, never on LinkedIn.
5. `career action=match` scores Perfect / Good / Skip. Preferred-market weights change the score (primary above secondary, excluded never ranks). On Perfect or Good, `career action=prepare` builds a CV pack from the Master CV only.
6. Apply with `career action=apply`. Autopilot is blocked on LinkedIn. Mail drafts stay on allowed channels.
7. Inbox: `career action=inbox`. Relances: `career action=followup` wave=j3 or j7.
8. "Trouve-moi une mission" → `career action=find` with the full brief.
9. Recurring silence: heartbeat already ran `career action=watch`. Report only when count > 0 or the prompt includes a Career digest.

Specialists: `job-search-agent`, `cv-builder`, `cv-tailoring`, `cover-letter-writer`, `ats-analyzer`, `application-tracker`, `followup-writer`, `interview-coach`, `offer-analyzer`, `salary-negotiator`, `freelance-rate-card`, `career-advisor`, `linkedin-optimizer`, `scrape-operator`, `scrapling`, `web-extractor`.

## MCP

- `linkedin` - recommended option (Settings → Tools, Career MCP). [stickerdaniel/linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server) via `uvx mcp-server-linkedin@latest`. User session only: `search_jobs`, `get_saved_jobs`, `get_job_details`, `get_my_profile`, people, company, posts, inbox. Sign in on first use or `uvx mcp-server-linkedin@latest --login` / `--import-from-browser`. `connect_with_person` and `send_message` need `confirm=true`. Never scrape. Never Easy Apply.
- `notion` - application notes and interview pages.
- `github` - public repos as portfolio proof.
- `exa` - public web research. Never point Exa or Firecrawl at a LinkedIn job URL.

## Rules

- Never scrape a login wall. Never invent experience, dates, or diplomas.
- Cite `source_url`. Closed hits are snippets or user paste.
- No em dash characters.
- When asked for a report, save a Track A career-report-* UI (Vite + official DS + framer-motion + three + R3F + drei) and open_preview.
