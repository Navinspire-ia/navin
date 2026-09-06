---
name: lead-qualification
description: Score prospects on need, budget, authority, timing, and fit - decide who gets sales effort. Use to prioritize the pipeline honestly.
metadata: {"navin":{"emoji":"🎛️","category":"sales"}}
---

# Lead Qualification

Qualification protects selling time. Score honestly, disqualify fast, document why. Use the deterministic helper for ICP totals and CSV validation.

The live book is Studio `#/leads`. `leads action=rescore` writes BANT-F on the desk. The desk loop and `leads action=watch` re-score locally. Heartbeat never hunts and never sends a sequence.

## When to use

- Ranking a prospect list before outreach
- Routing A/B/C after enrichment or discovery

## When not to use

- Pure research sheets without scoring (`account-research`)
- Inflating scores to hit activity quotas

## Scoring model (BANT-F + ICP)

| Dimension | Questions | Signals |
|-----------|-----------|---------|
| **B**udget | can they pay? | size, funding, current spend |
| **A**uthority | decider or tourist? | role, buying process |
| **N**eed | real pain? | trigger, cost of inaction |
| **T**iming | why now? | deadline, renewal, project |
| **F**it / ICP | can we serve them? | sector, size, geo, stack |

Score each dimension 0-5 with evidence. Weighted default: Fit 30%, Need 25%, Timing 20%, Authority 15%, Budget 10%.

Tiers from total 0-100:

| Tier | Range | Action |
|------|-------|--------|
| A | ≥70 | contact now |
| B | 40-69 | nurture |
| C | <40 | discard / revisit condition |

## Disqualification triggers

- No identifiable pain we solve
- Budget an order of magnitude off
- Fit failure (out of ICP)
- Deciders unreachable after agreed attempts

Log reason + revisit condition ("re-check after FY").

## Helper script

```bash
# Validate CSV columns / URLs / confidence
python navin/skills/lead-qualification/scripts/score_leads.py sales/prospects.csv --validate-only

# Score rows that already have bant columns (fit,need,timing,authority,budget)
python navin/skills/lead-qualification/scripts/score_leads.py sales/prospects.csv -o sales/prospects-scored.csv
```

Expected optional columns for scoring: `fit,need,timing,authority,budget` (0-5 each) or a single `icp_score`.

## Workflow

1. Input leads + discovery/enrichment notes.
2. Fill BANT-F; unknowns become next-call questions (do not invent).
3. Run the script; route A/B/C.
4. Update after material changes; `pipeline-analyst` consumes tiers.

## Rules

- Every score cites evidence or stays unknown.
- Optimism is not a data point.
- Never mark email `verified` without enrichment proof / public source.

## Anti-patterns

- Scoring everyone A
- Dropping disqualified rows without reason codes
