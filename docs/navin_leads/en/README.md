# Leads & Sales module — Overview

The **Leads** module (sidebar → **Leads**, route `#/leads`) is an elite B2B prospecting and sales desk. The agent hunts companies and decision-makers from open web sources, detects buying signals, qualifies and enriches every lead, builds the outreach, and formats everything for your CRM — with a source URL on every datum.

## How it works

1. Open **Leads** in the sidebar.
2. (Optional) Type a **brief** at the top: your ICP, product, territory, target volume. It is attached to every action.
3. Pick an action card in one of the four groups — **Find**, **Qualify**, **Outreach**, **Pipeline** (see [Actions](./actions.md)).
4. The chat opens and `/leads` is sent automatically with the action's specification and your brief.
5. The agent researches, builds the deliverable (CSV or table saved to the workspace), and ends with the best-fit leads and suggested next moves.

Direct usage in any chat:

```
/leads find 30 HR-tech SaaS companies in France, 50-200 employees, with their heads of people
/leads scan buying signals on the attached account list
```

## The `/leads` command

| | |
| --- | --- |
| Command | `/leads [icp\|company\|brief]` |
| Lifecycle | Agent workflow (runs a full agent turn) |
| Skills preloaded | `lead-prospector`, `buying-signals`, `lead-generation`, `lead-qualification`, `account-research`, `entity-research`, `outreach-sequencer`, `cold-email-writer`, `customer-persona-builder`, `pipeline-analyst` |
| Output | Sourced lead lists (CSV/markdown), signal dashboards, outreach sequences, account sheets — saved in the workspace |

## What the agent can search

| Target | Sources used |
| --- | --- |
| Companies | Directories, official registries (OpenCorporates, Pappers, Companies House…), award lists, competitor ecosystems, event exhibitor lists |
| People | Public profiles, team/leadership pages, press quotes, conference bios, article bylines, patents, GitHub orgs |
| Jobs | Careers pages and job boards — postings reveal stack, projects, and pains verbatim |
| Signals | Funding news, hiring sprees, leadership changes, expansions, tech changes, regulation deadlines |
| Contact context | Published emails and patterns, official switchboards, social profiles — with confidence levels |

## Data rules (what makes it trustworthy)

- **Every datum is sourced**: URL + collection date per field.
- **Nothing invented**: unverified fields are marked `unverified`; email patterns carry a confidence level, never presented as verified addresses.
- **Public sources only**: no login-walled scraping, robots and terms respected.
- **Deduplicated**: same domain = same company; quality beats volume.

## Continuous prospecting

Combine with Navin's autonomy features:

- `/goal watch for new funding rounds in French fintech and build a lead sheet weekly` — a sustained goal.
- Cron jobs for scheduled signal scans and pipeline reviews.
- `crm-update-agent` skill to push results into HubSpot/Salesforce when configured.

## Tips

- Feed it your best customers: "here are our 5 best clients, find 50 lookalikes".
- Chain the groups in one chat: ICP → company search → people search → signals → sequence.
- Ask for the CRM export last — it consolidates everything collected in the session.
