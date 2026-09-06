# Scraping skills

## Preloaded by `/scrape`

| Skill | Purpose |
| --- | --- |
| `scrapling` | Default framework (Scrapling 0.4.14) when the agent writes scraper code. |
| `scrape-operator` | Orchestrates jobs: Scrapling first, then `scrape` (Rust/httpx), then `browser`. **Built for this module.** |
| `web-extractor` | Firecrawl-style readable extraction into Markdown/JSON for RAG or migrations. |
| `data-quality-agent` | Deduping, schema consistency, missing-field checks on exported datasets. |
| `playwright-browser` | Drive Chromium when static HTML is an empty shell. |
| `deep-web-research` | Multi-source research that often seeds a scrape list. |
| `report-generator` | Executive summaries next to the raw exports. |
| `spreadsheet-analyst` | Pivot, filter and sanity-check CSV/XLSX outputs. |

## Complementary skills

| Skill | Purpose |
| --- | --- |
| `prompt-injection-defender` | Treat untrusted page content as data, not instructions. |
| `rag-knowledge-builder` | Index a cleaned scrape corpus into a knowledge base. |
| `fact-checker` | Verify claims extracted from pages before reuse. |
| `competitor-intelligence` | Structured competitor monitoring that can feed crawl seeds. |
| `seo-technical-auditor` | Crawlability / structured-data follow-ups after a site scrape. |

Skills load automatically with `/scrape`; invoke any of them explicitly for a focused task ("use data-quality-agent on scrape/out.csv").
