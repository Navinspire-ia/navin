---
name: multi-agent-orchestration
description: Coordinate specialized subagents (architect, implementer, reviewer, analyst, researcher) with clear roles, handoffs, and validation. Use spawn for parallel independent tracks.
metadata: {"navin":{"emoji":"🕸️","category":"intelligence"}}
---

# Multi-Agent Orchestration

## Overview

Split complex work across roles. You remain the orchestrator: define tasks, merge results, and own the final answer.

## Roles (pick what you need)

| Role | Responsibility | Typical tools |
|------|----------------|---------------|
| Architect | design, interfaces, risks | `read_file`, `grep` |
| Implementer | code / config changes | `edit` / `write_file`, `exec` |
| Reviewer | bugs, regressions, style | `read_file`, `grep`, `exec` tests |
| Analyst | data, metrics, SQL | `exec`, web tools |
| Researcher | docs, competitors, APIs | `web_search`, `web_fetch` |

## Workflow

1. **Decompose** with `task-planner` thinking: which tracks are independent?
2. **Spawn only independent work** via `spawn(task=..., label=...)`.
   - Put the full brief in `task` - subagents do not see your full history.
   - Include success criteria and “out of scope”.
   - Independent tracks go out together, in one batch: they run concurrently and
     each reports back on its own, so waiting for one before starting the next
     buys nothing.
3. **Serialize conflicting edits** - never spawn two writers on the same files.
4. **Integrate** results: resolve conflicts, run verification, present one coherent outcome.
5. **Review gate**: for user-facing or production changes, run a reviewer pass (same chat or `spawn`) before claiming done.

## Task brief template (put inside spawn.task)

```text
Role: Implementer
Goal: ...
Context paths: ...
Constraints: ...
Done when: ... (copy acceptance from the mission/board step)
Validation: test|lint|verify|manual|none
Do not: ...
Ledger step id: ...
```

Return evidence the parent can pass to `board` `ledger_progress` / `done`.

## Rules

- Prefer 1-3 subagents; more creates merge chaos.
- You deliver the final summary - never dump raw subagent noise.
- If a subagent fails, retry with a tighter brief or finish yourself.
- If a result never reaches you, `spawn(action="results")` says how the finished
  ones ended. Use it instead of assuming a subagent never ran.
- Security / prod deploys always need an explicit reviewer step.
- Parent marks board `done` only after merging evidence that satisfies validation.
