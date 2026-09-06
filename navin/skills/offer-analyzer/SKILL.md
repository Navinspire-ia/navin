---
name: offer-analyzer
description: Compare a job or freelance offer to the Career profile - compensation, risk, visa, stack, and a clear accept / negotiate / walk-away. Use when a written offer arrives.
metadata: {"navin":{"emoji":"📑","category":"careers","default_for":"career"}}
---

# Offer Analyzer

Decide from facts in the Career store and the written offer. Do not cheerlead.

## Workflow

1. Import or attach the offer text (`career action=import` if it came from a closed page).
2. Compare to profile floors: country, remote, min_rate / min_salary, stack, visa.
3. Score the same way as Discover (Perfect / Good / Skip) plus commercial risk.
4. Hand compensation to `salary-negotiator` or `freelance-rate-card`.
5. Recommend one of: accept, negotiate (with the talk track), or walk away.

## Output

```markdown
## Offer - <Company> <Role>
Fit: Perfect | Good | Skip
Money vs floor: above / at / below
Risks: visa, lock-in, vague scope, unpaid trial
Decision: accept | negotiate | walk
If negotiate: 3 asks max
```

## Rules

- Never invent equity value or a competing offer.
- Advance `career action=stage` to offer, won, or rejected only when the user confirms.
- Cite the offer URL or the pasted text. No scrape.
