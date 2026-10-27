---
name: app-project-analyzer
description: Phase 1 of App Factory. Analyze a web project before turning it into a mobile or desktop app - frontend framework and version, package manager, build script and output dir, backend, server features, auth, API base URL env vars, existing native folders. Drives `appfactory action=analyze` and verifies its report against the files.
metadata: {"navin":{"emoji":"🔎","category":"development"}}
---

# App Project Analyzer

## When to use

First step of any `/apps <target>` mission or "turn my app into a mobile/desktop app" request, loaded by `app-factory`.

## Inputs

- Project root. Optional target id.

## Procedure

1. Call `appfactory action=analyze`.
2. Verify each field by reading the source files:
   - Package manager from the lockfile: `package-lock.json` (npm), `pnpm-lock.yaml` (pnpm), `yarn.lock` (yarn), `bun.lockb` / `bun.lock` (bun). Run scripts as `npm run build`, `pnpm build`, `yarn build`, `bun run build`.
   - Frontend: `package.json` dependencies (`next`, `vite`, `react-scripts`, `@angular/core`, `nuxt`, `@sveltejs/kit`, `vue`) and the version.
   - Build output: `dist/` (Vite), `build/` (CRA), `out/` (Next export), `dist/<name>/browser` (Angular 17+), `.output/public` (Nuxt generate).
   - Server features (Next.js): `grep` for `"use server"`, `cookies()`, `headers()`, `export const dynamic`, `middleware.ts`, `app/**/route.ts`, `pages/api/**`, `getServerSideProps`.
   - Backend: `requirements.txt` / `pyproject.toml` (FastAPI, Django, Flask), `@nestjs/core`, `express`, Next.js API routes.
   - Auth: `next-auth` / `@auth/*`, `@clerk/*`, `@supabase/*`, `firebase`, `passport`, JWT libraries; note cookie vs bearer token.
   - API base URL: `.env.example`, `.env*` keys (`NEXT_PUBLIC_API_URL`, `VITE_API_URL`, `REACT_APP_API_URL`), hardcoded `fetch("http...`.
   - Existing folders: `os/`, `android/`, `ios/`, `src-tauri/`, `capacitor.config.*`.
3. Never print secret values from `.env` files; only key names.

## Verification

Every field is backed by a file path or marked `none`.

## Failure modes and fixes

- Monorepo: locate the web app package (`apps/web`, `packages/*`) and analyze it, not the root.
- No build script: report it; the plan adds the build only in `os/<target>`.

## Deliverable

The analysis summary passed to `app-compatibility-engine` and the report step `analysis`.
