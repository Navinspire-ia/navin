---
name: market-research
description: Study a market — size, trends, players, pricing, regulation, and opportunities — with sourced findings. Use before entering a market or positioning an offer.
metadata: {"navin":{"emoji":"🌍","category":"marketing"}}
---

# Market Research

## Overview

Answer: is this market worth entering, who wins today, and where is the opening. Every claim sourced.

## Research grid

| Question | Sources |
|----------|---------|
| Size & growth | industry reports, government stats, funding news |
| Segments | who buys what, at what price point |
| Players | leaders, challengers, local specialists |
| Pricing | public pricing pages, RFP results, proxies |
| Regulation | local requirements (esp. DZ/Gulf/EU data rules) |
| Trends | tech shifts, buyer behavior changes |
| Openings | underserved segments, complaints about incumbents |

## Workflow

1. Frame with the user: market definition, geography, decision to inform.
2. Multi-source research via `web_search` + `web_fetch` (see `deep-web-research` method): reports, news, competitor sites, reviews, communities.
3. Triangulate numbers: if sizes conflict, show the range and the source of each.
4. Map players on two axes that matter to the user (e.g. price × specialization).
5. Conclude with 3–5 openings and the evidence behind each.

## Report format

```markdown
## Market research — <market, geography, date>
### Answer in one paragraph
### Market size & growth [sources]
### Key players
### Pricing landscape
### Openings & risks
### Sources
```

## Rules

- Date every figure — markets move; 2019 data ≠ today.
- Distinguish facts, estimates, and opinions typographically.
