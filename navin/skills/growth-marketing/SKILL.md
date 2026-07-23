---
name: growth-marketing
description: Design acquisition experiments, run them, and analyze results with a testing loop. Use when growth is flat or channels are unproven.
metadata: {"navin":{"emoji":"📈","category":"marketing"}}
---

# Growth Marketing

## Overview

Growth = disciplined experimentation. Hypothesis → smallest test → measure → decide. No "let's try everything".

## Experiment template

```markdown
## Experiment: <name>
- Hypothesis: if we [change], then [metric] improves because [reason]
- Metric + baseline: ...
- Minimum success: ...
- Cost/effort: ...
- Duration: ...
- Result: ... → scale / iterate / kill
```

## Idea sources

- Funnel data: biggest drop-off step (`marketing-analytics`)
- Voice of customer: sales calls, reviews, support tickets
- Competitor moves (`competitor-intelligence`)
- Channel playbooks: cold outbound, SEO, communities, partnerships, referrals

## Workflow

1. Map the funnel with real numbers; find the constraint.
2. Backlog 5–10 experiments; score ICE (Impact, Confidence, Ease).
3. Run 1–2 at a time; log each in `growth/experiments.md` in the workspace.
4. Weekly review: results, learnings, next tests (schedule with `cron`).
5. Scale winners into always-on programs (`campaign-manager`).

## Rules

- One variable per experiment when possible.
- Kill criteria are set *before* launch.
- A failed experiment with a clear learning is a win; an unmeasured one is a loss.
