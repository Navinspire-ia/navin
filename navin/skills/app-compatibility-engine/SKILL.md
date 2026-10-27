---
name: app-compatibility-engine
description: Phase 2 of App Factory. Decide how a web frontend runs inside a mobile (Capacitor 8) or desktop (Tauri 2) shell - strategy static, next-export or client-shell - and enforce the Next.js rule (never just output export when Server Actions, SSR, middleware or route handlers exist). Drives `appfactory action=plan`.
metadata: {"navin":{"emoji":"🧩","category":"development"}}
---

# App Compatibility Engine

## When to use

After `app-project-analyzer`, before any file is written.

## Inputs

- Analyze report, target id.

## Locked engines

Desktop = Tauri 2 + Rust. Mobile = Capacitor 8. Never substitute React Native/Expo, Flutter, Tauri Mobile, Electron or WinUI 3, and never ask the client to choose.

## Strategy matrix

| Frontend | Server features | Strategy | Frontend dist |
| --- | --- | --- | --- |
| Vite / CRA / Angular / Vue / Svelte SPA | n/a | `static` | existing build output |
| SvelteKit / Nuxt | none | `static` with adapter-static / `nuxi generate` in `os/<id>` | generated output |
| Next.js | none | `next-export` (`output: "export"`, `images.unoptimized`, `trailingSlash`) in an `os/<id>` build config only | `out/` |
| Next.js 16 | Server Actions, server auth, SSR with `cookies()` / `headers()`, middleware, `app/**/route.ts`, `pages/api` | `client-shell` | `os/<id>/web` build |
| Server rendered only (Django templates, Rails) | all | `client-shell` or remote URL shell, ask the user | n/a |

## The Next.js rule

With any server feature, NEVER just set `output: "export"` and NEVER edit the site's `next.config`. Build an adapted client frontend in `os/<target>/web`:

1. Reuse components; replace Server Actions by `fetch` calls to the existing backend endpoints (or the existing route handlers, still served by the deployed site).
2. API base URL from env (`NEXT_PUBLIC_API_URL`), HTTPS only.
3. Auth over HTTP: bearer token in secure storage, or cookies with `SameSite=None; Secure` and CORS credentials (see `app-backend-bridge`).
4. The website keeps working unchanged.

## Procedure

1. `appfactory action=plan target=<id>`.
2. Check the strategy against the matrix and the cited files. If the engine is wrong, explain why with the file paths and follow the matrix.
3. Record risks: cross-origin auth, CORS, absolute asset paths, `next/image` optimization, mixed content, dynamic routes without `generateStaticParams`.

## Verification

Strategy justified by file paths; steps use the detected package manager.

## Failure modes and fixes

- Dynamic routes in export: use `generateStaticParams` or client routing with query params in `os/<id>/web`.
- `next/image` in export: `images: { unoptimized: true }` in the `os/<id>` config.

## Deliverable

Strategy + ordered steps + risks, report step `compatibility`.
