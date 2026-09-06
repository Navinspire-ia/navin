# Leads & Sales module - Overview

The **Leads** module (sidebar → **Leads**, route `#/leads`) is a **senior SDR desk**: public hunting with search/scrape tools, buying signals, ICP/BANT-F scoring, prepared outreach (human send), CRM export. Every datum is sourced or `unverified`. Verified emails and live CRM sync depend on connectors.

## Connectors for real leads

| Need | Config |
| --- | --- |
| Verified emails | `HUNTER_API_KEY` and/or `APOLLO_API_KEY`, then `enrich_leads.py` |
| Deep web research | **Exa** and/or **Firecrawl** MCP (Settings → MCP) |
| Live CRM | **HubSpot** MCP and/or **Salesforce** MCP (`crm-update-agent` preloaded) |
| Site corpora | `scrape` / `browser` tools; `/scrape` allowed from the Leads module |

Without Hunter/Apollo keys, the agent still builds sourced lists with `unverified` email patterns - never fake "verified" addresses.

## How it works

1. Open **Leads** in the sidebar (`#/leads`). Same store as Tauri, `navin leads`, and the `leads` tool.
2. Set the ICP on the start screen, then **Start loop** (daily / weekdays / weekend / week / month + hour). Pause or change the hours anytime. The gateway hunts then watches on that calendar while Navin is up.
3. Heartbeat only alerts (tier A, buying signals, due follow-ups). It never hunts and never sends a sequence.
4. One-shot hunt, enrich, sequence and outreach stay on the desk or in chat. Do not create a chat cron that hunts or ticks.

```
/leads find 30 HR-tech SaaS companies in France, 50-200 employees, with their heads of people
/leads enrich sales/prospects-*.csv then push tier A into HubSpot
```

## The `/leads` command

| | |
| --- | --- |
| Command | `/leads [icp\|company\|brief]` |
| Skills preloaded | expert contract, critic, DQ, prospector, signals, generation, qualification, account/entity research, outreach, persona, pipeline, **lead-enrichment**, **crm-update-agent**, **deep-web-research**, **web-extractor** |
| Board | Tracked run (`project-board`) |
| Output | `sales/prospects-*.csv` (+ enriched/scored) + `leads-report-*.html` + expert gate |

## Scripts

```bash
python navin/skills/lead-enrichment/scripts/enrich_leads.py --keys-check
python navin/skills/lead-enrichment/scripts/enrich_leads.py sales/prospects.csv -o sales/prospects-enriched.csv --verify-existing
python navin/skills/lead-qualification/scripts/score_leads.py sales/prospects-enriched.csv
```

## Data rules

- Every datum: source URL or `unverified`.
- `email_status=verified` only from API proof (Hunter/Apollo).
- Public sources only - no logged-in LinkedIn.
- Domain dedupe; quality over volume.

## Tips

- Clear ICP + 5 best customers → better lookalikes.
- Enable Exa/Firecrawl + Hunter before high-volume hunts.
- Chain: ICP → hunt → enrich → score → CRM.

## Desk reference

| Page | Contents |
| --- | --- |
| [Desk](./desk.md) | One store, waterfall, BANT-F 80/55 |
| [Start loop](./loop.md) | Start / stop / schedule, 20 s supervisor |
| [Heartbeat](./heartbeat.md) | Watch only, 90 s grace, 20 s deadline |
| [Desktop (Tauri)](./desktop.md) | Linux, Windows, macOS, `tsc` build |
| [CLI and API](./cli-api.md) | `navin leads`, `/api/leads`, agent tool |
| [Actions](./actions.md) | 17 Studio cards |
| [Skills](./skills.md) | Skills + HubSpot / Salesforce MCP |
