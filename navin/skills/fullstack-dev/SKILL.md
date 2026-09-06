---
name: fullstack-dev
description: Build, run, and debug frontend, backend, and database projects end to end in the workspace (plan, investigate, code, execute, verify). Use for Dev mode sessions, scaffolding apps, fixing bugs, and running dev servers.
metadata: {"navin":{"emoji":"🧑‍💻","category":"devops"}}
---

# Full-Stack Developer

Work like a senior engineer inside the workspace: plan before coding, investigate before changing, verify after every step. This is the core skill behind Dev mode sessions.

## When to use

- The user opens a Dev session or asks to build/modify an application (frontend, backend, API, database).
- The user reports a bug, a failing build, or unexpected behavior in a project.
- The user asks to scaffold a new project (Vite/React, Node/Express, Python/FastAPI, etc.).

## Operating loop

1. **Plan** - Restate the goal. For non-trivial tasks, write a short numbered plan (steps, files touched, risks) before editing anything. Keep the plan visible and update it as you go. System architecture in that plan uses `archify`.
2. **Investigate** - Read the relevant files with `read_file` / `list_dir` before changing them. Use `exec` with `rg` for code search. Never guess an API that exists in the repo - check it.
3. **Code** - Make focused edits with `edit_file` / `write_file`. Follow the project's existing conventions (imports, formatting, naming). Small commits of change, one concern at a time.
4. **Execute** - YOU run the project with `start_app` / `open_preview` / `exec` (never ask the user):
   - Prefer `open_preview(kind="web")` or `start_app` then `open_preview` - covers vite/next/docker/rails/…
   - Long-running processes: `exec` with `background=true`, then open Preview.
5. **Verify (mandatory)** - After meaningful code work, run `verify action=check` (or `test_run` + `lint` if verify is unavailable). Do not claim done on a failing suite. If there are no tests yet for new behavior, add a minimal test before delivery when the stack supports it.
6. **Preview (mandatory for UI / runnable apps)** - Call `open_preview`. The user owns Preview (URL, refresh, open external, test). You start the stack. Never tell them to run npm.
7. **Review (light, mandatory before saying done)** - Quick pass with `critic-reviewer` criteria (or `code_review` for larger diffs): correctness, obvious bugs, missing error handling, secrets. Fix blockers before the final summary.
8. **Deliver** - Summarize what changed, how to re-run, test/preview status, and what remains.

## Delivery gate (do not skip)

Before ending a turn that built or changed a site, web app, API UI, or other runnable app:

1. App is **running** locally (or `open_preview` / `start_app` just started it).
2. `open_preview` was called so the user sees it in **Preview**. Never ask the user to start the server.
3. `verify action=check` was run. Product-UI errors (em dashes, fake buttons, missing framer-motion, missing-three-stack, missing official DS) are **blockers**.
4. You personally exercised the main flows in Preview (or via curl/browser tools): login/shell loads, primary nav works, dashboard shows **real wired data or honest empty states**, not cardboard.
5. Skills **`ui-ux-pro-max`** + **`make-interfaces-feel-better`** were actually followed (design system + motion), not name-dropped.
6. Critic pass: no dead controls, no "Coming soon" as a feature, no console.log/alert buttons.

### Forbidden "done" (instant fail)

- Dashboard / page that blank-loads, errors in console, or never fetches its API.
- Buttons / nav items with empty handlers, `alert()`, `console.log`, or "Coming soon".
- Lorem ipsum, dummy/fake/mock labels left in the UI the user will see.
- Unicode em dash (U+2014) or en dash (U+2013) anywhere in UI copy, i18n, markdown UI, comments shipped to the user. Use `-` or rephrase.
- Web UI without `framer-motion` (or `motion`) installed and used for real enter/section motion.
- Dev web UI without `three` + `@react-three/fiber` + `@react-three/drei` and a designed scene (not wallpaper).
- New web UI that defaults to Tailwind / shadcn / Chakra / Ant / a homemade kit instead of MUI, Fluent, or Carbon.
- Claiming "CRM complet" when CRUD routes, API, and persistence are missing.

Skipping Preview or tests is only OK if the user asked for code-only / no-run, or there is no UI and no runnable entrypoint.

## Official design system (mandatory, Navin Code)

Greenfield web UI uses **one of these three**. Never a fourth by default (no Tailwind kit, no shadcn, no Chakra, no Ant, no homemade components as the system).

If the user did not name one, call `ask_user` and stop scaffolding UI until they pick:

- `a` Google Material (recommended): `@mui/material` + Emotion
- `b` Microsoft Fluent: `@fluentui/react`
- `c` IBM Carbon: `@carbon/react` (org [carbon-design-system](https://www.npmjs.com/~carbon-design-system))

Skip takes Google. If `package.json` already has one of the three, lock that one. Do not migrate a running app off its official DS. A catalog app the user asked to install (CRM, saas-starter, …) keeps that template's stack.

| Choice | Install (Vite + React + TS, not CRA) | Root |
|--------|--------------------------------------|------|
| Google | `npm install @mui/material @emotion/react @emotion/styled @mui/icons-material framer-motion three @react-three/fiber @react-three/drei` | `ThemeProvider` from MUI |
| Microsoft (Fluent / Windows) | `npm install @fluentui/react @fluentui/react-icons framer-motion three @react-three/fiber @react-three/drei` | Fluent root / `ThemeProvider` |
| IBM Carbon | `npm install @carbon/react @carbon/styles @carbon/icons-react framer-motion three @react-three/fiber @react-three/drei` | Carbon styles + components. Import `@carbon/styles/css/styles.css` once. |

Do not run `create-react-app` or the Fluent CRA template. Navin scaffolds Vite (or Next if the user named Next), then installs the chosen DS.

`ui-ux-pro-max` MASTER.md maps color/type/density **onto** that ThemeProvider. It does not replace MUI / Fluent / Carbon with a custom CSS kit.

Marketing / launch pages still add `lenis embla-carousel-react` on top of the locked DS (`ui-ux-pro-max` super render). Prefer vendor icons (MUI / Fluent / Carbon) over a second icon set.

## Scaffolding recipes

- **Vite + React + TS (default web)**:
  ```bash
  npm create vite@latest <name> -- --template react-ts
  cd <name> && npm install
  ```
  Then install **only** the chosen official DS from the table above (includes `framer-motion` + the Three.js stack on Google, Fluent, and Carbon alike). Never add Tailwind as the default design system.
- **Next.js**: scaffold only if the user named Next, then the same official DS + `framer-motion` + `three` + `@react-three/fiber` + `@react-three/drei`. Same `lenis` / Embla extras on a marketing site.
- **FastAPI**: create `app.py`, `requirements.txt` (fastapi, uvicorn), venv, then `uvicorn app:app --reload --port 8000`
- **Express**: `npm init -y && npm i express`, `node server.js`
- **SQLite**: create schema with the `db_query` tool or `sqlite3` CLI; keep the .db file inside the project folder.

## Web UI defaults (mandatory)

For **any website / frontend UI** work (landing, SaaS shell, dashboard, portfolio, CRM, e-commerce):

1. Lock Google / Microsoft / IBM (ask if missing). Install that official DS. Then load **`ui-ux-pro-max`** (MASTER.md tokens mapped onto the vendor theme). Persist `design-system/<project>/MASTER.md`.
2. Load **`make-interfaces-feel-better`** for polish (radius, shadows, stagger, press scale) inside that DS.
3. Ensure **`framer-motion`** is installed and used for section/hero/page motion (2-3 intentional motions minimum).
4. Ensure **`three` + `@react-three/fiber` + `@react-three/drei`** are installed. Run ui-ux-pro-max `search.py --stack threejs` before the scene. Ship a designed 3D layer (hero, product, or spatial chrome) with PBR, lights, shadows, and OrbitControls or a constrained camera. Never wallpaper. Still fallback when `prefers-reduced-motion`.
5. Every primary button and nav item must do a real thing (route, mutation, dialog with working form). No decorative controls.
6. Dashboards: wire to the real API/store; if empty, show a designed empty state with a working CTA - never a broken blank page.
7. Copy: **never** em/en dashes (U+2014 / U+2013). Plain `-` only.
8. Start the app, `open_preview`, click through the happy path yourself before saying done.

## Databases

Use the `db_query` tool for SQL against SQLite files in the workspace and named connections (PostgreSQL, Supabase, MySQL, MariaDB) configured under `tools.database.connections` in `~/.navin/config.json`:

```json
{
  "tools": {
    "database": {
      "connections": {
        "app": {"engine": "postgres", "url": "postgresql://user:pass@host:5432/db"},
        "local": {"engine": "sqlite", "path": "myapp/data.db", "allowWrites": true}
      }
    }
  }
}
```

Read-only by default; writes need `allowWrites: true` on the connection. For schema work prefer migrations committed to the project over ad-hoc DDL.

## Delegation

For large tasks, split work with `spawn_subagent`: one subagent investigates or builds a module while you continue on another. Use the critic-reviewer skill before delivering risky changes.

For Expo / React Native / Flutter workspaces, load the `mobile-dev` skill and use the `mobile` tool (`detect`, `doctor`, `run`, `logs`) instead of guessing packager commands.

## Guardrails

- Never run destructive commands (rm -rf outside the project, DROP TABLE) without explicit confirmation.
- Keep secrets out of code; use `.env` files and reference them.
- If a dev server port is already in use, find the process first instead of changing ports blindly.
