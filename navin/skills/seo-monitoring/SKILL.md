---
name: seo-monitoring
description: Track rankings, traffic signals, technical errors, and competitor changes over time, with scheduled checks and alerts. Use to maintain SEO performance after fixes.
metadata: {"navin":{"emoji":"📡","category":"seo"}}
---

# SEO Monitoring

## Overview

SEO decays silently. Schedule recurring checks and only alert on meaningful change.

## What to monitor

| Signal | Method | Frequency |
|--------|--------|-----------|
| Ranking spot-checks | `web_search` target keywords, note position of domain | weekly |
| Indexation | `site:domain` count trend | weekly |
| Technical health | fetch robots.txt / sitemap / key pages, status codes | weekly |
| Competitor moves | fetch competitor blog/sitemap, diff new pages | weekly |
| Backlink mentions | search new `"domain"` mentions | monthly |
| GSC data | prefer live MCP `search-console` when connected; else user CSV export - clicks/impressions/CTR | monthly |

## Workflow

1. Define the watchlist with the user: 10-20 keywords, 3-5 competitors, key pages.
2. Store baseline in a memory file (e.g. `seo/watchlist.md` + `seo/history.md` in the workspace).
3. Create a `cron` job for the recurring check; each run appends dated results.
4. Alert only on: position drops >3 spots, deindexed pages, 4xx/5xx on key pages, new competitor page targeting a watched keyword.
5. Monthly digest: trends, wins, losses, recommended actions.

## Rules

- Prefer MCP `search-console` (Settings → MCP) for live GSC queries/pages when configured; fall back to CSV exports only if the MCP is unavailable.
- Manual SERP checks are approximate (personalization) - report trends, not absolute truth.
- Keep history append-only so trends stay auditable.
- Route content fixes to `on-page-seo-optimizer`, technical ones to `seo-technical-auditor`.
