---
name: competitor-seo-analysis
description: Analyze competitors' keywords, top pages, content structure, and backlink signals to find gaps. Use before content planning or when a competitor outranks the user.
metadata: {"navin":{"emoji":"🕵️","category":"seo"}}
---

# Competitor SEO Analysis

Reverse-engineer what makes competitors rank, then find beatable gaps. Senior bar: SERP competitors (who ranks), not only business rivals.

## When to use

- Before content planning or market entry
- When a named competitor outranks the user
- Gap analysis for clusters

## When not to use

- Brand messaging comparison with no SEO angle (marketing competitor scan)
- Inventing backlink metrics without `seo-data-provider` / exports

## Workflow

1. Identify 3-5 true SERP competitors for target keywords via `web_search`.
2. For each, `web_fetch` top pages: title, H1-H3, depth cues, media, schema, IA clues.
3. Map content cadence/topics from blog/resources if present.
4. Content gap: topics they cover that the user does not (and vice versa).
5. Backlink signals without paid tools: `"competitor.com" -site:competitor.com` mentions, directories, partners. With API: use `seo-data-provider`.
6. Summarize replicate / beat / ignore; prioritize by winnability.
7. Save `seo/competitor-analysis-<date>.md`.

## Report format

```markdown
## Competitor SEO - <user domain> vs <competitors>

### Who ranks and why
| Competitor | Strengths | Weaknesses | Evidence URL |

### Content gaps (our opportunities)
1. topic - competitor URL - angle to beat it - effort

### Link opportunities
- ...

### Priority actions
1. ...
```

## Rules

- Cite actual URLs for every claim.
- Focus on beatable gaps, not "publish 200 articles".
- Never invent domain rating / backlink counts; mark requires API data.
- Feed results into `keyword-research` clusters and `backlink-strategy`.

## Anti-patterns

- Comparing only homepage copy
- Listing giants (Amazon/Wikipedia) as "must beat" without niche angles
