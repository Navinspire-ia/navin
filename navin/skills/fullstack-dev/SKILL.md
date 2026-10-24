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
5. **Verify (mandatory)** - Build first: run the unit's build (`npm run build`, `cargo build`, `project action=build`) after the last edit, every time, and fix a red build before anything else. Then run `verify action=check` (or `test_run` + `lint` if verify is unavailable). Do not claim done on a failing suite. If there are no tests yet for new behavior, add a minimal test before delivery when the stack supports it.
6. **Preview (mandatory for UI / runnable apps)** - Call `open_preview`. The user owns Preview (URL, refresh, open external, test). You start the stack. Never tell them to run npm.
7. **Review (light, mandatory before saying done)** - Quick pass with `critic-reviewer` criteria (or `code_review` for larger diffs): correctness, obvious bugs, missing error handling, secrets. Fix blockers before the final summary.
8. **Deliver** - Finish every part of the request first, then summarize what changed, how to re-run, test/preview status, and remarks (risks, observations). Not a to-do list: a part left undone must be genuinely blocked, with its reason.

## Delivery gate (do not skip)

Before ending a turn that built or changed a site, web app, API UI, or other runnable app:

1. App is **running** locally (or `open_preview` / `start_app` just started it).
2. `open_preview` was called so the user sees it in **Preview**. Never ask the user to start the server.
3. The project was **built** after the last edit (`npm run build`, `cargo build`, `project action=build`...) and the build is green. Always build before delivering, even for a small change. `verify action=check` was run. Product-UI errors (em dashes, fake buttons, missing framer-motion, missing-three-stack, missing official DS) are **blockers**.
4. You personally exercised the main flows in Preview (or via curl/browser tools): login/shell loads, primary nav works, dashboard shows **real wired data or honest empty states**, not cardboard.
5. Skills **`ui-ux-pro-max`** + **`make-interfaces-feel-better`** were actually followed (design system + motion), not name-dropped.
6. Critic pass: no dead controls, no "Coming soon" as a feature, no console.log/alert buttons.
7. New app with users: in Preview, sign up -> sign in -> open a protected page -> change password -> sign out -> sign in with the new password all work; forgot/reset works through the dev mailbox; the API answers 401 to an unauthenticated curl.
8. Every page sits in the app shell and is built from the shared kit (no page-local styling).
9. Healthy project rules hold: lists paginated server-side with the standard shape, one error shape, `/health` answers, `lint` / `typecheck` / `test` / `build` pass, README and `.env.example` complete.
10. The chosen database (local SQLite by default) is migrated and seeded, and `/health` reaches it. Optional modules the client chose work end to end (payment in Stripe test mode with the webhook, file upload and download, French / English / Arabic with right-to-left).

### Forbidden "done" (instant fail)

- Dashboard / page that blank-loads, errors in console, or never fetches its API.
- Buttons / nav items with empty handlers, `alert()`, `console.log`, or "Coming soon".
- Lorem ipsum, dummy/fake/mock labels left in the UI the user will see.
- Unicode em dash (U+2014) or en dash (U+2013) anywhere in UI copy, i18n, markdown UI, comments shipped to the user. Use `-` or rephrase.
- Web UI without `framer-motion` (or `motion`) installed and used for real enter/section motion.
- Dev web UI without `three` + `@react-three/fiber` + `@react-three/drei` and a designed scene (not wallpaper).
- New web UI that defaults to Tailwind / shadcn / Chakra / Ant / a homemade kit instead of MUI, Fluent, or Carbon.
- Claiming "CRM complet" when CRUD routes, API, and persistence are missing.
- A new app with users that has no working sign in / sign up / password reset, or a login form that does not authenticate against the backend.
- A "Continue with Google" button that does nothing (show it only when the provider is configured).
- Pages styled on their own (inline colors, one-off buttons, local CSS) instead of the app's shared kit and the chosen template.

Skipping Preview or tests is only OK if the user asked for code-only / no-run, or there is no UI and no runnable entrypoint.

## Official design system (mandatory, Navin Code)

Greenfield web UI uses **one of these three**. Never a fourth by default (no Tailwind kit, no shadcn, no Chakra, no Ant, no homemade components as the system).

For a new app or a front-end redesign the user asked for, if the user did not name one, call `ask_user` and stop scaffolding UI until they pick:

- `a` Google Material (recommended): `@mui/material` + Emotion
- `b` Microsoft Fluent UI React v9: `@fluentui/react-components`
- `c` IBM Carbon: `@carbon/react` (org [carbon-design-system](https://www.npmjs.com/~carbon-design-system))

Skip takes Google. If `package.json` already has one of the three, lock that one. Do not migrate a running app off its official DS. An existing app (its `package.json` already committed with a UI) keeps the stack it has, official or not: new screens reuse its components and styles, and you ask no design-system question and add no kit, motion or 3D library it lacks. A catalog app the user asked to install (CRM, saas-starter, …) keeps that template's stack.

| Choice | Install (Vite + React + TS, not CRA) | Root |
|--------|--------------------------------------|------|
| Google | `npm install @mui/material @emotion/react @emotion/styled @mui/icons-material framer-motion three @react-three/fiber @react-three/drei` | `ThemeProvider` from MUI |
| Microsoft (Fluent UI v9) | `npm install @fluentui/react-components @fluentui/react-icons framer-motion three @react-three/fiber @react-three/drei` | `FluentProvider` with `webLightTheme` / `webDarkTheme` |
| IBM Carbon | `npm install @carbon/react @carbon/styles @carbon/icons-react framer-motion three @react-three/fiber @react-three/drei` | Carbon styles + components. Import `@carbon/styles/css/styles.css` once. |

Do not run `create-react-app` or the Fluent CRA template. Navin scaffolds Vite (or Next if the user named Next), then installs the chosen DS.

`ui-ux-pro-max` MASTER.md maps color/type/density **onto** that ThemeProvider. It does not replace MUI / Fluent / Carbon with a custom CSS kit.

Marketing / launch pages still add `lenis embla-carousel-react` on top of the locked DS (`ui-ux-pro-max` super render). Prefer vendor icons (MUI / Fluent / Carbon) over a second icon set.

## Greenfield fullstack baseline (mandatory for a new app with users)

Applies when the user asks for an application built from scratch that people log into: SaaS, CRM, ERP, dashboard, back-office, portal, marketplace, internal tool. Not for a landing page, a script, an API-only service the user scoped as such, or a catalog template (it keeps its own stack).

Build in this order, so the very first Preview already looks and behaves like a real product:

1. **Official template of the locked design system.**
2. **One shared UI kit and app shell**, used by every page.
3. **Authentication end to end** - the first feature.
4. Business features, built on 2 and 3.

### 1. Start from the design system's official template

The look and the layout come from the vendor, not from improvisation:

- **Google (MUI)**: start from MUI's official templates (mui.com/material-ui/getting-started/templates: dashboard, sign-in, sign-in-side, sign-up). Bring their theme customizations, side menu, app bar and sign-in / sign-up cards into `src/`, then brand them. Toolpad Core (`@toolpad/core`: `AppProvider`, `DashboardLayout`, `PageContainer`, `SignInPage` with Google / GitHub / credentials providers) is a good fit for dashboards.
- **IBM (Carbon)**: Carbon UI Shell (`Header`, `HeaderName`, `HeaderGlobalBar`, `SideNav`, `Content`, `Theme`) for the shell; `@carbon/ibm-products` (`PageHeader`, `Tearsheet`, `CreateFullPage`...) for page-level patterns when they fit.
- **Microsoft (Fluent UI v9)**: `FluentProvider` + `webLightTheme` / `webDarkTheme`; `NavDrawer` / `Drawer` + `Toolbar` for the shell; `Card`, `Field`, `Input`, `Button`, `MessageBar` for the auth screens.

Not sure of a component's current props? Read the package's docs or README once (`web_fetch`) before writing it. Never invent an API.

### 2. One shared UI kit, used by every page

```
src/
  theme/        tokens: palette, typography, spacing, radius, shadows (the ONLY place colors and fonts are defined) + light/dark
  components/   AppShell, PageHeader, DataTable, FormField, FormActions, ConfirmDialog,
                EmptyState, LoadingState, ErrorState, StatusChip, UserMenu, Toast host...
  layouts/      AuthLayout (centered card), AppLayout (shell + outlet)
  features/<domain>/   pages, hooks and API calls of one domain (contacts, deals, invoices...)
  lib/api.ts    ONE API client: base URL, auth header, 401 -> refresh or redirect to sign in, typed errors
  routes.tsx    public routes, protected routes, role-gated routes
```

Rules, for every page and every later change:

- Pages compose kit components. No inline hex colors, no page-local CSS, no one-off buttons or cards.
- The second time a piece of UI appears, it becomes a kit component; the existing copies switch to it.
- Every list is the kit `DataTable` (search, sort, pagination, empty state, loading). Every form uses `FormField` + one validation pattern (inline errors, disabled submit while saving, toast on result).
- Every page renders inside `AppLayout` with a `PageHeader` (title, breadcrumb, primary action).

### 3. Authentication end to end (the first feature)

Frontend screens (from the template's sign-in / sign-up, in `AuthLayout`): Sign in, Sign up, Forgot password, Reset password (from the emailed link), Change password and Profile (in Settings), Sign out from the user menu. Protected routes redirect to Sign in with a return URL; the session survives a reload; navigation hides what the role cannot open.

Backend:

- `users`: unique email, password hash (argon2id or bcrypt - never plain or fast hashes), role (`admin` / `user`), `email_verified`, timestamps.
- Endpoints: `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `POST /auth/refresh`, `GET /auth/me`, `POST /auth/password/forgot`, `POST /auth/password/reset`, `POST /auth/password/change`, `GET /auth/providers`, and `GET /auth/oauth/google/start` + `GET /auth/oauth/google/callback` (Microsoft / GitHub the same way when asked).
- Tokens: short-lived access token + refresh token in an httpOnly, SameSite=Lax cookie (or a server-side session). Every business endpoint requires auth; admin endpoints check the role.
- Rate-limit login and forgot-password; answer "invalid email or password" and "if this email exists, a link was sent" (no account enumeration); enforce a password policy; reset tokens are single-use and expire.
- Emails in development: a dev mailbox (Mailpit in `docker-compose.yml`, or the reset link written to the server log) so forgot / reset is testable locally.

Google sign-in is a real OAuth flow: `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / redirect URI read from `.env`, documented in `.env.example` and in the README (how to create the OAuth client). `GET /auth/providers` tells the UI which providers are configured; the Google button only appears when Google is - never a dead button.

Seed an admin account (its credentials in `.env.example` and the README) and realistic demo data, so every page shows content on first run.

### 4. Pro from the first Preview

Light / dark theme toggle (persisted), responsive layout with a mobile drawer, 404 page and an error boundary, toasts for every mutation, skeleton loading, designed empty states with a working call to action, accessible labels and focus, a page title per route, app name and favicon, a user menu (Profile, Settings, Sign out), a Settings page (profile, change password, theme).

### 5. Healthy project rules (from the first commit)

- **Lists**: pagination, sorting and filtering happen on the server. Every list endpoint takes `page`, `page_size`, `sort`, filters, and answers one shape: `{"items": [...], "total": n, "page": p, "page_size": s}`. The UI keeps page, sort and filters in the URL, so a filtered view is a shareable link. The kit `DataTable` drives these parameters.
- **API**: every input is validated at the boundary (Pydantic, Zod, class-validator, Bean Validation...). One error shape everywhere: `{"error": {"code": "...", "message": "...", "fields": {"email": "..."}}}` with the right status (400 / 401 / 403 / 404 / 409 / 422 / 429 / 500). OpenAPI docs generated from the code (FastAPI `/docs`, `@nestjs/swagger`, springdoc...).
- **Administration**: an admin page to list users, invite one (email with a set-password link), change a role and deactivate an account. Never delete a user.
- **Security**: CORS limited to the frontend origin; security headers (HSTS in production, X-Content-Type-Options, X-Frame-Options or frame-ancestors, a basic CSP); secrets only in `.env`, never in code or git; a complete `.env.example`; uploads limited in size and type, stored outside the web root.
- **Quality**: strict type checking (TypeScript `strict`, mypy / pyright), a linter and a formatter. The project exposes `lint`, `typecheck`, `test` and `build` scripts (package.json, Makefile or pyproject). API tests from day one: at least the authentication flow and one CRUD resource.
- **Docs**: a README with how to run it (one command), every environment variable, the demo accounts, and an architecture diagram (`archify`).
- **Code organisation**: by business domain (`features/` or `modules/<domain>`), with layers: routes / controllers only parse and answer, services hold the business rules, repositories talk to the database. No business logic or SQL in route handlers.
- **Operations**: a `GET /health` endpoint (process up, database reachable); structured logs (JSON in production) carrying a request id, also returned in an `X-Request-ID` header; separate development and production settings, loaded from the environment.

### 6. Optional modules - ask at kickoff

In the same `ask_user` as the design system choice, also ask where the data lives (single choice, default **local SQLite**):

- **Local SQLite** (default when the client does not choose): one file inside the project (`data/app.db`, git-ignored), WAL mode, foreign keys on. Nothing to install.
- **PostgreSQL**: a remote server or managed service (Neon, AWS RDS, Google Cloud SQL, Azure Database...) through `DATABASE_URL`; for development, a `postgres` service in `docker-compose.yml` when Docker is available.
- **Supabase**: its PostgreSQL through `DATABASE_URL` (pooler URL for the server). The app keeps its own auth from section 1 unless the client asks for Supabase Auth; if the file storage module is chosen, Supabase Storage can be its S3-compatible backend.
- **Any other database the client names**, local or remote / managed: MySQL / MariaDB, SQL Server, Oracle, CockroachDB, MongoDB (Atlas or local), Firestore, DynamoDB, Redis as a primary store... The list above is only the usual choices, never a limit.

Whatever the choice: one data layer (ORM / query builder for SQL: Prisma or Drizzle on Node, SQLAlchemy + Alembic on Python, the framework's own elsewhere; the official driver or ODM for document stores: Mongoose, Beanie / Motor...), schema changes committed to the project (migrations for SQL, a script that creates the indexes and validation rules for document stores), a seed script for the demo accounts, the connection string only in `.env` (placeholder in `.env.example`, SSL on for remote servers), and `GET /health` checks the database. Migrations run with the project's `migrate` command; never drop or reset a remote database. If the client chose a remote database but has no connection string yet, ask for it once and meanwhile develop on a local instance of the same engine (docker-compose service or the official emulator), never on SQLite (dialects and features differ).

Then offer these modules (multi-select, none by default). Build only the ones the client picks, and build them completely:

- **Payments (Stripe)**: Checkout Sessions (one-off) or Billing subscriptions, created on the server from server-side prices - never an amount sent by the browser. A webhook endpoint verifies the signature (`STRIPE_WEBHOOK_SECRET`), is idempotent, and is the only thing that marks an order paid or a subscription active. Customer portal for cards and invoices. Test-mode keys in `.env.example`; local webhooks through `stripe listen --forward-to <api>/webhooks/stripe`. Payment buttons appear only when Stripe is configured.
- **Cloud file storage (S3-compatible)**: one storage service behind an interface (AWS S3, Cloudflare R2, Google Cloud Storage interop, Azure Blob through an adapter; MinIO in `docker-compose.yml` for development). Private by default; uploads and downloads through short-lived presigned URLs; size and type checked on the server; a `files` table (owner, key, name, mime type, size, created_at). Never serve user files from the web root.
- **Languages - French, English, Arabic (right to left)**: i18n from the first screen (`react-i18next`, or `next-intl` on Next): one messages file per locale, no hard-coded UI text, a language switcher, the choice persisted. Arabic switches the document to `dir="rtl"` and the design system to RTL (MUI: theme `direction: "rtl"` with the RTL Emotion cache; Fluent v9: `FluentProvider dir="rtl"`; Carbon: `dir="rtl"` on the root). Dates, numbers and currencies through `Intl` with the active locale. The API returns error codes; the UI translates them.

Each chosen module joins the delivery gate: a test payment completes in Stripe test mode and the webhook updates the database; a file uploads, lists and downloads; the app switches to Arabic and every page renders right to left.

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

1. New app or requested redesign: lock Google / Microsoft / IBM (ask if missing). An existing app keeps its own stack: it skips the installs of steps 1, 3 and 4. Install that official DS. Then load **`ui-ux-pro-max`** (MASTER.md tokens mapped onto the vendor theme). Persist `design-system/<project>/MASTER.md`.
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
