---
name: seo-content-writer
description: Write search-optimized articles that read naturally - proper structure, intent match, and internal links, without keyword stuffing. Use for blog posts, guides, and landing page copy.
metadata: {"navin":{"emoji":"✍️","category":"seo"}}
---

# SEO Content Writer

Write for the reader first; structure for search engines second. No stuffing, no filler intros, no "in today's fast-paced world".

## When to use

- Blog posts, guides, landing pages targeting a primary keyword
- Briefs that must be publish-ready with meta + schema

## When not to use

- Pure technical audits
- Keyword maps without writing (use `keyword-research`)

## Article blueprint

1. **Title** - primary keyword near the front, ≤60 chars, clear promise
2. **Meta description** - 140-155 chars, keyword + hook
3. **H1** - one only, close to the title
4. **Intro** - answer the query in the first 2-3 sentences (snippet-worthy)
5. **H2/H3** - each answers a sub-question (PAA themes)
6. **Entities** - cover people/products/concepts SERP winners cover
7. **Internal links** - 2-5 descriptive anchors to real/proposed site pages
8. **FAQ** - real questions + FAQPage schema when useful
9. **Conclusion / CTA** - next action aligned to business goal

## Workflow

1. Lock primary keyword + intent (`keyword-research` or user brief).
2. Fetch top 3 SERP results: format, depth, gaps to beat.
3. Outline first; confirm with the user for long/high-stakes pieces.
4. Write with short paragraphs, bullets/tables, concrete examples - never fabricated stats.
5. Illustrate full articles: hero + section images via image tools (keyword-bearing filenames, natural alt). If image gen unavailable, list prompts + alts.
6. Add JSON-LD draft (Article and/or FAQPage).
7. Save `seo/content/<slug>.md` (+ assets beside it); reference in `seo-report-*.html`.

## GEO bonus

Add a 40-80 word answer-first block under major H2s so passages are quotable by AI answers (`geo-ai-search-optimizer`).

## Rules

- Match audience language/register (FR/EN/AR).
- Never fabricate statistics or quotes - link real sources or omit.
- Respect `brand-voice-manager` when defined.
- Primary keyword in title, H1, intro, one H2, conclusion - naturally.

## Anti-patterns

- 2000 words of fluff that never answers the query
- Keyword stuffing / synonym spam
- Placeholder "insert statistic here"
