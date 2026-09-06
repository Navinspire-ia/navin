---
name: keyword-research
description: Find keywords, estimate difficulty and intent, and map opportunities to pages. Use when planning content, launching a site, or expanding into new topics or languages.
metadata: {"navin":{"emoji":"🔑","category":"seo"}}
---

# Keyword Research

Build a keyword map grounded in search intent. Without paid API access, rely on SERP analysis, autocomplete patterns, and competitor pages. With `seo-data-provider` env keys, pull real volume/KD.

## When to use

- New site / new market content plan
- Expanding clusters or languages (FR/EN/AR)
- Prioritizing which pages to create or refresh

## When not to use

- Single-page title tweak (use `on-page-seo-optimizer`)
- Fabricating a "volume report" with no API or export

## Intent types

| Intent | Signal | Page type |
|--------|--------|-----------|
| Informational | how, what, guide | Blog / docs |
| Commercial | best, vs, review, pricing | Comparison / landing |
| Transactional | buy, demo, quote, brand+product | Product / contact |
| Navigational | brand names | Home / feature pages |

## Workflow

1. Seed list: products, services, pains, jargon from the user (FR + EN + AR when relevant).
2. If MCP `search-console` is connected, pull live queries/pages (impressions, CTR, position) for the property before estimating; treat GSC as ground truth over SERP guesses.
3. If `seo-data-provider` is available, pull volume/KD/related; else expand via `web_search` (variants, PAA themes, related searches).
4. For each candidate, inspect the SERP: who ranks (giants vs niche), format (listicle, landing, video).
5. Estimate difficulty **qualitatively** without API: Low / Medium / High from domain strength + content depth needed. Never invent numeric KD or monthly volume.
6. Cluster by topic; assign one primary keyword + secondaries per target page.
7. Save `seo/keyword-map-<topic>-<date>.md` (and CSV if useful).

## Keyword map format

```markdown
| Cluster | Primary keyword | Intent | Difficulty | Volume | Target page | Priority | Notes |
|---------|-----------------|--------|------------|--------|-------------|----------|-------|
```

- `Volume`: integer from API/export, or `n/a (requires API data)`
- `Difficulty`: Low/Medium/High (qualitative) or API KD if sourced
- `Priority`: P1/P2/P3 by business value × winnability

## Rules

- Prefer MCP `search-console` live data over CSV or estimates when the preset is connected (Settings → MCP).
- Never invent volume numbers; say estimate or requires Ahrefs/Semrush/DataForSEO/GSC.
- One primary keyword per page - no cannibalization.
- Prefer money-intent clusters when the goal is leads/sales.
- Pair with `seo-content-writer` for execution and `competitor-seo-analysis` for gaps.

## Anti-patterns

- 500-keyword dumps with no clusters or page owners
- Copying competitor keyword lists without SERP checks
- Treating brand navigational queries as content opportunities
