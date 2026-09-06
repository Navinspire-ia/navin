---
name: on-page-seo-optimizer
description: Optimize existing pages - titles, meta descriptions, heading hierarchy, internal links, and content depth. Use to improve pages that rank on page 2-3 or underperform.
metadata: {"navin":{"emoji":"📐","category":"seo"}}
---

# On-Page SEO Optimizer

Improving an existing page usually beats writing a new one. Optimize what already has impressions. Senior bar: fetch the live page, compare SERP peers, propose surgical before→after edits.

## When to use

- URL underperforms for a known query
- Page-2/3 rankings or declining CTR
- Pre-publish polish on a draft URL

## When not to use

- No URL and no draft content (brief `seo-content-writer` instead)
- Sitewide technical crawl issues (start with `seo-technical-auditor`)

## Page checklist

| Element | Target |
|---------|--------|
| Title tag | ≤60 chars, keyword front-loaded, unique |
| Meta description | 140-155 chars, actionable, matches intent |
| H1 | one, matching intent |
| H2-H6 | logical hierarchy, no skipped levels |
| First 100 words | answer the query directly |
| Body depth | covers entities/questions top SERP covers |
| Internal links | in and out, descriptive anchors |
| Images | alt text, descriptive filenames, compression note |
| Schema | Article/Product/FAQ/LocalBusiness where relevant |
| Freshness | dates, updated stats, dead links removed |
| UX signals | thin content, intrusive popups, mobile readability |

## Workflow

1. Fetch the page with `web_fetch`; extract head + heading structure + word-count estimate.
2. Confirm primary keyword + intent with the user or from `keyword-research`.
3. Fetch current top 3 SERP results; note format, depth, unique angles.
4. Diagnose: intent mismatch? thin? weak title? no internal links? schema gap?
5. Produce a concrete edit list (before → after for each element).
6. If source files exist in the workspace/CMS export, apply edits; else deliver copy-ready patches.
7. Save under `seo/on-page-<slug>-<date>.md`.

## Output format

```markdown
## On-page fixes - <URL>
Primary keyword / intent / SERP format to match

| Element | Current | Proposed | Why |
|---------|---------|----------|-----|

### Content gaps vs SERP
- ...

### Internal link suggestions
- ...

### Schema draft (if needed)
```json
```
```

## Rules

- Keep URL stable; if a change is unavoidable, plan a 301.
- Preserve what already ranks - surgical edits over rewrites.
- Every proposed title/meta must be unique vs other known site pages.
- Do not invent competitor word counts; approximate from fetched content.

## Anti-patterns

- Rewriting the whole page when only the title/intent is wrong
- Keyword stuffing or identical meta across templates
