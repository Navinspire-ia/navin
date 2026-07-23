# SEO module — Overview

The **SEO** module (sidebar → **SEO**, route `#/seo`) is a complete SEO agency: technical audits, keyword research with intent mapping, ready-to-publish optimized content, schema markup, competitor gap analysis, and link strategy. When you give it a URL, the agent fetches the actual pages — findings are grounded in the real site, not guesses.

## How it works

1. Open **SEO** in the sidebar.
2. (Optional) Type a **brief** at the top: site URL, business, target market, language. It is attached to every action.
3. Pick an action card in one of the three groups — **Audit**, **Research**, **Optimize** (see [Actions](./actions.md)).
4. The chat opens and `/seo` is sent automatically with the action's specification and your brief.
5. The agent delivers actionable output ordered by impact, saved as files when substantial.

Direct usage in any chat:

```
/seo https://example.com — full technical audit
/seo keyword research for artisan bakery in Lyon
```

## The `/seo` command

| | |
| --- | --- |
| Command | `/seo [url\|topic]` |
| Lifecycle | Agent workflow (runs a full agent turn) |
| Skills preloaded | `seo-technical-auditor`, `keyword-research`, `on-page-seo-optimizer`, `seo-content-writer`, `backlink-strategy`, `competitor-seo-analysis`, `local-seo`, `geo-ai-search-optimizer` |
| Output | Prioritized findings, keyword tables, optimized articles, JSON-LD markup — files in the workspace |

## Coverage

| Area | What the agent does |
| --- | --- |
| Technical | Indexability (robots, canonicals, sitemaps), meta and heading structure, structured data, internal linking, performance signals |
| Keywords | Seed expansion, intent classification (informational/commercial/transactional), difficulty and value estimates, clustering, prioritization |
| Content | Briefs and full articles: title, H-structure, entities, internal links, FAQ, schema.org markup |
| Competitors | Content strategy, site structure, targeted keywords, gap analysis with opportunities |
| Links | Internal mesh plans and backlink acquisition with outreach templates |
| Local | Business profile, citations, reviews, localized pages |
| AI search (GEO) | Optimizing for AI-generated answers and answer engines |

## Continuous monitoring

Combine with Navin's autonomy features:

- `/goal monitor example.com rankings weekly and alert me on drops` — a sustained goal the agent keeps pursuing.
- Cron jobs (via the `cron` skill) for scheduled audits and reports.
- The `seo-monitoring` and `website-monitor` skills for recurring checks.

## Tips

- Always give the URL when auditing; the agent fetches real pages.
- Chain: technical audit → fix list → `/forge` (in Dev) to apply the fixes to your codebase.
- Ask for the deliverable format you need: table, CSV file, or full article in markdown/HTML.
