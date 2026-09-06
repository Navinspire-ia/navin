# Scraping actions - 12 cards

Each card sends `/scrape` with a precise specification; your brief is appended.

## Collect

| Action | Delivers |
| --- | --- |
| Scrape a page | Clean text/markdown from one or more URLs via `scrape action=fetch`. Saves JSON + Markdown under `scrape/`, with a short quality summary. |
| Crawl a site | Same-domain BFS via `scrape action=pipeline` with depth/page caps. Exports XLSX + HTML report; flags empty/JS-gated pages for a browser pass. |
| From sitemap / list | Parallel fetch of a URL list or sitemap seeds. Deduplicated CSV + JSONL with ok/error counts. |
| JS-rendered page | Uses the `browser` tool for client-rendered pages, normalizes into scrape records, then `scrape action=export`. |

## Clean & enrich

| Action | Delivers |
| --- | --- |
| Clean a corpus | Strip nav/chrome leftovers, normalize whitespace, drop near-duplicates; rewrite cleaned files with a removal note. |
| Enrich metadata | Add description/OG fields, language guess, word count, `fetched_at`; enriched JSON/CSV + field dictionary. |
| Extract tables | HTML tables → structured CSV/Excel rows with source URL and normalized column names. |
| Prepare for RAG | One Markdown file per page with YAML frontmatter (`url`, `title`, `fetched_at`) plus `manifest.json`. |

## Export

| Action | Delivers |
| --- | --- |
| Export Excel | Workbook (`xlsx`) with url, title, status, text, error; optional summary sheet. |
| Export CSV + JSON | Machine-friendly dumps with matching row counts and a one-line schema note. |
| Export XML | Well-formed `pages/page` XML for legacy ingest. |
| HTML report | Readable report (`format=report`) plus executive summary: pages, success rate, paths, next crawl. |
