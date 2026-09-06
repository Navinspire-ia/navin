---
name: mission-ledger
description: Magentic-style Task/Progress Ledger orchestration for /blueprint, /forge, /cruise, and /mission. Use whenever planning or executing multi-step work on the board.
metadata: {"navin":{"emoji":"📒","category":"intelligence"}}
---

# Mission Ledger (Task + Progress)

## Overview

You are the orchestrator. The mission ledger (`.navin/board/mission.json`) plus the project board are the single source of truth - never keep a phantom plan only in chat.

- **Task Ledger**: goal, constraints, facts, missing_info, acceptance_criteria, versioned steps, history
- **Progress Ledger**: current step, evidence, stall/loop, budgets, next_actor, pause

Use the `board` tool ledger actions: `ledger_init`, `ledger_get`, `ledger_update`, `ledger_progress`, `ledger_replan`, `ledger_pause`, `ledger_resume`.

## Orchestrator protocol (every step)

1. **Read** `ledger_get` + `board next` (never guess).
2. **Choose** one ready step (or spawn parallel independent ready steps in /cruise|/mission).
3. **Act** with Navin tools.
4. **Verify** per step `validation` (`test` / `lint` / `verify` / `manual` / `none`).
5. **Write** `ledger_progress` with `ok`, `evidence`, `progress`.
6. **Decide**: next | retry (cap max_retries) | `ledger_replan` local | `ledger_pause`.
7. **Never** mark `done` without evidence when validation is test/lint/verify.

## One mission at a time

There is one ledger per project (`.navin/board/mission.json`) and it is replayed
into your context every turn. Two things follow.

- **A one-off errand is not a mission.** "Generate a deck", "rename this file",
  "answer this question" get done, not planned. Do not `ledger_init` and stop
  for Build. A ledger is for a multi-step system the user asked you to carry,
  not for every request that happens to take three tool calls.
- **The goal you are shown is not automatically yours.** When a new request has
  nothing to do with the mission in context, do not restate that mission's goal,
  criteria or facts. That is how a request for a slide deck ends up filed under
  "finish the CRM", with the deck's steps hanging off someone else's plan.

`ledger_init` therefore refuses while a mission is `draft`/`running`/`paused`/
`blocked`. When it does, pick one:

| The request is | Do |
| --- | --- |
| part of the open mission | `board create` the tasks, then `ledger_update` |
| a separate one-off | just do the work, no ledger |
| the open mission is finished | `ledger_update status=done`, then `ledger_init` |
| the open mission is abandoned | `ledger_init` with `replace=true`, and say so |

## Planning (/blueprint)

- If the request is a one-off errand, do not plan. Switch to Agent and do the work.
- Fill facts and missing_info honestly; do not invent facts.
- Create board tasks with `depends_on`, `acceptance`, `validation`.
- `ledger_init` with goal + criteria + step_ids.
- Stop only for multi-file systems, migrations, or when the user asked only for a plan. Do not claim or execute in that case.

## Execution (/forge, /cruise, /mission)

- Prefer local replan over rewriting the whole plan.
- On loop_detected or high stall_count → `ledger_replan` around the failed step.
- On budget/human wall / blocking missing_info → `ledger_pause` and ask clearly.
- Subagent briefs must include Role, Goal, Context paths, Constraints, Done when (acceptance), Do not.
- Merge subagent evidence before marking the parent step done.

## History

Every meaningful plan change must go through ledger version bumps (`ledger_replan`, `ledger_update`, pause/resume) so `history.reason` explains why the plan changed.
