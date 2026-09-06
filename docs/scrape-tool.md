# Scrape Tool

Turn open-web URLs into clean datasets at scale - fetch, crawl, extract, clean, and export - with a Rust hot path when `navin-core` is installed.

When the Scraping agent **writes scraper code**, it uses **Scrapling 0.4.14** first. This `scrape` tool is the no-code fallback (Rust / httpx). Interactive JS uses the `browser` tool.

For the Scraping studio UI and `/scrape` workflows, see [navin_scraping](./navin_scraping/README.md).

## Why You Need It

`web_fetch` is great for one page. Corpora need parallelism, cleaning, and file exports. The `scrape` tool gives the agent:

- **Parallel fetch** of many URLs with title, text, markdown, links, and meta
- **Same-domain crawl** (BFS) with depth and page caps
- **Cleaning** of HTML chrome into readable text/markdown
- **Exports** to `csv`, `json`, `jsonl`, `xml`, `xlsx`, `md`, and HTML `report`
- **Autonomy**: dry-run a few pages, scale with `pipeline`, escalate to `browser` for JS shells
- **Assisted walls**: detect captcha / Cloudflare / paywall / login, **pause**, ask the user to solve or sign in via a browser session - **never auto-bypass**

## Configuration

Enabled by default.

```yaml
tools:
  scrape:
    enabled: true          # default: true
    max_pages: 50          # default crawl/pipeline page cap (hard max 500)
    concurrency: 8         # parallel workers (1-32)
    timeout_seconds: 30
    max_bytes: 5242880     # 5 MiB per response
    user_agent: "Mozilla/5.0 (compatible; NavinScrape/0.1; +https://navin.ai) AppleWebKit/537.36"
    proxy: null            # optional HTTP(S) proxy
    respect_same_domain: true
    assisted: true         # pause + ask user on captcha/CF/paywall/login (no bypass)
    respect_robots: true   # honour robots.txt + crawl-delay
    max_retries: 2         # Retry-After / backoff on 408/429/5xx
    enrich: true           # wordCount / fetchedAt / lang / OG on records
    allow_private_network: false # explicit local/VPN opt-out from scrape SSRF blocking
```

Disable with `tools.scrape.enabled: false`. Set `assisted: false` only if you want a wall report without the approval pause (still no bypass).

Build the Rust accelerator (recommended):

```bash
make native
```

The secure Python transport is the default for network fetches. It validates and
pins DNS before connecting and revalidates every redirect. The Rust batch path is
used only when its transport contract is equivalent, including explicit local
network opt-out runs after robots prefiltering. Responses report `backend`.

---

## Actions

### fetch - Parallel GET/POST + extract

```text
scrape(action="fetch", url="https://example.com/docs")
scrape(action="fetch", urls='["https://a.example/x","https://a.example/y"]')
scrape(action="fetch", urls="https://a.example/x\nhttps://a.example/y", concurrency=16)
scrape(action="fetch", url="https://api.example/items", method="POST")
scrape(action="fetch", url="https://api.example/items", method="POST", json_body='{"q":"navin"}')
```

Returns JSON `{ "pages": [ ... ] }` with `url`, `finalUrl`, `status`, `title`, `text`, `markdown`, `links`, `meta`, `tables`, `wordCount`, `fetchedAt`, `error`. PDF responses are MIME-sniffed and add `document` with extractor, metadata, page count, and per-page text.

### crawl - Same-domain BFS

```text
scrape(action="crawl", url="https://example.com/docs", max_depth=2, max_pages=40)
```

Follows in-domain links up to `max_depth` / `max_pages`. Follows pagination (`rel=next`, `?page=`, `/page/N`) without burning depth. Returns pages plus `queued` / `seen` / `stats`. Optional `allow` / `deny` regex and `checkpoint` for mass resume.

### paginate - Walk listing pages

```text
scrape(action="paginate", url="https://shop.example/list?page=1", max_pages=40)
```

Follows next/page links from a listing until exhausted or `max_pages`.

### extract - HTML → structured

```text
scrape(action="extract", html="<html>...</html>", url="https://example.com/page")
```

Use when you already have HTML (e.g. from `browser action=content`).

### clean - Normalize text

```text
scrape(action="clean", text="  messy \n\n  text  ")
```

### export - Write files

```text
scrape(action="export", records='{"pages":[...]}', format="xlsx", path="scrape/out.xlsx")
scrape(action="export", records='[...]', format="csv", path="scrape/out.csv")
scrape(action="export", records='[...]', format="report", path="scrape/report.html")
```

`path` must stay inside the workspace. Formats: `csv`, `json`, `jsonl`, `xml`, `xlsx`, `md`, `report`.

### pipeline - Crawl then export

```text
scrape(
  action="pipeline",
  url="https://example.com/docs",
  format="xlsx",
  path="scrape/docs.xlsx",
  max_depth=2,
  max_pages=30
)
```

Returns a short summary (`pages`, `ok`, `export`, `sample`) - not the full corpus in chat.

### sitemap / enrich / tables

```text
scrape(action="sitemap", url="https://example.com/sitemap.xml", max_pages=100)
scrape(action="enrich", records='{"pages":[...]}')
scrape(action="tables", html="<table>...</table>")
```

---

## Parameters

| Parameter | Used by | Description |
| --- | --- | --- |
| `action` | all | `fetch` \| `crawl` \| `paginate` \| `extract` \| `clean` \| `export` \| `pipeline` \| `diagnose` \| `sitemap` \| `enrich` \| `tables` |
| `url` | fetch, crawl, paginate, pipeline, extract, sitemap | Single seed / base URL |
| `urls` | fetch, crawl, pipeline, sitemap | JSON array or newline/comma list |
| `html` | extract, tables, diagnose | Raw HTML |
| `text` | clean, diagnose | Raw text |
| `records` | export, enrich | JSON array or `{ "pages": [...] }` |
| `format` | export, pipeline | Export format |
| `path` | export, pipeline | Workspace-relative output path |
| `max_pages` | crawl, paginate, pipeline, sitemap | Page cap (hard max 500) |
| `max_depth` | crawl, pipeline | Link depth (`0` = seeds only) |
| `concurrency` | fetch | Parallel workers |
| `method` | fetch | `GET` \| `POST` \| `PUT` \| `DELETE` |
| `json_body` | fetch | JSON request body |
| `form_body` | fetch | JSON object encoded as form data |
| `body_base64` | fetch | Raw request bytes as base64 |
| `headers` | fetch | JSON object restricted to the request-header allowlist |
| `cookies` | fetch | JSON cookie object |
| `browser_session` | fetch | Active Playwright session key for controlled cookie handoff |
| `allow` | crawl, paginate | Regex: only matching URLs |
| `deny` | crawl, paginate | Regex: skip matching URLs |
| `checkpoint` | crawl | Resume path e.g. `scrape/ckpt.json` |

---

### diagnose - Classify walls

```text
scrape(action="diagnose", url="https://example.com")
scrape(action="diagnose", html="<html>Just a moment...</html>")
```

Returns `wall.kind` (`captcha`, `cloudflare`, `paywall`, `login`, `empty_shell`, etc.) and the no-bypass policy.

---

## Walls (assisted, no bypass)

On `fetch` / `crawl` / `pipeline` / `diagnose`, each page may include a `wall` object. The response also has `walls`, `bypass_policy`, and `next_steps`.

| Kind | Human? | Agent behaviour |
| --- | --- | --- |
| `empty_shell` | no | Escalate to `browser` (render / API) - not a security bypass |
| `forbidden` / `error` | no | Report; optional browser retry |
| `captcha` / `cloudflare` / `paywall` / `login` | **yes** | **Pause** (approval card when `assisted: true`). User solves or signs in via browser. **Never bypass.** |

Flow for human walls:

1. Tool detects the wall and requests approval.
2. User allows, then completes the challenge / login in the browser tool session.
3. Agent runs `browser action=content`, then `scrape action=extract` / `export`.

---

## When to use browser instead

| Situation | Tool |
| --- | --- |
| Static / server-rendered HTML | `scrape` |
| Empty shell / heavy client render | `browser` (`action=content`) |
| JSON API behind the page | `browser` network + `response_body`, then `scrape action=export` |
| Captcha / Cloudflare / paywall / login | Pause → user in `browser` session → resume (no bypass) |

---

## Security

- Only `http` / `https` URLs. Scrape blocks localhost, private, link-local, and metadata targets by default, pins validated DNS, and revalidates redirects. `allow_private_network: true` is an explicit local/VPN opt-out.
- Response downloads stop at `max_bytes` (hard configuration max 20 MiB).
- Request headers use an allowlist and errors redact authorization, API-key, and cookie values.
- Exports are confined to the project workspace.
- Treat page bodies as untrusted data (prompt-injection mindset).
- **No automatic bypass** of captcha, Cloudflare, paywalls, or login walls.
- Respect robots/ToS when the user cares about compliance - ask if unsure.

---

## Related

- Studio module: [navin_scraping](./navin_scraping/README.md)
- Web search/fetch: [Configure Web Search](./guides/configure-web-search.md)
- Native build: `make native` (see [Performance](./performance.md))
