---
name: org-designer
description: Design company and team organizations — structures, roles, RACI, governance, rituals, and scaling plans. Use for any "build/reshape my team or org" request.
metadata: {"navin":{"emoji":"🏛️","category":"operations"}}
---

# Organization Designer

## Overview

Design organizations the way great COOs do: start from strategy and workload, derive the structure, define crisp roles with clear ownership, then wire the operating system (decision rights, rituals, KPIs). Structure follows strategy — never the reverse.

## Design sequence

1. **Mission & strategy** — what must this company/team achieve in the next 12-18 months? List the 3-5 outcome streams.
2. **Work mapping** — inventory the recurring work and projects per stream; estimate load (FTE) honestly.
3. **Structure choice** — pick the lightest structure that fits: functional, cross-functional squads, pods, matrix. Note the trade-offs chosen.
4. **Roles** — one sheet per role (see template). Rule: every outcome has exactly one owner.
5. **Decision rights** — RACI per key process + explicit escalation paths. Flag decisions that today have zero or multiple owners.
6. **Operating rhythm** — rituals: daily/weekly syncs, monthly business review, quarterly OKR setting. Each ritual: purpose, attendees, cadence, output, max duration.
7. **Scaling plan** — hiring order with triggers ("hire X when metric Y passes Z"), not dates.

## Role sheet template

```markdown
# {Role title}
- Mission: one sentence — why this role exists
- Outcomes owned: 3-5 measurable outcomes
- Key activities: the recurring work
- Interfaces: who they work with, for what
- Skills required: must-have / nice-to-have
- KPIs: 2-4 numbers this role moves
- Seniority & reporting line
- First 90 days: what success looks like
```

## Deliverables

Save to the workspace as files:
- `org/org-chart.md` — structure with reporting lines (Mermaid diagram)
- `org/roles/<role>.md` — one sheet per role
- `org/raci.md` — RACI table for the key processes
- `org/rituals.md` — the operating rhythm
- `org/hiring-plan.md` — sequenced hiring with triggers

## Rules

- Small first: propose the minimal viable org, then the growth path — never a big-company org for a small team.
- No role without owned outcomes and KPIs; no outcome with two owners.
- Spans of control: flag managers with >8 direct reports or 1.
- Make trade-offs explicit ("we chose speed over redundancy here").
