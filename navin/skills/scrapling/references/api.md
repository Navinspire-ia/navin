# Scrapling 0.4.14 API notes

Load this when writing sessions, multi-session spiders, CLI, or MCP. Keep `scrapling==0.4.14`.

## Fetchers

| Class | Role |
|---|---|
| `Fetcher` / `AsyncFetcher` | Fast HTTP. TLS impersonation, headers, HTTP/3. |
| `StealthyFetcher` | Stealth + fingerprint. Cloudflare Turnstile / interstitial. |
| `DynamicFetcher` | Full browser (Playwright Chromium or Chrome). |
| `FetcherSession` | Persistent HTTP cookies/state. Sync and async context. |
| `StealthySession` / `AsyncStealthySession` | Persistent stealth browser. |
| `DynamicSession` / `AsyncDynamicSession` | Persistent full browser. |

Useful kwargs (official examples): `impersonate='chrome'`, `stealthy_headers=True`, `http3=True`, `headless=True`, `network_idle=True`, `solve_cloudflare=True`, `google_search=False`, `disable_resources=False`, `load_dom=False`, `max_pages=2`, `cdp_url` (remote browser), `executable_path` (own Chromium), `capture_xhr` (collect matching XHR as `response.captured_xhr`).

Proxy: built-in `ProxyRotator` on sessions; per-request proxy override. Optional DoH via Cloudflare to avoid DNS leaks. Browser fetchers can block domains or ~3500 ad/tracker hosts.

### Async sessions

```python
import asyncio
from scrapling.fetchers import FetcherSession, AsyncStealthySession

async with FetcherSession(http3=True) as session:
    page1 = session.get("https://quotes.toscrape.com/")
    page2 = session.get("https://quotes.toscrape.com/", impersonate="firefox135")

async with AsyncStealthySession(max_pages=2) as session:
    urls = ["https://example.com/page1", "https://example.com/page2"]
    results = await asyncio.gather(*(session.fetch(url) for url in urls))
    print(session.get_pool_stats())
```

## Spiders

`Spider` is Scrapy-like: `name`, `start_urls`, async `parse`, `Request` / `Response`, `response.follow`.

- Concurrency: `concurrent_requests`, per-domain throttle, download delay, AutoThrottle.
- Multi-session: `configure_sessions(manager)` then `yield Request(url, sid="stealth")`.
- Pause/resume: `crawldir=...`; Ctrl+C graceful; restart with the same dir.
- Streaming: `async for item in spider.stream()`.
- Blocked-request detect + retry. Optional `robots_txt_obey`.
- Dev mode: cache responses to disk and replay `parse()` without re-hitting the site.
- Export: `result.items.to_json()` / `to_jsonl()` / `to_csv()` / `to_xml()`.
- `LinkExtractor`: allow/deny, domains, CSS/XPath scope, extensions, canonicalization.

```python
from scrapling.spiders import Spider, Request, Response
from scrapling.fetchers import FetcherSession, AsyncStealthySession

class MultiSessionSpider(Spider):
    name = "multi"
    start_urls = ["https://example.com/"]

    def configure_sessions(self, manager):
        manager.add("fast", FetcherSession(impersonate="chrome"))
        manager.add("stealth", AsyncStealthySession(headless=True), lazy=True)

    async def parse(self, response: Response):
        for link in response.css("a::attr(href)").getall():
            if "protected" in link:
                yield Request(link, sid="stealth")
            else:
                yield Request(link, sid="fast", callback=self.parse)
```

### Templates

| Template | Use |
|---|---|
| `CrawlSpider` | Rule-based link following |
| `SitemapSpider` | sitemap / robots.txt seeds |
| `XMLFeedSpider` / `CSVFeedSpider` | XML/RSS or CSV feeds |
| `ShopifySpider` | Every product via Shopify JSON API, one item per variant (`target_website`) |

## CLI

Needs `scrapling[shell]` (or `[all]`) plus `scrapling install` if browsers are used.

```bash
scrapling shell
scrapling extract get 'https://example.com' scrape/content.md
scrapling extract get 'https://example.com' scrape/content.txt --css-selector '#fromSkipToProducts' --impersonate 'chrome'
scrapling extract fetch 'https://example.com' scrape/content.md --css-selector '#fromSkipToProducts' --no-headless
scrapling extract stealthy-fetch 'https://nopecha.com/demo/cloudflare' scrape/captchas.html --css-selector '#padded_content a' --solve-cloudflare
```

`.txt` = text, `.md` = markdown of HTML, `.html` = raw HTML. Default extract is the `body` content.

Reinstall browsers from code if the CLI is unavailable:

```python
from scrapling.cli import install

install([], standalone_mode=False)
install(["--force"], standalone_mode=False)
```

## MCP / AI extra

`pip install "scrapling[ai]==0.4.14"`. Official MCP keeps browser sessions, can screenshot, and can drive remote browsers over CDP. Prefer Navin tools for delivery in this product; use Scrapling MCP only if the user asked for it.

## Scrapy interop

If the project already uses Scrapy, decorate a callback with `scrapling_response` and parse with Scrapling. Do not rewrite a working Scrapy spider unless asked.

## Navin fallbacks

Still apply after this API: `scrape` tool for no-code corpora; `browser` for assisted walls and Navin-driven UI. Do not copy sponsor proxy ads into generated code.
