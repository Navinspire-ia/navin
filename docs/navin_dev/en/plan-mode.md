# Plan Mode & Mission Ledger

Navin's Plan Mode is the orchestrator that decides how the agent plans, delegates, verifies, and recovers. It is the quality core of a long run: not a chat-only todo list, but a coded Task Ledger plus Progress Ledger on the project board.

Inspiration: Magentic-One (facts, missing info, progress, local replan, pause) and Deep Agents (structured plan, isolated sub-agents, skills, MCP). Implementation is native on Navin's board and AgentLoop. There is no LangGraph or Deep Agents dependency.

## Orchestrator protocol

On every step (`/forge`, `/cruise`, `/mission`):

1. Read the ledger (`board` `ledger_get`) and `board next`. Never guess the queue.
2. Choose one ready step (or spawn independent ready steps in cruise/mission).
3. Act with Navin tools.
4. Verify according to step `validation` (`test` / `lint` / `verify` / `manual` / `none`).
5. Write `ledger_progress` with evidence.
6. Decide: next | retry (cap `max_retries`) | `ledger_replan` (local) | `ledger_pause`.
7. Never mark `done` without evidence when validation is `test`, `lint`, or `verify`.

If `missing_info` blocks the step, pause and ask the human. Do not invent facts.

Invariants:

- One source of truth: `.navin/board/mission.json` plus the board. No phantom plan that lives only in chat.
- A claimed `done` is a board tool call plus evidence.
- Replan locally first. Rewrite the whole plan only when the goal itself changed, and say so in `history.reason`.
- A sub-agent gets an isolated brief (role, goal, paths, constraints, done-when). It does not inherit the parent transcript.
- `/blueprint` never mutates code, installs packages, or runs exec tests.

## Levels

| Intent | Command | Behavior |
| --- | --- | --- |
| Plan only | `/blueprint` | Goal analysis + Task Ledger + board tasks. **No** code execution. |
| Plan then build | Build button → `/forge` | Execute the plan with Progress Ledger per step. |
| Autopilot | `/cruise` | Plan, execute, test, detect stalls, local replan until done or paused. No Build wait. |
| Long mission | `/mission` | Multi-turn durable goal + ledger + checkpoints + resume + final report. |

Composer UI modes stay `plan` / `agent`. `/cruise` and `/mission` set composer mode to Agent. Model routing: `/cruise` → `dev`, `/mission` → `deep`.

In Plan composer mode only read tools and planning surfaces (`board`, `ask_user`, `set_composer_mode`) are live. `spawn(action="results")` is a read. `spawn(action="start")`, `exec`, and writes are refused until Agent (Build or `set_composer_mode`).

## Data model

Persisted at `.navin/board/mission.json` next to `board.json`. Schema version 1.

**Task Ledger** (what we know, what we still need, the plan):

- `goal`, `status` (`draft` | `running` | `paused` | `blocked` | `done` | `failed`)
- `version`, `constraints[]`, `facts[]`, `missing_info[]`, `acceptance_criteria[]`
- `steps[]`: board task ids plus `depends_on`, `agent`, `acceptance`, `validation`, `retry_count`, `evidence`
- `history[]`: `{version, reason, at, actor, changes}`
- `paused_at`, `pause_reason` (`human` | `budget` | `stall` | `loop` | `manual`)

**Progress Ledger** (where we are):

- `current_step_id`, `last_result_ok`, `last_evidence`, `real_progress`
- `loop_detected`, `stall_count`, `replan_needed`, `recent_action_fingerprints[]`
- `budget`: `max_retries` (3), `max_replans` (5), `max_stalls` (3), optional token/cost caps
- `next_actor`: `main` | `subagent:...` | `human`

There is one ledger per project. `ledger_init` refuses to overwrite a live mission unless `replace=true`.

## Board

Board tasks remain the executable steps. Optional fields:

- `acceptance` - what "done" means for that card
- `validation` - `test` | `lint` | `verify` | `manual` | `none`
- `retry_count`, `max_retries`, `agent`, `evidence`

`depends_on` drives the ready queue (`navin/board/plan.py`). A local replan resets the failed step and its dependents to `planned` and leaves completed upstream steps alone, so the ready queue will not start downstream work until the failed step is done again.

Hard gate: marking `done` with `validation` in `test|lint|verify` (or with non-empty `acceptance`) is **rejected** without `evidence`. For agents, prose evidence is not enough: a recorded green `test_run` / `verify` / lint run must exist.

## Runtime

Each turn, Runtime Context injects a short board digest plus mission lines (goal, version, stall, pause, protocol reminder) via `board_digest`.

- If the ledger is `paused`, the lines say so and tell the agent to wait for a human.
- `ledger_progress` can debit tokens and auto-pause on budget, stall, or loop.
- `/forge`, `/cruise`, and `/mission` auto-checkpoint at turn start; `/cruise` and `/mission` also checkpoint before destructive work or a replan.
- Repeated action fingerprints set `loop_detected` and eventually force a local replan or a HITL pause.

## Pause / resume / manual edit

- Agent: `board` `ledger_pause` / `ledger_resume` / `ledger_update`
- Human: chat plan panel Pause / Resume mission, or board API `pause_mission` / `resume_mission` / `update_mission`
- Every human edit bumps `version` and appends `history` with `actor=human`

## Checkpoints and multi-session resume

`/mission` also creates or updates `/goal` so the objective survives across chats. Combined with `/checkpoint` and the Project Home resume seed, a later session can reload the same ledger, see `current_step_id`, and continue. Close a finished mission with `ledger_update status=done` (and `update_goal complete`) before starting a different one.

## Sub-agents

`spawn` briefs must copy the step's `acceptance` and `validation`. Required brief fields: Role, Goal, Context paths, Constraints, Done when, Do not. The parent merges evidence before `done` / `ledger_progress`. Sub-agents do not see the parent transcript.

## Chat panel

`ThreadPlanPanel` shows `goal`, `v{version}`, ledger status, stall / loop / budget / paused badges, and the last `history` reason. Build still hands `/blueprint` off to `/forge`. When a mission is live, Pause and Resume mission call the board API without replacing Build.

`session_plan` attaches those ledger fields so the panel does not need a second fetch.

## Equivalence (inspiration → Navin)

| Capability | Source | Navin primitive |
| --- | --- | --- |
| Structured todos / plan | Deep Agents | Board tasks + `mission.json` |
| Isolated sub-agents | Deep Agents | `spawn` + project agents |
| Skills / MCP | Deep Agents | Skills + MCP tools |
| Durable state / resume | LangGraph | `mission.json` + `/checkpoint` + `/goal` |
| Pause / resume / HITL | LangGraph | ledger status + panel + `next_actor=human` |
| Controlled loops / replan | Magentic | Progress Ledger + `ledger_replan` |
| Facts / missing info | Magentic | `facts[]`, `missing_info[]` |

## Examples

```text
/blueprint Add GitHub OAuth without breaking Google Auth
# review the plan panel → Build

/forge Implement the approved plan

/cruise Ship dark mode end to end with tests

/mission Migrate auth to Supabase providers across the monorepo
```

## Related

- [Modes](./modes.md)
- [Commands](./commands.md)
- [Board Autonomy](./board-autonomy.md)
- [Skills](./skills.md)
- [Code agent](./code-agent.md)
