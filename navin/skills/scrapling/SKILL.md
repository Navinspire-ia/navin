---
name: scrapling
description: Write scrapers with the official Scrapling 0.4.14 API (Fetcher, StealthyFetcher, DynamicFetcher, sessions, Spider templates, adaptive CSS). Default framework in the Scraping module. Fall back to the scrape tool or browser only when Scrapling is the wrong tool.
metadata: {"navin":{"emoji":"🕷️","category":"navigation"}}
---

# Scrapling 0.4.14

Official adaptive scraping framework (D4Vinci). Pin **`scrapling==0.4.14`**. Use only this API. Do not invent methods or mix BeautifulSoup / raw Playwright unless the user named that stack.

Docs: https://scrapling.readthedocs.io/en/latest/

In the **Scraping** module this is the default, the same way Code uses the Dev stack and Leads uses prospecting scripts.

## Choose a layer

| Need | Use |
|---|---|
| Static HTML, TLS impersonation | `Fetcher` / `FetcherSession` |
| Cloudflare Turnstile / stealth | `StealthyFetcher` / `StealthySession` (`solve_cloudflare=True`) |
| Full JS browser (Playwright Chromium / Chrome) | `DynamicFetcher` / `DynamicSession` |
| Multi-page crawl, pause/resume, export | `Spider` (or a template below) |
| Parse HTML you already have | `Selector` from `scrapling.parser` |
| One-shot corpus, no new Python | Navin `scrape` tool (Rust / httpx) |
| Forms, multi-tab, upload, human walls in Navin UI | Navin `browser` tool |

Read [references/api.md](references/api.md) for sessions, spiders, templates, CLI, and MCP.

## Install (required before fetchers/spiders)

`pip install scrapling` is **parser only**. `from scrapling.fetchers` or `from scrapling.spiders` fails without extras.

```bash
pip install "scrapling[fetchers]==0.4.14"
scrapling install
```

Repo extra: `pip install -e ".[scraping]"` then `scrapling install`.

- MCP: `pip install "scrapling[ai]==0.4.14"`
- Shell / `scrapling extract`: `pip install "scrapling[shell]==0.4.14"`
- Everything: `pip install "scrapling[all]==0.4.14"` then `scrapling install`

If import fails, install via `exec`, then retry. Do not silently switch stack.

## Canonical fetch + adaptive select

```python
from scrapling.fetchers import Fetcher, AsyncFetcher, StealthyFetcher, DynamicFetcher

StealthyFetcher.adaptive = True
page = StealthyFetcher.fetch(
    "https://example.com",
    headless=True,
    network_idle=True,
)
products = page.css(".product", auto_save=True)
# After a redesign:
products = page.css(".product", adaptive=True)
```

HTTP with session + Chrome TLS:

```python
from scrapling.fetchers import Fetcher, FetcherSession

with FetcherSession(impersonate="chrome") as session:
    page = session.get("https://quotes.toscrape.com/", stealthy_headers=True)
    quotes = page.css(".quote .text::text").getall()

page = Fetcher.get("https://quotes.toscrape.com/")
```

Stealth / Cloudflare:

```python
from scrapling.fetchers import StealthyFetcher, StealthySession

with StealthySession(headless=True, solve_cloudflare=True) as session:
    page = session.fetch("https://nopecha.com/demo/cloudflare", google_search=False)
    data = page.css("#padded_content a").getall()

page = StealthyFetcher.fetch("https://nopecha.com/demo/cloudflare")
```

Full browser:

```python
from scrapling.fetchers import DynamicFetcher, DynamicSession

with DynamicSession(headless=True, disable_resources=False, network_idle=True) as session:
    page = session.fetch("https://quotes.toscrape.com/", load_dom=False)
    data = page.xpath('//span[@class="text"]/text()').getall()

page = DynamicFetcher.fetch("https://quotes.toscrape.com/")
```

## Canonical spider

```python
from scrapling.spiders import Spider, Request, Response

class QuotesSpider(Spider):
    name = "quotes"
    start_urls = ["https://quotes.toscrape.com/"]
    concurrent_requests = 10

    async def parse(self, response: Response):
        for quote in response.css(".quote"):
            yield {
                "text": quote.css(".text::text").get(),
                "author": quote.css(".author::text").get(),
                "url": response.url,
            }
        next_page = response.css(".next a")
        if next_page:
            yield response.follow(next_page[0].attrib["href"])

result = QuotesSpider().start()
result.items.to_json("scrape/quotes.json")
```

Pause / resume: `QuotesSpider(crawldir="./scrape/crawl_data").start()`. Ctrl+C saves; same `crawldir` resumes.

Ready-made templates (prefer these over a blank spider when they fit): `CrawlSpider`, `SitemapSpider`, `XMLFeedSpider`, `CSVFeedSpider`, `ShopifySpider`.

```python
from scrapling.spiders import ShopifySpider

class MyStore(ShopifySpider):
    target_website = "example.com"

result = MyStore().start()
```

## Parse without fetching

```python
from scrapling.parser import Selector

page = Selector("<html>...</html>")
quotes = page.css(".quote")
quotes = page.xpath('//div[@class="quote"]')
quotes = page.find_all("div", class_="quote")
quotes = page.find_by_text("quote", tag="div")
```

Selection: CSS, XPath, `find_all`, `find_by_text`, chained `.css()`, `::text` / `::attr(href)` like Scrapy/Parsel. Navigation: `.parent`, `.next_sibling`, `.find_similar()`, `.below_elements()`. Adaptive: `auto_save=True` then later `adaptive=True`.

## Delivery in Navin

- Write scripts under `scrape/build/`. Pin `scrapling==0.4.14` in any requirements file.
- Deliver the exported dataset and the report, not the spider. Hand over scraper code only when the user asked for it.
- Export with `result.items.to_json()` / `to_jsonl()` / `to_csv()` / `to_xml()`, or Navin `scrape` `export`.
- Keep a source URL on every row. Never dump corpora into chat.
- `robots_txt_obey` on spiders when the user cares about compliance.
- Captcha / paywall / login that still blocks after StealthyFetcher: pause and use the Navin `browser` tool with the user. Do not sell bypass as a guarantee.

## Fallback (do not skip)

1. Scrapling 0.4.14 for code the agent writes or runs.
2. Navin `scrape` tool for a ready-made fetch/crawl/export (Rust, else httpx).
3. Navin `browser` for interactive UI, empty JS shells after Scrapling, or assisted walls.
