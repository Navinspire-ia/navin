---
name: programmatic-seo
description: Generate hundreds of structured pages from data (locations, integrations, glossaries, comparisons) with unique value per page. Use when a dataset can answer many long-tail queries.
metadata: {"navin":{"emoji":"🏭","category":"seo"}}
---

# Programmatic SEO

## Overview

Data × template = long-tail coverage. Only works when each generated page has real unique value - thin duplicates get penalized.

## Good pSEO patterns

- `{tool} vs {tool}` comparisons
- `{service} in {city}` local pages (with genuinely local data)
- `{term}` glossary with rich definitions
- `{integration} + {product}` pages
- `{template/example}` galleries

## Workflow

1. Validate the pattern: search 5 sample queries - is there long-tail demand and weak competition?
2. Build the dataset (CSV/JSON): one row per page with enough fields for uniqueness (≥40% unique content per page).
3. Design the template: title formula, H1, intro variables, data tables, FAQ, internal links to hub pages.
4. Generate with a script (`exec` + Python/Jinja or the site's SSG) - never hand-write 300 pages.
5. Ship in batches (50-100), monitor indexation before scaling.
6. Add hub/index pages linking to all generated pages.

## Anti-patterns

- Same paragraph with one swapped word across pages
- Pages with no data behind them ("we serve {city}" with nothing local)
- Publishing 10,000 pages at once on a fresh domain

## Rules

- Every page must answer its query better than a generic page would.
- Keep the dataset as the source of truth; regenerate, don't hand-edit.
