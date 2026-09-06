---
name: crm-update-agent
description: Keep the CRM clean - log activities, update deal stages, maintain contact data in HubSpot, Salesforce, or a workspace CRM. Use after every sales interaction.
metadata: {"navin":{"emoji":"🗃️","category":"sales"}}
---

# CRM Update Agent

## Overview

A CRM is only as good as its hygiene. Log everything close to real time, keep stages honest, kill zombie deals.

## Supported backends

| Backend | Access |
|---------|--------|
| Navin Studio CRM (default) | `crm` tool - same SQLite as `#/crm` |
| HubSpot | API via `exec` + `curl` (needs `HUBSPOT_ACCESS_TOKEN`) when the user asks |
| Salesforce | MCP preset `salesforce` (`SF_ACCESS_TOKEN` + `SF_INSTANCE_URL`, read-only by default) |
| Workspace files (legacy) | `sales/crm/{companies,contacts,deals}/` if the user still uses that tree |

Prefer the shared `crm` tool for Navin Studio. When an MCP server for HubSpot or Salesforce is configured (Settings → MCP), use it only if the user wants that backend. Official Salesforce Hosted MCP is OAuth PKCE and is not a paste-URL flow in Navin v1; the `salesforce` preset uses a token + instance URL.

## What gets logged

- **Activities**: calls, emails, meetings - date, participants, summary, next step
- **Deal updates**: stage, amount, close date, probability - with the reason for any change
- **Contact/company data**: roles, emails, phones - corrected the moment a change is learned

## Stage honesty rules

- A deal advances only on buyer action (meeting held, proposal requested, verbal yes) - not on our optimism
- No next step + no activity 30 days → flag for `pipeline-analyst`
- Close dates are the buyer's dates, not the quarter's end

## Workflow

1. After each interaction (from `discovery-call-assistant`, `meeting-followup`, outreach logs): extract the structured update.
2. Write to the backend; on API errors, fall back to workspace files and note the sync gap.
3. Weekly hygiene pass (`cron`): deals without next steps, stale contacts, duplicate entries → fix list.

## Rules

- Never delete CRM records - mark inactive/lost with reason.
- Secrets via environment/`secrets-manager`; never hardcode tokens.
- Summaries in the CRM are factual - verbatim quotes marked as quotes.
