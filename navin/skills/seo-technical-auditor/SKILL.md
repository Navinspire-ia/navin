---
name: seo-technical-auditor
description: Audit technical SEO - indexation, sitemap, robots.txt, Core Web Vitals hints, redirects, canonical tags, and crawl errors. Use when a site underperforms in search or before a relaunch.
metadata: {"navin":{"emoji":"🔧","category":"seo"}}
---

# SEO Technical Auditor

Find and prioritize technical issues that block ranking. Evidence first, checklist second. Operate like a senior technical SEO: grounded fetches, impact × effort triage, no invented CWV scores.

## When to use

- Full site technical audit before relaunch or after traffic drops
- Indexation / crawlability suspicion
- Pre-migration checklist

## When not to use

- Pure content strategy with no URL (use `keyword-research` / `seo-content-writer`)
- Paid ranking reports without API access (use MCP `search-console` if connected, else `seo-data-provider` or ask for GSC/PSI exports)

## Audit checklist

| Area | What to check | How |
|------|---------------|-----|
| Indexation | noindex, canonical conflicts, `site:` coverage | fetch pages + web_search `site:domain` |
| robots.txt | blocked paths, sitemap line, crawl-delay | `web_fetch https://domain/robots.txt` |
| Sitemap | valid XML, fresh URLs, sample 404s | fetch `/sitemap.xml` (+ index children) |
| Redirects | chains, http→https, www, trailing slash | fetch variants; note status chain |
| Meta / head | title, description, robots, canonical, hreflang | sample 5-15 money pages |
| Structured data | JSON-LD presence/type | parse `<script type="application/ld+json">` |
| Internal links | orphans, deep money pages | sample nav + footer + in-content |
| Performance | LCP/CLS/INP hints only | ask for PSI/CrUX URL or note "requires PSI" |
| HTTPS | mixed content signals | fetch http upgrade + page assets notes |
| Hreflang | reciprocal FR/EN/AR | head tags on language variants |

## Workflow

1. Run `seo` action `pipeline` first for a full audit, or compose `crawl`, `audit`, `schema`, `psi`, `crux`, `score`, and `report`.
2. Treat engine findings and their evidence objects as the source of truth.
3. Use `serp_snapshot` only with DataForSEO or Semrush credentials and retain source/confidence.
4. Preserve every `data_gap`; do not replace missing PSI, CrUX, ranking, position, or volume data with estimates.
5. Add expert interpretation and prioritize findings as Critical / Important / Nice-to-have.
6. The canonical health score comes from `seo` action `score`. For legacy imported findings only, the helper remains available:

```bash
python navin/skills/seo-technical-auditor/scripts/audit_score.py findings.json
```

Or from workspace: `python <skill_dir>/scripts/audit_score.py seo/audit-findings.json`

7. Save report under `seo/technical-audit-<domain>-<date>.md` and feed `seo-report-*.html`.

## Finding severity

| Severity | Meaning | Examples |
|----------|---------|----------|
| Critical | Blocks crawl/index or money page | sitewide noindex, sitemap 404, https broken |
| Important | Material ranking/UX drag | canonical loops, missing titles, thin money pages |
| Nice to have | Polish | missing FAQ schema, minor meta length |

## Report format

```markdown
## Technical SEO Audit - <domain>
Date / scope / pages sampled

### Critical (blocks ranking)
1. issue - URL - evidence - fix - effort (S/M/L)

### Important
...

### Nice to have
...

### Quick wins (this week)
- ...

### Data gaps
- Core Web Vitals: requires PageSpeed Insights / CrUX
- Index coverage: prefer MCP `search-console` when connected; else GSC export if site: is inconclusive
```

## Rules

- Prefer live GSC via MCP `search-console` (Settings → MCP) for index coverage, queries, and page performance when available; otherwise ask for a CSV export or mark the gap.
- Every finding needs a URL + observed evidence (status, snippet, header).
- Never invent Lighthouse, CrUX, volume, or position data; use the engine's explicit `data_gap`.
- Prioritize by impact × effort; do not dump 100 undifferentiated issues.
- Sample-based audit is honest: state sample size, not "full crawl of N pages" unless you crawled them.
- Pair with `on-page-seo-optimizer` for page-level rewrites and `seo-monitoring` for tracking.

## Anti-patterns

- Auditing from memory without fetching the live site
- Claiming "Core Web Vitals fail" without data
- Mixing content strategy opinions into technical Critical
