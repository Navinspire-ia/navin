# Project Home

Project Home (`#/project`) is the continuity hub for projects that last weeks or months. It answers one question in seconds: **where were we, and what happens next?** Everything on the page reads the same files and APIs as the Code workbench and the agent itself, so what you see is always what the agent sees.

Open it from the sidebar **Project** entry, or directly: `#/project?chat=<session-key>`.

## One brain, every surface

Project Home is a window over the durable project pack scaffolded in every project:

```
<project>/
  .navin/
    board/            board.json, milestones.json, activity.jsonl,
                      settings.json (autonomy consent), mission.json
    continuity/       RESUME.md, DECISIONS.md
  memory/MEMORY.md    durable facts and constraints
  SOUL.md, USER.md    agent persona and user preferences
```

Code, Project Home and every studio (Marketing, SEO, Documents, ...) share this pack, and the agent re-reads it on every turn. Editing here changes what the agent knows on its very next reply.

## Tabs

| Tab | What it shows | Powered by |
| --- | --- | --- |
| **360 Vision** | The full project audit: global health and per-domain scores, API modules, architecture, API explorer, permissions / RBAC, SQL queries, security and performance findings, PDF export | Same `DevProjectAudit` component as Code |
| **Tasks** | The full task board - the exact same kanban as Code: columns, plan strip (ready / blocked / critical path), task detail, dependencies, comments, **Autonomy** toggle, **Sync GitHub**, Run agent | Same `DevBoardPanel` component as Code |
| **Git** | Branch / commit / merge timeline with the relation graph | Git timeline API |
| **Issues** | The repository's GitHub issues (open / closed / all) with author, labels, comment counts, plus **Import to board** and a per-issue **Fix with agent** action | `gh` CLI through the gateway |
| **Evolutions** | Roadmap milestones, execution plan, ready queue, and the activity timeline with actor filters | Same `DevEvolutions` component as Code |
| **Graph** | The dependency metagraph: Files / Packages views, filters, `.metadata` generation | Same `DevMetagraph` component as Code |
| **Resume** | Resume brief, open tasks, session plan, milestones with progress, recent sessions, one-click resume | Board + brain APIs |
| **Brain** | Editable `RESUME.md`, `DECISIONS.md`, `MEMORY.md`, `SOUL.md`, `USER.md`, extracted constraints, drift note | Project brain API |
| **Timeline** | The board activity feed: every human and agent mutation, newest first | `activity.jsonl` |

### Resume

Assembles a **resume seed** from the `RESUME.md` brief, the constraints in `MEMORY.md`, and the top open tasks, then the **Resume** button opens a chat with that seed pre-filled: the agent starts exactly where the project stopped, even after weeks away. Project Home opens on **360 Vision** by default; Resume sits after the operational tabs.

### Tasks

The board is not a copy: it is the same component, the same API and the same live `board_updated` events as the Code workbench. Drag nothing twice - a card moved here is moved everywhere, including for the agent.

From this tab you can also:

- enable **[Board Autonomy](./board-autonomy.md)** (consent dialog: isolated branch per task, PR on done, issues sync);
- **Sync GitHub** to import open issues as tasks;
- **Run agent on board** to hand the ready queue to the agent.

Cards carry the autonomy trail: branch chip, **PR** link and **Issue** link, all clickable.

### Evolutions

Same roadmap / milestones / execution plan / activity view as the Code workbench Evolutions tab: create milestones, see what is ready now, and filter the board activity feed by human, agent or subagent. It reads `milestones.json` and `activity.jsonl` under `.navin/board/`.

### Issues

Lists the repository's GitHub issues through the authenticated `gh` CLI: state filter (open / closed / all), author, labels, comment counts, direct links. **Import to board** creates one task per open issue not on the board yet (deduplicated by issue URL, labeled `github` plus the issue labels). If `gh` is missing or the repo has no GitHub remote, the tab says so honestly instead of showing an empty shell.

Every open issue also has a **Fix with agent** button: one click sends the agent a strict brief - reproduce the problem, fix it on an isolated task branch, run the relevant tests until fully green, commit, open a PR when PR-on-done is enabled, and only then close the issue on GitHub and re-sync the board. The agent is explicitly told to never close an issue whose fix is not tested and committed. The same behaviour without a click is available through the **Fix issues autonomously** consent option described in [Board Autonomy](./board-autonomy.md).

### 360 Vision and Graph

Both embed the Code workbench panels unchanged, so there is exactly one implementation to trust: the audit engine behind **360 Vision** (health scores, AST-backed security/performance findings with confidence + evidence, RBAC map, SQL inventory, PDF export) and the dependency **metagraph** (Files / Packages, impact analysis, `.metadata` generation). See [Graph](./graph.md) for the full graph reference. Tasks, Evolutions and Graph follow the same rule: one component shared with Code.

### Brain

Direct read-write access to the agent's memory files with unsaved-change indicators. Constraints listed under `## Constraints` in `MEMORY.md` are extracted and displayed; the agent receives them on every turn.

## Live sync

- Board mutations (human or agent, any surface) broadcast `board_updated`; every open view refreshes.
- A 15 s polling fallback covers missed events.
- The scaffold is idempotent: opening a project that misses parts of the pack creates them without ever overwriting existing files.

## Related

- [Board Autonomy](./board-autonomy.md) - auto-branch, PR on done, issues sync, consent
- [Plan Mode](./plan-mode.md) - `/blueprint` `/forge` `/cruise` `/mission`
- [Workbench](./workbench.md) - the Code side of the same project
- [Graph](./graph.md) - metagraph reference
