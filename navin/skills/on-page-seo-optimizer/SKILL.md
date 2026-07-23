---
name: on-page-seo-optimizer
description: Optimize existing pages — titles, meta descriptions, heading hierarchy, internal links, and content depth. Use to improve pages that rank on page 2–3 or underperform.
metadata: {"navin":{"emoji":"📐","category":"seo"}}
---

# On-Page SEO Optimizer

## Overview

Improving an existing page usually beats writing a new one. Optimize what already has impressions.

## Page checklist

| Element | Target |
|---------|--------|
| Title tag | ≤60 chars, keyword front-loaded, unique |
| Meta description | 140–155 chars, actionable |
| H1 | one, matching intent |
| H2–H6 | logical hierarchy, no skipped levels |
| First 100 words | answer the query directly |
| Internal links | in and out, descriptive anchors |
| Images | alt text, compressed, descriptive filenames |
| Schema | Article/Product/FAQ where relevant |
| Freshness | dates, updated stats, dead links removed |

## Workflow

1. Fetch the page with `web_fetch`; extract head + heading structure.
2. Compare with the current top 3 SERP results for the target keyword.
3. Diagnose: intent mismatch? thin content? weak title? no internal links?
4. Produce a concrete edit list (before → after for each element).
5. If the user provides the source files (CMS export, repo), apply edits directly.

## Output format

```markdown
## On-page fixes — <URL>
| Element | Current | Proposed | Why |
```

## Rules

- Keep URL stable; if a URL change is unavoidable, plan a 301.
- Preserve what already ranks — surgical edits over rewrites.
