---
name: task-planner
description: Decompose complex requests into ordered steps, dependencies, risks, and deliverables. Use when the user asks to plan a project, break down a feature, migrate systems, or tackle multi-step work before coding.
metadata: {"navin":{"emoji":"🧭","category":"intelligence"}}
---

# Task Planner

## Overview

Turn ambiguous or large requests into an executable plan the agent (and user) can follow. Prefer clarity over length.

## When to use

- Multi-step projects, migrations, refactors, or “build X end-to-end”
- Unclear scope — plan first, then ask only the blockers
- Handoffs to `spawn` subagents or parallel workstreams

## Workflow

1. **Restate the goal** in one sentence (success criteria).
2. **List constraints** (deadline, stack, security, “do not touch X”).
3. **Break into steps** (5–12 max for the first pass). Each step must be:
   - actionable
   - independently verifiable
   - ordered by dependency
4. **Mark dependencies** (`blocked by step N`).
5. **Identify risks / unknowns** and the cheapest probe to resolve each.
6. **Define deliverables** (files, PRs, reports, commands to run).
7. **Propose execution mode**:
   - sequential in this chat, or
   - `spawn` for independent tracks (architect / implementer / reviewer)

## Output format

```markdown
## Goal
...

## Plan
1. ...
2. ... (depends on 1)

## Risks
- ...

## Deliverables
- ...

## Next action
Start with step 1: ...
```

## Rules

- Do not invent requirements — call out assumptions.
- Prefer the smallest plan that unblocks progress.
- After the user confirms, execute step 1 immediately unless they ask to wait.
- When plans change mid-flight, update the plan briefly before continuing.
