---
name: blog-writer
description: Produce structured, documented, readable long-form articles - tutorials, opinion pieces, industry analyses. Use for editorial content beyond pure SEO pieces.
metadata: {"navin":{"emoji":"📝","category":"writing"}}
---

# Blog Writer

## Overview

Write articles people finish. Structure, evidence, and voice - with sources when facts are claimed.

## Article types

| Type | Skeleton |
|------|----------|
| Tutorial / how-to | problem → prerequisites → numbered steps → pitfalls → result |
| Opinion / essay | claim → strongest counterargument → evidence → implications |
| Analysis | question → data/sources → findings → so-what |
| Listicle (sparingly) | promise → items with real substance each → verdict |
| Case study narrative | see `case-study-writer` |

## Workflow

1. Angle first: what does this article say that the 10 existing ones don't?
2. Research with `web_search`/`web_fetch`; collect quotes, numbers, source URLs.
3. Outline: title, hook, H2 skeleton, conclusion - validate with the user for long pieces.
4. Draft: hook in 3 sentences, subheads that tell the story alone, examples in every section.
5. Edit pass: cut intro throat-clearing, verify each fact (`fact-checker`), run `proofreader`.
6. Ship with metadata via `seo-content-writer` rules if it targets search.

## Readability bar

- Paragraphs ≤ 4 lines; a visual break (list, quote, image note) every ~300 words
- No section without a concrete example or number
- Conclusion says what to do next, never "in conclusion, we saw that…"

## Rules

- Sources linked inline; no orphan claims.
- Write in the user's brand voice; French content uses French typography (espaces insécables, guillemets).
