---
name: lead-qualification
description: Score prospects on need, budget, authority, timing, and fit — decide who gets sales effort. Use to prioritize the pipeline honestly.
metadata: {"navin":{"emoji":"🎛️","category":"sales"}}
---

# Lead Qualification

## Overview

Qualification protects the scarcest resource: selling time. Score honestly, disqualify fast, document why.

## Scoring model (BANT-F)

| Dimension | Questions | Signals |
|-----------|-----------|---------|
| **B**udget | can they pay? | company size, funding, current spend on alternatives |
| **A**uthority | talking to a decider or a tourist? | role, who signs, buying process |
| **N**eed | real pain or curiosity? | trigger event, cost of inaction, current workaround |
| **T**iming | why now? | deadline, contract renewal, project date |
| **F**it | can WE serve them well? | ICP match, geography, language, integration constraints |

Score each /5 with evidence; weighted total → A (pursue now) / B (nurture) / C (disqualify).

## Disqualification triggers

- No identifiable pain we solve
- Budget an order of magnitude off
- Deciders unreachable after 3 attempts
- Fit failure (out of ICP, red-flag payment history, misaligned expectations)

Disqualified ≠ deleted: log the reason + revisit condition ("re-check after their fiscal year").

## Workflow

1. Input: lead from `lead-generation` + discovery notes (`discovery-call-assistant`) or email exchanges.
2. Score the grid; mark unknowns as unknowns — they become next-call questions.
3. Route: A → proposal path; B → nurture sequence (`email-marketing`); C → log and archive.
4. Keep scores updated after every interaction; `pipeline-analyst` consumes them.

## Rules

- Optimism is not a data point; every score cites evidence.
- Re-qualify when anything material changes (champion leaves, budget cycle turns).
