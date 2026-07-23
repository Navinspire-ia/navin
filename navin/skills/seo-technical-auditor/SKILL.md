---
name: seo-technical-auditor
description: Audit technical SEO — indexation, sitemap, robots.txt, Core Web Vitals, redirects, canonical tags, and crawl errors. Use when a site underperforms in search or before a relaunch.
metadata: {"navin":{"emoji":"🔧","category":"seo"}}
---

# SEO Technical Auditor

## Overview

Find and prioritize the technical issues blocking a site from ranking. Evidence first, checklist second.

## Audit checklist

| Area | What to check |
|------|---------------|
| Indexation | `site:domain.com` coverage, noindex tags, canonical conflicts |
| robots.txt | `web_fetch https://domain/robots.txt` — blocked paths, sitemap line |
| Sitemap | fetch `/sitemap.xml` — valid XML, fresh URLs, no 404s inside |
| Redirects | chains (301→301→200), http→https, trailing slash consistency |
| Core Web Vitals | LCP, CLS, INP via PageSpeed Insights (share the URL for the user) |
| Mobile | viewport meta, tap targets, responsive layout |
| HTTPS | mixed content, invalid certs |
| Structured data | JSON-LD presence and validity |
| Duplicates | same content on multiple URLs, missing canonicals |
| Hreflang | for FR/EN/AR sites — reciprocal tags |

## Workflow

1. Fetch homepage + robots.txt + sitemap with `web_fetch`; note status codes and meta robots.
2. Sample 5–10 key pages (money pages, blog, category) and inspect `<head>`: title, meta description, canonical, robots, hreflang.
3. Test redirect behavior on common variants (http, www, trailing slash).
4. Check `site:` results and compare with sitemap size to estimate index coverage.
5. Produce the report.

## Report format

```markdown
## Technical SEO Audit — <domain>

### Critical (blocks ranking)
1. issue — evidence — fix

### Important
...

### Nice to have
...

### Quick wins (this week)
- ...
```

## Rules

- Every finding needs a URL + observed evidence.
- Prioritize by impact × effort; do not dump 100 undifferentiated issues.
- Cross-reference `seo-monitoring` for tracking fixes over time.
