---
name: scrape-operator
description: Orchestrate any web scraping job to delivery - static, paginated, infinite-scroll, mass crawl, sitemap, JS pages via browser. Export datasets until done.
metadata: {"navin":{"emoji":"🕸️","category":"navigation"}}
---

# Scrape Operator

## Overview

Deliver corpora from any site type into workspace files (csv/json/xlsx/md/report). When writing Python scrapers, use **Scrapling 0.4.14** first (see skill `scrapling`). Use the `scrape` tool (Rust / httpx) for one-shot jobs with no custom code. Use `browser` for JS/scroll/forms. Finish with files + a one-line done summary.

## Routing by page type

| Site type | Approach |
|---|---|
| Custom scraper / spider the agent writes | **Scrapling 0.4.14** (`scrapling` skill) |
| Static docs / blogs, no custom code | `scrape` `crawl` or `pipeline` (Rust when `navin-core` is built) |
| Listing + next/page=N | Scrapling spider, or `scrape` `paginate` then crawl item links |
| Sitemap known | Scrapling or `sitemap` → `fetch` urls in batches → `export` |
| Infinite scroll / lazy JS | `browser` `navigate` → `scroll_infinite` → `extract` / `content` → hand off to export |
| Forms / multi-tab / upload | `browser` (see playwright-browser) |
| empty_shell after fetch | escalate `browser` |
| captcha / CF / paywall / login | **pause** - never bypass |

## Mass crawl checklist

1. `diagnose` a seed URL.
2. Cap with `max_pages` (hard max 500) and optional `allow` / `deny` regex.
3. Resume long jobs with `checkpoint="scrape/checkpoint.json"`.
4. Prefer `pipeline` to write exports directly; keep chat short.
5. Dedup is on by content hash; pagination links do not burn depth budget.

## Cheat sheet

```text
# Custom code: Scrapling 0.4.14 (Fetcher / StealthyFetcher / Spider / ShopifySpider)
# see skill scrapling and scrapling/references/api.md

scrape(action="paginate", url="https://shop.example/list?page=1", max_pages=30)
scrape(action="crawl", url="https://shop.example/list", max_depth=2, max_pages=200, checkpoint="scrape/ckpt.json", deny="/(cart|login|logout)")
scrape(action="sitemap", url="https://example.com/sitemap.xml", max_pages=200)
scrape(action="pipeline", url="https://example.com", format="xlsx", path="scrape/out.xlsx", max_pages=100)
browser(action="scroll_infinite", index=12)   # after navigate - then extract/content
```

## Rules

- Developing scraper code: Scrapling 0.4.14 first. Do not start with BeautifulSoup or raw Playwright.
- One-shot corpora without new code: `scrape` tool (Rust hot path, else httpx).
- Escalate to `browser` for scroll/JS/forms only.
- Respect robots.txt (default on). Never invent rows.
- Never bypass human walls. Write files, not chat dumps.
- Deliver the dataset and the report. Scraper code stays under `scrape/build/` and is handed over only when asked for.
- Qualified people/companies from a scrape can be written into the shared Studio CRM with the `crm` tool after a quality pass.
