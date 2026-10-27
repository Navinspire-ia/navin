---
name: app-frontend-adapter
description: Phase 4 of App Factory. Adapt the web frontend to run inside the Capacitor 8 or Tauri 2 shell without touching the website - static build config in os/<target>, client-shell frontend in os/<target>/web for Next.js server features, API base URL from env, relative assets, real mobile adaptation (safe areas, 44px touch targets, keyboard, back navigation).
metadata: {"navin":{"emoji":"🎨","category":"development"}}
---

# App Frontend Adapter

## When to use

After the strategy is fixed by `app-compatibility-engine`.

## Inputs

- Strategy, build output dir, package manager, API base URL env var.

## Procedure

1. Never edit the site's configs. All overrides live in `os/<target>`.
2. `static`: build with the site script (`npm run build` / `pnpm build` / `yarn build` / `bun run build`) and point the shell at the output. Relative base: Vite `--base ./`, CRA `PUBLIC_URL=.`, Angular `--base-href ./`.
3. `next-export`: `os/<target>/next.config.export.mjs` that imports the site config and adds `output: "export"`, `images: { unoptimized: true }`, `trailingSlash: true`; build with `NEXT_CONFIG_FILE` or a copy step in `os/<target>`, output `out/`.
4. `client-shell`: create `os/<target>/web` (Vite + React or Next export) reusing components; replace Server Actions and server data loading by an API client:
   ```ts
   const API = import.meta.env.VITE_API_URL; // https only
   export const api = (p: string, init?: RequestInit) =>
     fetch(`${API}${p}`, { credentials: "include", ...init });
   ```
5. Mobile adaptation: `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`, `padding: env(safe-area-inset-top) ...`, touch targets min 44px, inputs not hidden by the keyboard (`@capacitor/keyboard` resize), hardware back mapped to router back (`app-native-capabilities`), no hover only interactions.
6. Hash or memory routing when the shell cannot serve deep links.

## Verification

Serve the dist locally (`npx serve -s <dist>`) and run `tests/desktop/smoke.spec.ts` of `app-factory`; no 404 on assets.

## Failure modes and fixes

- White screen: absolute `/assets/...` paths or `<base href="/">`; switch to relative base.
- Mixed content: every API URL must be `https://`.
- `window` undefined in reused server components: mark `"use client"` or move to the client shell.

## Deliverable

A static frontend dist for `frontendDist` / `webDir`; report step `frontend`.
