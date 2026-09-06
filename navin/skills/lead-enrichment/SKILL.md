---
name: lead-enrichment
description: >
  Enrich leads with email/domain data via Hunter or Apollo when API keys are
  present. Prefer scripts/enrich_leads.py. Never mark emails verified without
  API or public proof. Fall back to pattern inference when keys are missing.
metadata: {"navin":{"emoji":"✉️","category":"sales"}}
---

# Lead Enrichment

Enrich company/person rows with contact context. Prefer APIs when configured; otherwise infer patterns from public pages and keep confidence honest.

## When to use

- Contact enrich cards / post-prospecting enrichment
- Preparing CRM export with email patterns

## When not to use

- Inventing verified inboxes
- Login-walled LinkedIn scrapes

## Credentials (never print secrets)

| Provider | Env | Notes |
|----------|-----|-------|
| Hunter | `HUNTER_API_KEY` | domain search / email finder / email verifier |
| Apollo | `APOLLO_API_KEY` | people/org enrichment |
| HubSpot | `HUBSPOT_ACCESS_TOKEN` | CRM push via HubSpot MCP / crm-update-agent |

Check keys without leaking values:

```bash
python navin/skills/lead-enrichment/scripts/enrich_leads.py --keys-check /dev/null 2>/dev/null || \
  python -c "import os; print('HUNTER', 'set' if os.getenv('HUNTER_API_KEY') else 'missing'); print('APOLLO', 'set' if os.getenv('APOLLO_API_KEY') else 'missing')"
```

## Preferred path (script)

After prospecting wrote `sales/prospects-*.csv`:

```bash
python navin/skills/lead-enrichment/scripts/enrich_leads.py sales/prospects-YYYYMMDD.csv \
  -o sales/prospects-YYYYMMDD-enriched.csv --verify-existing
python navin/skills/lead-qualification/scripts/score_leads.py sales/prospects-YYYYMMDD-enriched.csv
```

Columns written: `email`, `email_status` (`verified`|`unverified`|`catch_all`|`invalid`), `email_score`, `enrichment_source`.

## Manual Hunter / Apollo (fallback)

```bash
curl -s "https://api.hunter.io/v2/email-finder?domain=example.com&first_name=Ada&last_name=Lovelace&api_key=$HUNTER_API_KEY"
curl -s "https://api.hunter.io/v2/email-verifier?email=ada@example.com&api_key=$HUNTER_API_KEY"
curl -s -X POST "https://api.apollo.io/api/v1/mixed_people/search" \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: $APOLLO_API_KEY" \
  -d '{"q_organization_domains":"example.com","page":1,"per_page":5}'
```

Only label `verified` when the API status/score says so.

## Public research stack (no LinkedIn login)

Use together with prospecting:

1. `web_search` + `web_fetch` for directories, registries, team pages.
2. `scrape` for same-domain careers/about corpora.
3. Exa / Firecrawl MCP (Settings → MCP) for deeper public crawl/search.
4. HubSpot MCP for CRM push after enrichment.

## Fallback (no keys)

1. Fetch company contact/legal/press pages.
2. Infer pattern (`first.last@`, `flast@`) with confidence medium/low.
3. Leave `contact_hint` + `email_status=unverified`; never invent a full mailbox as verified.

## Workflow

1. Detect keys (`--keys-check`).
2. Enrich CSV with `enrich_leads.py`.
3. Validate + score with `score_leads.py`.
4. Apply `data-quality-agent` before CRM push (`crm-update-agent` / HubSpot MCP).

## Rules

- Never print API keys.
- Public-source or API evidence only.
- Opt-out / suppression lists honored when the user provides them.
- Prefer HubSpot MCP / `crm-update-agent` for CRM writes after enrichment.

## Anti-patterns

- "john.doe@company.com (verified)" with no API/public proof
- Enriching personal emails scraped from private profiles
- Skipping `--keys-check` then claiming verified contacts
