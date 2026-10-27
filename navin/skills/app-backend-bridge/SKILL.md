---
name: app-backend-bridge
description: Phase 4 of App Factory. Keep the existing backend (FastAPI, NestJS, Express, Django, Next.js API) and make the mobile/desktop app talk to it over HTTPS - API base URL, CORS origins capacitor://localhost, http://localhost, https://tauri.localhost, tauri://localhost, auth cookies or tokens. Sidecar backend only when a local desktop backend is required.
metadata: {"navin":{"emoji":"🌉","category":"development"}}
---

# App Backend Bridge

## When to use

Whenever the app calls an API or uses auth.

## Inputs

- Backend framework, deployed backend URL, auth library and transport.

## Procedure

1. Never rewrite the backend. Changes are limited to CORS and cookie settings, proposed to the user as a diff first.
2. App origins to allow: `capacitor://localhost` (iOS), `http://localhost` and `https://localhost` (Android), `https://tauri.localhost` (Windows), `tauri://localhost` (macOS, Linux).
3. CORS per backend, with credentials when cookies are used:
   - FastAPI: `CORSMiddleware(allow_origins=[...], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])`.
   - Express / NestJS: `cors({ origin: [...], credentials: true })` / `app.enableCors({...})`.
   - Django: `django-cors-headers`, `CORS_ALLOWED_ORIGINS`, `CORS_ALLOW_CREDENTIALS = True`, plus `CSRF_TRUSTED_ORIGINS`.
   - Next.js route handlers: `Access-Control-Allow-Origin` echo of the allowed origin + `OPTIONS` handler.
4. Auth: prefer bearer tokens stored with `@capacitor/preferences` (or secure storage plugin) / Tauri store; cookies need `SameSite=None; Secure`. Android Capacitor can use `CapacitorHttp` / `CapacitorCookies` to avoid cross-origin cookie limits.
5. API base URL from env at build time (`VITE_API_URL`, `NEXT_PUBLIC_API_URL`), HTTPS only.
6. Sidecar (desktop only, when the app needs a local backend): bundle the binary via Tauri `bundle.externalBin` and spawn it with the shell plugin, scope limited to that binary.

## Verification

`curl -i -X OPTIONS <api>/health -H "Origin: https://tauri.localhost" -H "Access-Control-Request-Method: GET"` returns the origin; login then an authenticated call succeed in the app.

## Failure modes and fixes

- CORS error: origin missing or `*` with credentials.
- Logged out after reload: cookie `SameSite=Lax` blocked on `capacitor://localhost` / `https://tauri.localhost`; use tokens or `SameSite=None; Secure`.
- Mixed content: `http://` API blocked; use HTTPS.

## Deliverable

Backend patch proposal (applied only with consent) and the app API config; report step `backend`.
