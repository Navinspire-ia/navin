---
name: salary-negotiator
description: Prepare salary and package negotiation for a specific offer - market range, anchors, counters, and email drafts. Use before answering a recruiter on compensation.
metadata: {"navin":{"emoji":"💶","category":"careers","default_for":"career"}}
---

# Salary Negotiator

Negotiate the package, not a single number. Every figure must come from the Career profile, the offer, or cited market data.

## Inputs

1. `career action=status` and the offer id. Read title, country, track, compensation.
2. Profile floors: `min_salary` (jobs) or `min_rate` (freelance - hand to `freelance-rate-card`).
3. Market check via `web_search` on public salary pages (levels, official stats). Cite the URL and date. Never scrape LinkedIn salary.

## Output

```markdown
## Package - <Company> <Role>
Floor (walk away): ...
Target: ...
Stretch: ...
Non-cash: remote, bonus, equity, formation, conges, hardware
Talk track: 4 lines the user can say or paste
Counter email: short draft, facts only
```

## Rules

- Do not invent a competing offer.
- If the user has no floor, ask before naming a number.
- After an interview, log the discussed range with `career action=stage` or inbox notes.
- Human sends the mail. Navin drafts.
