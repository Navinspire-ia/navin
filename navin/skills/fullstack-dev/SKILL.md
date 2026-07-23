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

1. **Plan** — Restate the goal. For non-trivial tasks, write a short numbered plan (steps, files touched, risks) before editing anything. Keep the plan visible and update it as you go.
2. **Investigate** — Read the relevant files with `read_file` / `list_dir` before changing them. Use `exec` with `rg` for code search. Never guess an API that exists in the repo — check it.
3. **Code** — Make focused edits with `edit_file` / `write_file`. Follow the project's existing conventions (imports, formatting, naming). Small commits of change, one concern at a time.
4. **Execute** — Run the project with `exec`:
   - Frontend: `npm/bun/pnpm install`, `npm run dev` (Vite serves on a port — report the URL), `npm run build` to validate.
   - Backend: `python -m venv .venv && .venv/bin/pip install ...`, `uvicorn app:app --reload`, `node server.js`, etc.
   - Long-running dev servers: start them in background (`nohup ... &` or the exec session) and verify with `curl http://127.0.0.1:<port>`.
5. **Verify** — Run linters/build/tests after edits. Reproduce the reported bug before fixing, and re-run the reproduction after fixing. Show the actual output, never claim success without evidence.
6. **Deliver** — Summarize what changed, how to run it, and what remains.

## Scaffolding recipes

- **Vite + React + TS**: `npm create vite@latest <name> -- --template react-ts && cd <name> && npm install`
- **FastAPI**: create `app.py`, `requirements.txt` (fastapi, uvicorn), venv, then `uvicorn app:app --reload --port 8000`
- **Express**: `npm init -y && npm i express`, `node server.js`
- **SQLite**: create schema with the `db_query` tool or `sqlite3` CLI; keep the .db file inside the project folder.

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

## Guardrails

- Never run destructive commands (rm -rf outside the project, DROP TABLE) without explicit confirmation.
- Keep secrets out of code; use `.env` files and reference them.
- If a dev server port is already in use, find the process first instead of changing ports blindly.
