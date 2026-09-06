---
name: project-board
description: Work the shared project task board (kanban + dependency-aware plan + milestones + timeline) that humans edit in the Dev workbench - decompose objectives, claim tasks, track progress, file findings, assign to subagents or humans, and run the board in a loop. Use for /board, task tracking, project planning, or when audits produce actionable findings.
metadata: {"navin":{"emoji":"📋","category":"devops"}}
---

# Project Board

## Overview

The board (`board` tool) is the shared workspace between you and humans: they see it live as a kanban plus an Evolutions timeline in the Dev workbench, and can edit or report issues at any time. Treat it as the single source of truth for what is done, in progress, and missing. Statuses: `backlog → planned → in_progress → review → audit → fix → done`, plus `blocked`.

Tasks form a dependency graph through `depends_on`. The board derives from it the only three facts you need to decide what to do:

- **ready** - every dependency is `done`, so it can start now.
- **blocked** - a dependency is unfinished or missing, or a human parked it in `blocked`.
- **critical path** - the longest chain of open tasks. Shortening it shortens the whole project; a high-priority leaf only helps itself.

## Discipline

1. **Ask the board, don't guess**: `board next` returns the ready queue already sorted (critical path first, then the tasks that unblock the most work, then priority). Pick the top item. Never scan `list` and eyeball a choice - `next` is the deterministic contract, and it explains itself when nothing is ready.
2. **One at a time**: `board claim` sets you as assignee and moves the task to `in_progress`. Keep exactly one task in flight per actor; a subagent may hold its own. `next` tells you what is already in flight.
3. **Record as you go**: `comment` findings, decisions, and file references on the task - humans read these. Move to `review` when a human should check.
4. **Never close what you did not verify**: `done` requires evidence when
   `validation` is `test`, `lint`, or `verify` (pass `evidence=` on move/update).
   Also call `ledger_progress` with the same evidence. If you could not verify,
   use `review` and say why. A wrong `done` corrupts every downstream `ready`
   computation.
5. **File what you find**: audits and reviews (/inspect, /fortify, /probe…) must convert each confirmed finding into a task - `create` with `status=fix`, severity mapped to `priority`, and the file path in the description.
6. **Mission ledger**: for multi-step systems use `ledger_init` / `ledger_progress` /
   `ledger_replan`. Prefer local replan; pause on budget/human blockers.
   A one-shot (deck, memo, one file, typo) is not a mission: do it now.

## Planning an objective

When asked to plan something substantial, decompose before executing:

1. `board plan` first - never plan on top of an unknown board.
2. Create a milestone per outcome (`milestone_create`, with `target_date` when there is a real deadline).
3. Create tasks under it (`milestone_id`), each one a single verifiable deliverable with acceptance criteria in the description.
4. **Wire the order with `depends_on`, not with priorities.** Priority says how much it matters; `depends_on` says what is physically impossible before something else. This is what makes the plan executable: the board then computes the ready queue and the critical path for you, and parallel work falls out for free.
5. Assign each task to whoever should do it (see below), then report the plan: milestone list, critical path length, what starts immediately.

Keep the graph clean: `plan` reports dependency cycles and references to unknown tasks. Both make tasks permanently unready, so fix them on sight rather than working around them.

## Intelligent assignment

- **Subagent**: independent, well-scoped task → `spawn` with a complete brief (subagents cannot see history), assignee `subagent:<label>`. The subagent claims, works, comments, and closes its own task via the `board` tool. Several ready tasks with no dependency between them can run in parallel - that is exactly what the ready queue is for.
- **Human**: decision, approval, credentials, or anything irreversible → set status `blocked`, assignee `human:<name>`, comment what you need, and notify via `message` (see `human-approval`).
- **Yourself**: sequential or context-heavy work.

## Board autonomy (per-project consent)

Projects can enable Autonomy in the Tasks panel (consent stored in
`.navin/board/settings.json`). When Runtime Context reports it enabled:

- **Chain ready tasks** inside the run you were invited into (Run agent,
  /board task, /forge, /cruise, /mission, or a loop cron) without asking the
  user between cards. Stop and notify only on `blocked`.
- **Git isolation is automatic**: `board claim` creates and switches to an
  isolated `navin/task-<id>` branch and records it on the task. Commit your
  work there; never commit to the branch the user was on.
- **PR on done is automatic**: moving a task to `done` pushes the branch and
  opens the pull request (the PR URL lands on the task). Do not merge PRs
  yourself; merging stays with the human.
- **GitHub issues**: `board sync_github` imports open GitHub issues as tasks;
  `board sync_github task_id=<id>` creates the mirror issue for one task.
  Done tasks with a linked issue close it automatically.
- Destructive git operations (force-push, hard reset, deletes) still require
  explicit user approval, autonomy or not.

When autonomy is off, work only the task the user named and ask before
claiming more.

## Loop mode

To process the board continuously, create a cron job bound to this session (see `cron` tool) with a message like: "Process the project board: `board next`, claim the top ready task, execute it or spawn a subagent, comment the evidence, move it to done or review, then stop. Report only if a human is blocked." Prefer intervals of 15+ minutes; the run defers automatically while the session is busy.

Each cycle is one task, not a marathon: claim, execute, verify, comment, move, stop. Stop the cycle early when `next` reports nothing ready, and only notify a human when a decision or a blocker actually needs them.

## Anti-patterns

- Picking a task by scanning the list instead of asking `next`
- Encoding execution order as priorities instead of `depends_on`
- Marking `done` without verification evidence, or claiming several tasks at once
- Working on something with no board trace when a board exists
- Duplicating tasks instead of commenting the existing one
- Deleting human-created tasks (comment and let humans delete)
- Notifying humans on every tick of a loop - only signal decisions and blockers
