# SEO module - Overview

The **SEO** module (sidebar → **SEO**, route `#/seo`) is a **senior SEO desk**: grounded audits (live fetch), intent-mapped keywords, publish-ready content, schema, competitor gaps, links/local/GEO - under the expert contract. Without a data API (DataForSEO/Semrush), live Search Console (MCP), or a GSC export, volumes/KD stay qualitative (`n/a - requires API data`) and are never invented.

## Connect Search Console

For live queries, pages, and index signals, connect the **Google Search Console** MCP preset:

1. Open **Settings → MCP**.
2. Install the **Google Search Console** (`search-console`) preset.
3. Provide **either** an OAuth Desktop `client_secrets.json` path (`GSC_OAUTH_CLIENT_SECRETS_FILE`) **or** a service-account JSON path (`GSC_CREDENTIALS_PATH`, with `GSC_SKIP_OAUTH=true`).
4. Requires `uvx` (Astral uv) on the machine that runs the gateway. Destructive sitemap tools stay off by default.

SEO skills prefer this MCP over CSV exports when it is connected. Cron/loop jobs reuse the same gateway env.

## Evidence engine

`/seo` uses the built-in `seo` tool before interpretation. Its actions are `crawl`, `audit`, `schema`, `psi`, `crux`, `serp_snapshot`, `serp_history`, `score`, `report`, and `pipeline`. Crawls are bounded and reuse the secure Scraping transport. PSI v5 and CrUX v1 return an explicit `data_gap` without optional API credentials. SERP snapshots require DataForSEO or Semrush, include source and confidence, and are stored atomically as append-only history. No position, volume, or performance metric is inferred.

## How it works

1. Open **SEO** in the sidebar.
2. Click an action card in one of the three groups - **Audit**, **Research**, **Optimize** (see [Actions](./actions.md)).
3. The chat opens with `/seo` and the card prompt already in the composer. Add the site or topic in chat, then send.
4. The agent delivers actionable output ordered by impact, saved as files when substantial.

Direct usage in an SEO module chat:

```
/seo https://example.com - full technical audit
/seo keyword research for artisan bakery in Lyon
```

## The `/seo` command

| | |
| --- | --- |
| Command | `/seo [url\|topic]` |
| Lifecycle | Agent workflow (runs a full agent turn) |
| Skills preloaded | `studio-expert-contract`, `critic-reviewer`, `seo-technical-auditor`, `keyword-research`, `on-page-seo-optimizer`, `seo-content-writer`, `backlink-strategy`, `competitor-seo-analysis`, `local-seo`, `geo-ai-search-optimizer`, `seo-data-provider` |
| Board | Tracked run (live plan via `project-board`) |
| Output | Files under `seo/` + `seo-report-*.html` + expert gate |

## Coverage

| Area | What the agent does |
| --- | --- |
| Technical | Bounded crawl evidence for status, redirects, robots, sitemaps, canonicals, hreflang, meta, headings, structured data, internal links, PSI and CrUX |
| Keywords | Seed expansion, intent classification, qualitative prioritization, plus provider metrics only when their source is available |
| Content | Briefs and full articles: title, H-structure, entities, internal links, FAQ, schema.org markup |
| Competitors | Content strategy, site structure, targeted keywords, gap analysis with opportunities |
| Links | Internal mesh plans and backlink acquisition with outreach templates |
| Local | Business profile, citations, reviews, localized pages |
| AI search (GEO) | Optimizing for AI-generated answers and answer engines |

## Continuous monitoring

Combine with Navin's autonomy features:

- `/goal monitor example.com rankings weekly and alert me on drops` - a sustained goal the agent keeps pursuing.
- Cron jobs (via the `cron` skill) for scheduled audits and reports.
- The `seo-monitoring` and `website-monitor` skills for recurring checks.

## Tips

- Always give the URL when auditing; the agent fetches real pages.
- Chain: technical audit → fix list → `/forge` (in Dev) to apply the fixes to your codebase.
- Ask for the deliverable format you need: table, CSV file, or full article in markdown/HTML.
