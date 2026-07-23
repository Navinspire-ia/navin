---
name: virtual-team-builder
description: Compose and run a virtual team of AI agents — each with a role, mission, and skills — that collaborate on a goal using subagents. Use when a task benefits from multiple specialized roles working in parallel.
metadata: {"navin":{"emoji":"🤖","category":"operations"}}
---

# Virtual Team Builder

## Overview

Turn an organization design into a working AI team: define roles, brief each one like a real hire, spawn them as subagents, orchestrate their collaboration, and merge their output. You are the chief of staff — you own the mission, the roster, the handoffs, and the final assembly.

## When to build a team vs. work solo

Build a team when the task has ≥3 separable workstreams with different expertise (e.g. market analysis + financial model + landing copy), or when parallel speed matters. Stay solo for sequential or small tasks — orchestration has overhead.

## Team composition

1. Derive roles from the mission (use `org-designer` thinking): 2-5 roles, each with one clear deliverable.
2. Write a role brief per agent:

```markdown
# Agent: {role name}
- Mission: the single deliverable expected
- Context: everything needed to work autonomously (audience, constraints, data locations)
- Skills to apply: {relevant skill names}
- Output: exact file path + format expected
- Boundaries: what NOT to do (no scope creep, no fabricated data)
```

3. Spawn each role with the subagent tool, passing the full brief — subagents do not share your context.

## Orchestration patterns

| Pattern | Use when |
|---------|----------|
| Parallel specialists | independent workstreams; spawn all at once, merge at the end |
| Pipeline | output of one feeds the next (research → write → review); spawn sequentially |
| Producer + critic | quality matters; one agent produces, a reviewer agent critiques, producer revises |
| Manager + squad | big mission; a coordinator subagent decomposes and delegates further |

## Rules of the desk

- Every agent gets ONE deliverable and an exact output path; vague briefs produce vague work.
- Verify each deliverable exists and meets the brief before merging — trust but check.
- Merge yourself: write the final assembly/summary connecting the pieces; never dump raw agent outputs.
- Keep a roster file `team/roster.md`: role, mission, status, output path — update it as agents complete.
- Cap teams at 5 concurrent agents; escalate to the human before spawning more.
