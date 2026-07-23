---
name: keyword-research
description: Find keywords, estimate difficulty and intent, and map opportunities to pages. Use when planning content, launching a site, or expanding into new topics or languages.
metadata: {"navin":{"emoji":"🔑","category":"seo"}}
---

# Keyword Research

## Overview

Build a keyword map grounded in search intent, not just volume. Without paid API access, rely on SERP analysis, autocomplete patterns, and competitor pages.

## Intent types

| Intent | Signal | Page type |
|--------|--------|-----------|
| Informational | "how", "what", "guide" | Blog / docs |
| Commercial | "best", "vs", "review", "pricing" | Comparison / landing |
| Transactional | "buy", "demo", "quote", brand+product | Product / contact |
| Navigational | brand names | Home / feature pages |

## Workflow

1. Seed list: products, services, pains, jargon from the user (FR + EN + AR when relevant).
2. Expand via `web_search`: autocomplete-style variations, "People also ask" themes, related searches.
3. For each candidate keyword, check the SERP: who ranks (giants vs niche), what format (listicle, landing, video).
4. Estimate difficulty qualitatively: domains ranking, content depth needed.
5. Cluster by topic; assign one primary keyword + secondaries per target page.
6. Deliver the keyword map.

## Keyword map format

```markdown
| Cluster | Primary keyword | Intent | Difficulty | Target page | Notes |
|---------|-----------------|--------|-----------|-------------|-------|
```

## Rules

- Never invent volume numbers; say "estimate" or "requires Ahrefs/Semrush data".
- One primary keyword per page — no cannibalization.
- Pair with `seo-content-writer` for execution and `competitor-seo-analysis` for gaps.
