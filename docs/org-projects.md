# Organization shared projects

Team organizations can create **shared projects** with specialty ownership zones,
an event feed for notifications, and a PM cockpit API.

## Prerequisites

Run SQL in this order after `organizations`:

1. `site/supabase/2026-08-02-organizations.sql`
2. `site/supabase/2026-08-02-org-projects.sql`

## Concepts

| Concept | Meaning |
|---|---|
| `org_projects` | Shared project bound to an org (repo URL, default branch) |
| `org_project_members` | Roles: `lead` (PM), `contributor`, `observer` + specialty label |
| `org_ownership_zones` | Path globs per member (`webui/**`, `navin/**`, …) |
| `org_project_events` | Append-only feed (PR, conflict, ticket, report, …) |

## Anti merge conflicts

1. Assign each contributor a specialty and one or more path globs.
2. Before commit / PR, check ownership of the changed paths in the project cockpit.
3. Paths with **no owner** are allowed (greenfield).
4. Paths owned by someone else are **blocked** unless a project lead forces the change.
5. Prefer one board ticket → one branch `feat/<ticket-id>-slug`.

Local helper (Python): `navin.collab.ownership`.

Create projects, members, ownership zones and the activity feed from the Team cockpit on navin.live (logged-in session).

## Suggested Team 5 layout

| Specialty | Zones (example) |
|---|---|
| Frontend | `webui/**`, `site/src/components/**` |
| Backend | `navin/**`, `site/src/app/api/**` |
| Data / SQL | `site/supabase/**` |
| QA / Security | `tests/**` |
| Project lead | no code zones (lead role + cockpit) |

## PM / direction

- Members with `project_role = lead` (or org `admin`) manage members and zones.
- Org `viewer` can read project events when granted project membership as `observer`.
- Open the project cockpit for a live activity stream.
- Emit `report` / `blocker` / `conflict` events from CI, git hooks, or the agent.
