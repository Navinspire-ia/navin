---
name: task-planner
description: Decompose complex requests into ordered steps, dependencies, risks, and deliverables. Use when the user asks to plan a project, break down a feature, migrate systems, or tackle multi-step work before coding.
metadata: {"navin":{"emoji":"🧭","category":"intelligence"}}
---

# Task Planner

## Overview

Turn ambiguous or large requests into an executable plan the agent (and user) can follow. Prefer clarity over length.

## When to use

- Multi-step projects, migrations, refactors, or "build X end-to-end"
- Unclear scope - plan first, then ask only the blockers
- Handoffs to `spawn` subagents or parallel workstreams

## When NOT to use

One-shot work is not a plan: one deck, one memo, one file, one script, a typo, a rename, a selected template. Do that now. Do not file a two-step board and wait for Build. HTML or JSON in the pipeline is an intermediate, not a deliverable and not a reason to stop.

## Workflow

1. **Restate the goal** in one sentence (success criteria).
2. **List constraints** (deadline, stack, security, “do not touch X”).
3. **Break into steps** (5-12 max for the first pass). Each step must be:
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

Prefer recording the plan on the board + mission ledger. When writing in chat, still include:

```markdown
## Goal
...

## Constraints
- ...

## Facts
- ...

## Missing info
- ...

## Acceptance criteria
- ...

## Plan
1. ... (validation=verify|test|lint|manual|none; depends on …)
2. ...

## Risks
- ...

## Next action
Start with step 1: ...
```

Each board step should set `acceptance` and `validation`. After filing steps, call `board` `ledger_init` only for multi-file / architecture work. A plan that describes a system includes an `archify` diagram (HTML path + caption).

## Rules

- Do not invent requirements - call out assumptions as missing_info or facts.
- Prefer the smallest plan that unblocks progress.
- If the request is a greeting or a vague one-liner with no goal, ask what to plan - do not invent a project name from the word.
- After the user confirms (or in /forge|/cruise with a clear target), execute step 1 unless they ask to wait.
- Wait for Build only after /blueprint on a multi-file or architecture job, or when the user asked only for a plan. A short mechanical plan (1-3 steps) on a simple deliverable starts step 1 in this turn.
- When plans change mid-flight, `ledger_replan` locally and state why before continuing.
