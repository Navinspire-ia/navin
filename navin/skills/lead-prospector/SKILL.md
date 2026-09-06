---
name: lead-prospector
description: Expert-level prospect hunting - find companies, people, roles, and verified contact context from open web sources. Use for any "find me leads / people / companies" request.
metadata: {"navin":{"emoji":"🎯","category":"sales"}}
---

# Lead Prospector

Operate like a top-tier SDR research desk: turn a target definition into a clean, deduplicated, fully sourced list of companies and decision-makers. Never invent data - every field has a source or is marked unknown/`unverified`.

The live book is Studio `#/leads` and the `leads` tool (same store as Tauri, `navin leads`, `python -m navin.leads.desk_cli`). Call `leads action=status` first and read the Loop line.

Autonomous hunt is the Leads desk loop. Use `leads action=start` / `stop` / `schedule` / `tick`. If Loop is ON, do not hunt again. Do not create a chat cron that hunts or ticks. The gateway hunts SIRENE / open data then watch on that calendar while Navin is up.

One-shot hunt: `leads action=hunt` (SIRENE / Companies House / OpenCorporates / web_search, then Apollo Places Crunchbase). Enrich one row with `leads action=enrich` (Pappers, PDL, Hunter, scrape public /about /contact). `rescore` writes BANT-F evidence, why and the next action. `sequence` starts a j0/j3/j7 cadence. `lookalike` finds peers of a seed company.

Heartbeat is `leads action=watch` only (alerts on 80+, buying signals and due follow-ups). Never hunt, start, schedule, tick or send from heartbeat.

## When to use

- "Find me N companies / people matching …"
- Building a first outbound list from an ICP

## When not to use

- Enrichment of an existing list only (`lead-enrichment` / contact enrich cards)
- Pipeline forecast (`pipeline-analyst`)

## Context bar

ICP: sector, size band, geography, roles, disqualifiers, volume target, product angle.

## Search playbook

### Companies

| Technique | How |
|-----------|-----|
| Directory sweep | `web_search` "{sector} companies {geography}", awards, chambers |
| Registry lookups | Pappers/societe.com (FR), Companies House (UK), OpenCorporates |
| Ecosystem mining | competitor logos/case studies, partner pages, exhibitor lists |
| Tech footprint | careers pages, stack hints in job posts / HTML |
| Lookalike expansion | 3 best clients → peers/competitors |
| Corpus scrape | `scrape` on careers/about/blog hubs (same-domain); Exa/Firecrawl MCP when configured |

### People

| Technique | How |
|-----------|-----|
| Role search | `web_search` "{company} {role}" - public profiles only |
| Team pages | fetch /about, /team, /leadership |
| Press & talks | releases, podcasts, conference bios |
| Authorship | bylines, whitepapers, patents, GitHub orgs |

### Contact context

- Prefer `leads action=enrich` on a row id (BYOK keys live on the desk). Scripts stay as a fallback when `HUNTER_API_KEY` / `APOLLO_API_KEY` exist.
- Infer email patterns from public sources only as fallback; confidence high/medium/low; never label fabricated as verified.
- Phone/switchboard: official contact/legal pages only.
- Record source URL + collection date per datum.

## Output format

Save `sales/prospects-<date>.csv` with columns:

`company, website, size, sector, country, signal, person, role, profile_url, contact_hint, source, confidence, icp_score`

Then: top 5 fits + why + opening angle each. Run:

```bash
python navin/skills/lead-qualification/scripts/score_leads.py sales/prospects-<date>.csv --validate-only
```

## Rules

- Public sources only; no login-walled scraping.
- Dedupe on domain; quality beats volume (ask target N if unclear).
- Mark unverified fields `unverified`; never pad with guesses.
- Apply `data-quality-agent` before CRM export.
- After quality, `leads action=crm` on the row id, or the `crm` tool. Keep a CSV only when the user asks.

## Anti-patterns

- Invented emails presented as verified
- LinkedIn logged-in scraping
- 200-row dumps with empty sources
- A chat cron that hunts or ticks (use `leads action=start` / `stop` / `schedule`)
- Hunt from heartbeat (watch only)
