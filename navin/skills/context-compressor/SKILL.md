---
name: context-compressor
description: Compress long histories and documents into durable decision summaries without losing constraints, TODOs, or file paths. Use before long continuations, handoffs, or when context is getting large.
metadata: {"navin":{"emoji":"📦","category":"intelligence"}}
---

# Context Compressor

## Overview

Produce a high-signal digest that a future turn (or subagent) can resume from.

## What to keep

- Goal and success criteria
- Hard constraints (“do not…”, versions, environments)
- Decisions made + why
- Open questions / blockers
- Key file paths and commands
- Current status of each workstream

## What to drop

- Exploratory dead-ends that no longer matter
- Raw tool dumps (keep conclusions only)
- Repeated chit-chat
- Full file contents (link paths instead)

## Workflow

1. Skim the conversation / docs for decisions and artifacts.
2. Write a **Resume Brief** using the template below.
3. Offer to store durable facts in memory only when the user wants persistence (Dream manages long-term memory files - do not silently rewrite them).
4. For handoff to `spawn`, paste the Resume Brief into the subagent `task`.

## Resume Brief template

```markdown
## Goal
...

## Done
- ...

## Decisions
- ...

## Open
- ...

## Key paths
- `path/to/file`

## Next step
...
```

## Rules

- Prefer bullets over prose.
- Never invent completed work.
- If unsure whether a constraint still applies, list it under Open.
