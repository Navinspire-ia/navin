# Team module — Overview

The **Team** module (sidebar → **Team**, route `#/team`) is a chief-of-staff and organization-design desk. The agent designs company and team structures from strategy, writes role sheets and RACI matrices, builds hiring plans and interview kits, cascades OKRs, prepares ops reviews — and can **spawn a virtual team of AI agents**, each with a role and a deliverable, to execute a mission in parallel.

## The interactive organization

The top of the view shows your **org chart**: members with an avatar, a name, a role, a specialty, and their key skills, laid out by reporting line (top level first, then each manager's reports below). It ships with a starter organization — Atlas (Chief of Staff) and six leads: Forge (Engineering), Nova (Marketing), Vector (SEO), Compass (Sales), Mentor (HR & Talent), Scribe (Documents).

- **Call** on a member card opens the chat and routes the conversation to that member: their workflow command (`/forge`, `/campaign`, `/seo`, `/leads`, `/studio`, `/team`…) plus their role, specialty, and skills as the persona.
- **Add member / edit / remove**: build your own organization — name, role, specialty, emoji avatar, workflow command, skills, and who they report to. Stored server-side (`~/.navin/webui/team-roster.json`), shared by every chat.

### Calling members from any chat

Type `@` in any composer: your team members appear first in the mention palette (avatar + role). Picking one rewrites the message so it runs as that member — e.g. `@nova launch a campaign for our new plan` becomes `/campaign You are Nova, Marketing Lead… launch a campaign for our new plan`. Members, CLI apps, and MCP servers share the same `@` palette.

## How it works

1. Open **Team** in the sidebar.
2. (Optional) Type a **brief** at the top: your company, stage, headcount, goal. It is attached to every action.
3. Pick an action card in one of the four groups — **Design**, **Staff**, **Run**, **Grow** (see [Actions](./actions.md)).
4. The chat opens and `/team` is sent automatically with the action's specification and your brief.
5. The agent works and saves every artifact as files under `org/` or `team/` in the workspace.

Direct usage in any chat:

```
/team design the organization for a 12-person SaaS startup moving from founder-led sales
/team spawn a virtual team to produce our investor data room: analyst, writer, reviewer
```

## The `/team` command

| | |
| --- | --- |
| Command | `/team [mission\|org brief]` |
| Lifecycle | Agent workflow (runs a full agent turn) |
| Skills preloaded | `org-designer`, `virtual-team-builder`, `multi-agent-orchestration`, `task-planner`, `recruitment-agent`, `job-description-writer`, `candidate-screening`, `kpi-reporter`, `human-approval` |
| Output | Org charts (Mermaid), role sheets, RACI, rituals, hiring plans, OKRs, review packs — saved in the workspace |

## The virtual AI team

The distinctive capability: the agent can staff a mission with **real subagents**, not just documents.

1. It derives 2-5 roles from the mission, each with exactly one deliverable and an exact output path.
2. It writes a complete brief per agent (mission, context, skills to apply, boundaries) — subagents do not share the parent's context.
3. It spawns them with the subagent tool, in parallel or as a pipeline (research → write → review).
4. It tracks the roster in `team/roster.md`, verifies every deliverable against the brief, and merges the results itself.

Orchestration patterns available: parallel specialists, pipeline, producer + critic, manager + squad. Teams are capped at 5 concurrent agents; beyond that the agent asks you first.

## Design principles the agent applies

- **Structure follows strategy** — the org is derived from outcome streams and workload, never copied from a template.
- **One owner per outcome** — no role without owned outcomes and KPIs; no outcome with two owners.
- **Minimal viable org** — the smallest structure that works, plus a growth path with metric-based hiring triggers.
- **Explicit decision rights** — RACI per key process, escalation paths, no orphan decisions.

## Continuous operation

Combine with Navin's autonomy features:

- `/goal keep the OKR tracker up to date and flag at-risk key results weekly` — a sustained goal.
- Cron jobs for recurring ops review packs and team health pulses.
- `/pilot` to route heavy design work and quick updates to different models.

## Tips

- Give real context in the brief: stage, headcount, revenue, what's breaking — the org design gets dramatically better.
- Chain the groups in one chat: design → role sheets → hiring plan → interview kits.
- For missions, prefer the virtual AI team card when there are ≥3 separable workstreams; solo execution is faster below that.
