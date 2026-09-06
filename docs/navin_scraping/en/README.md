# Scraping module - Overview

The **Scraping** module (sidebar → **Scraping**, right after **Code**, route `#/scraping`) turns open-web URLs into clean, structured datasets. The agent crawls or fetches in parallel, strips chrome, enriches metadata, and exports to CSV, JSON, JSONL, XML, Excel, Markdown or an HTML report - with a source URL on every row.

## How it works

1. Open **Scraping** in the sidebar (after **Code**).
2. Click a card in one of the three groups - **Collect**, **Clean & enrich**, **Export** (see [Actions](./actions.md)).
3. The chat opens with `/scrape` and the card prompt already in the composer (height follows the text). Add seed URLs / constraints in chat, then send.
4. Custom scrapers use **Scrapling 0.4.14**. One-shot jobs use the `scrape` tool (Rust when `navin-core` is built). JS/forms use `browser`. Files go under `scrape/`.

Direct usage in any chat:

```
/scrape crawl https://example.com/docs depth 2, export xlsx + html report
/scrape turn this URL list into a RAG-ready markdown corpus
```

## The `/scrape` command

| | |
| --- | --- |
| Command | `/scrape [url\|site\|brief]` |
| Lifecycle | Agent workflow (runs a full agent turn) |
| Skills preloaded | `scrapling`, `scrape-operator`, `web-extractor`, `data-quality-agent`, `playwright-browser`, `deep-web-research`, `report-generator`, `spreadsheet-analyst` |
| Output | Clean datasets and reports saved in the workspace (`scrape/…`) |

## Architecture (Scrapling first, then Rust / Playwright)

| Layer | Role |
| --- | --- |
| WebUI studio | Cards that fill the `/scrape` chat - easy one-click jobs |
| **Scrapling 0.4.14** | Default Python framework when the agent writes scrapers / spiders |
| Python tool `scrape` | Fallback API: `fetch`, `crawl`, `extract`, `clean`, `export`, `pipeline` |
| `navin-core` (Rust) | Parallel fetch, HTML extract/clean, CSV/JSON/XML/XLSX/report export |
| Python httpx fallback | Same `scrape` API without `make native` (slower) |
| `browser` tool | JS-gated pages, forms, scroll (Playwright / Chromium) |

Install the framework with `pip install "scrapling[fetchers]==0.4.14"` then `scrapling install`, or `pip install -e ".[scraping]"`. Build the Rust accelerator with `make native`.

## What the agent can collect

| Job | Approach |
| --- | --- |
| Single / few pages | `scrape action=fetch` |
| Same-domain site | `scrape action=crawl` or `pipeline` with `max_depth` / `max_pages` |
| URL list / sitemap seeds | Parallel `fetch`, then `export` |
| JS-rendered shells | Escalate to `browser` (`action=content` or network + `response_body`) |
| RAG corpus | One Markdown file per page + `manifest.json` |

## Export formats

| Format | Typical use |
| --- | --- |
| `csv` / `xlsx` | Spreadsheets, CRM / BI import |
| `json` / `jsonl` | Pipelines, APIs, RAG loaders |
| `xml` | Legacy / enterprise ingest |
| `md` | Human-readable corpus |
| `report` | HTML overview (ok / errors / links) |

## Data rules

- **Source every row**: keep the page URL (and status) on each record.
- **Do not invent content**: empty or blocked pages stay empty/error; escalate to browser when needed.
- **No bypass**: captcha / Cloudflare / paywall / login → pause and ask you to solve or sign in via the browser session, then resume.
- **Write files, not chat dumps**: corpora go under `scrape/` in the workspace.
- **Respect compliance**: ask when robots/ToS matter.
- **SSRF-safe**: URLs are validated before fetch (same network guards as `web_fetch`).

## Tips

- Dry-run 1-3 URLs with `fetch` before a large crawl.
- Prefer `pipeline` when you already know the export format.
- Chain Collect → Clean → Export in one chat for a polished deliverable.
- Combine with `/studio` if you need a deck or Word summary of the scrape.

See also the full [scrape tool reference](../scrape-tool.md).
