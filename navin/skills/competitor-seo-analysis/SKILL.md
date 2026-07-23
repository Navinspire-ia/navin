---
name: competitor-seo-analysis
description: Analyze competitors' keywords, top pages, content structure, and backlink signals to find gaps. Use before content planning or when a competitor outranks the user.
metadata: {"navin":{"emoji":"🕵️","category":"seo"}}
---

# Competitor SEO Analysis

## Overview

Reverse-engineer what makes competitors rank, then find the gaps you can win.

## Workflow

1. Identify 3–5 true SERP competitors (who actually ranks for target keywords — not just business rivals).
2. For each competitor, inspect with `web_fetch`:
   - top pages structure (title, H1–H3, word count, media, schema)
   - blog cadence and topics
   - internal linking patterns to money pages
3. Content gap: topics they cover that the user does not (and vice versa).
4. Backlink signals (without paid tools): search `"competitor.com" -site:competitor.com` for mentions, guest posts, directories, partners.
5. Summarize what to replicate, what to beat, what to ignore.

## Report format

```markdown
## Competitor SEO — <user domain> vs <competitors>

### Who ranks and why
| Competitor | Strengths | Weaknesses |

### Content gaps (our opportunities)
1. topic — competitor URL — angle to beat it

### Link opportunities
- ...

### Priority actions
1. ...
```

## Rules

- Cite actual URLs for every claim.
- Focus on beatable gaps, not "publish 200 articles".
- Feed results into `keyword-research` clusters and `backlink-strategy`.
