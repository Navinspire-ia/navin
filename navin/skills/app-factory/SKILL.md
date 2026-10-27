---
name: app-factory
description: Entry point to turn an existing web app into a real mobile or desktop app (Android, iOS, Windows, macOS, Linux). Use when the user says "turn my app into an Android/iOS/Windows/macOS/Linux app", "make a mobile app", "make a desktop app", "/apps", "build an apk", "build an msi", "AppImage", "dmg", "aab". Drives the `appfactory` engine through 6 phases (analysis, compatibility, tools, transformation, build and tests, delivery) and delegates to the 12 app-* specialist skills.
metadata: {"navin":{"emoji":"🏭","category":"development"}}
---

# App Factory

## Overview

You turn an existing web project into installable apps without asking the client any technical question. The client only sees "Create a mobile app" or "Create a desktop app". Everything is generated in `os/<target>` at the project root, built, tested on a real runtime and delivered with an honest report.

## Locked technology decision

Navin imposes two technologies and never asks the client to pick a framework:

| Family | Engine | Order | Deliverables |
| --- | --- | --- | --- |
| Desktop | Tauri 2 + Rust | Windows first, then macOS, then Linux | `.exe` / `.msi`, `.dmg`, `.deb`, `.rpm`, `.AppImage` |
| Mobile | Capacitor 8 | Android first, then iOS | `.apk`, `.aab`, then `.ipa` |

- React Native / Expo, Flutter, Tauri Mobile, Electron and WinUI 3 are known (see `mobile-dev`) but are never substituted automatically.
- Backend: keep the existing one (FastAPI, NestJS, Express, Django, Next.js API...). Never rewrite it.
- Tests: Playwright, Maestro, ADB (others when present: Espresso, Appium, native tests).
- Builds: Android SDK, Gradle, Rust, MSVC. When the local host cannot build a target (MSI needs Windows; iOS and macOS need macOS + Xcode), use a CI runner on the right OS.
- Client-facing messages never name Rust, Gradle, Android SDK, Tauri or Capacitor. Say "the desktop toolchain", "the Android toolchain", "the iOS toolchain".

## Targets and folders

`/apps <target>` with target in `android`, `ios`, `windows`, `macos`, `ubuntu`, `centos`, `appimage`. Each target lives in `os/<target>` (for example `os/android`, `os/windows`). Reports go to `.navin/appfactory/<target>/`.

## Engine: the `appfactory` tool

| Action | Use |
| --- | --- |
| `targets` | List targets: engine, folder `os/<id>`, deliverables, required host. |
| `analyze` | Project report: frontend framework + version, package manager, build script, build output dir, backend frameworks, server features with file paths, auth libraries, API base URL env vars, existing `os/` or native folders. |
| `plan target=<id>` | Compatibility diagnosis + strategy (`static`, `next-export`, `client-shell`), ordered steps with exact commands for the package manager, required tools, risks, files to create. |
| `doctor target=<id>` | Toolchain per target (present with version / missing with official install command for this OS) and host compatibility. Never installs anything. |
| `scaffold target=<id> [app_name] [app_id] [backend_url] [dry_run]` | Writes `os/<id>` from templates (`dry_run=true` lists the files only). Keeps every existing file, never writes outside `os/<id>` and `.navin/appfactory`, refuses a plain HTTP backend (except localhost / 10.0.2.2) and an invalid app id. Runs the audit at the end. |
| `audit target=<id>` | Security checks of `os/<id>` against `policies/security.yaml`: remote URL wrapper, cleartext, Android permissions, Tauri capabilities and CSP, HTTPS backend, secret files and secret patterns. Run it before every delivery. |
| `artifacts target=<id>` | Built packages found under `os/<id>` (path, size, date, debug or release). |
| `report target=<id> steps=[...] tests=[...]` | Writes `.navin/appfactory/<id>/report.md` + `report.json`. Status `delivered` only with a package and every required test passed with evidence (a `passed` test without evidence counts as not run; a project with a login also needs an `auth` test); otherwise `partial` or `failed`. |

What `scaffold` writes in `os/<id>`:

| Path | Role |
| --- | --- |
| `appfactory.json` | Strategy, frontend folder, build command, backend URL, API env names, aliases. Change the backend here, not in code. |
| `scripts/build-web.mjs` | Builds the frontend and copies it into `www/` (static: the site's build, rebuilt with a relative base when needed; next-export: a staging copy in `.stage/` with `output: "export"`, the site's next.config untouched; client-shell: `web/`). Adds the native helpers to the copy. |
| `capacitor.config.json`, `native/`, `assets/logo.png` | Mobile: `webDir: www`, https scheme, no remote URL; back button and safe areas; icon source for `capacitor-assets`. |
| `src-tauri/` (`tauri.conf.json`, `Cargo.toml`, `src/`, `capabilities/default.json`), `app-icon.png` | Desktop: `frontendDist: ../www`, CSP limited to the backend, `core:default` only. |
| `web/` + `MIGRATION.md` | Only for `client-shell`: Vite + React client that reuses the site's components (tsconfig aliases, shims for `next/link`, `next/navigation`, `next/image`) and calls the backend through `web/src/api.ts`. `MIGRATION.md` lists every server feature to reconnect, with files. |
| `tests/` | `smoke.maestro.yaml` (mobile) or `smoke.spec.ts` (desktop) with the app id filled in. |
| `ci/github-actions.yml` | Build on the right runner when this machine cannot (copy it to `.github/workflows/`). |

## The 6 phases

| # | Phase | Tool actions | Specialist skill | Runbook |
| --- | --- | --- | --- | --- |
| 1 | Analysis | `appfactory action=analyze`, `appfactory action=targets` | `app-project-analyzer` | `orchestrator/detect.md` |
| 2 | Compatibility diagnosis | `appfactory action=plan target=<id>` | `app-compatibility-engine` | `orchestrator/plan.md` |
| 3 | Tool preparation | `appfactory action=doctor target=<id>`, `ask_user`, `exec` | `app-environment-manager` | `orchestrator/plan.md` |
| 4 | Transformation | `appfactory action=scaffold target=<id>`, edits inside `os/<id>` | `app-frontend-adapter`, `app-backend-bridge`, `app-tauri-builder` or `app-capacitor-builder`, `app-native-capabilities` | `orchestrator/execute.md` |
| 5 | Build and tests | `exec background=true`, `write_stdin`, `appfactory action=artifacts`, `appfactory action=audit`, `mobile`, adb, Maestro, Playwright | `app-tauri-builder`, `app-capacitor-builder`, `app-device-tester`, `app-security-auditor`, `app-build-repair` | `orchestrator/verify.md`, `orchestrator/recover.md` |
| 6 | Delivery | `appfactory action=report target=<id>` | `app-release-manager` | `orchestrator/verify.md` |

Track each phase as a board task (`board action=create`, then `move ... status=done evidence=...`) and as a `steps` entry of the final report.

## User-facing journal

Short lines, one per milestone, in the user's language, no tool names. French examples:

```
Analyse du projet terminee
Compatibilite du frontend verifiee
Outils de compilation prets
Application preparee dans os/android
Application compilee
Tests Android valides
Application livree : os/android/app-release.aab
```

English equivalents: "Project analysis complete", "Frontend compatibility checked", "Build tools ready", "Application compiled", "Android tests passed", "Delivered: <artifact name>". When something fails, say what is missing in plain words ("The Windows installer needs a Windows machine; I prepared a CI build").

## Mandatory rules

1. **Zero technical choice for the client.** Never ask which framework, engine or bundler. Only ask for consent (installs, branches, signing secrets) and business facts (app name, icon, store account).
2. **Zero useless rewrite.** Reuse the existing frontend build. Adapt only what the runtime requires.
3. **No destruction.** Write only in `os/<target>` and `.navin/appfactory`. Never overwrite the web project, never edit the site's `next.config`, `vite.config` or `package.json` scripts. Never create or switch git branches without asking; a dedicated branch is used only if the user agrees.
4. **Backend preserved.** The app calls the existing backend over HTTPS. A local sidecar backend is added only when the desktop app truly requires a local backend.
5. **Real mobile adaptation.** Responsive layout, touch targets of 44px or more, keyboard handling, safe areas, hardware back navigation, notifications and permissions requested in context.
6. **Mandatory tests.** Install, start, auth, navigation, API calls, main features.
7. **No false success.** A compiled build is not a working app. The delivery separates executed tests from tests not executed, with evidence for each.
8. **Security.** Minimal native permissions, no secret in source, signing keys from env or a secret store, `app-security-auditor` checks before any publishing.

## Next.js rule (critical)

When the app uses Next.js 16 with Server Actions (`"use server"`), server auth, SSR (dynamic rendering, `cookies()` / `headers()`), middleware or route handlers (`app/**/route.ts`, `pages/api`), NEVER just set `output: "export"`. Build an adapted client frontend in `os/<target>/web` (strategy `client-shell`) that calls the existing backend over HTTPS. Without server features, static export is fine, but only in the `os/<target>` build config. Details: `app-compatibility-engine`.

## Structure

```
navin/skills/app-factory/
  SKILL.md                     this entry point
  orchestrator/
    detect.md                  phase 1 runbook
    plan.md                    phases 2 and 3 runbook
    execute.md                 phase 4 runbook
    verify.md                  phases 5 and 6 runbook
    recover.md                 failure recovery runbook
  templates/
    common/                    build-web.mjs, README, .gitignore (every target)
    capacitor/                 native helpers, CI workflow (mobile)
    tauri/                     src-tauri sources, CI workflow (desktop)
    client-shell/web/          client frontend for apps with server features
  policies/
    security.yaml              permissions, CSP, secrets, HTTPS rules used by audit
    builds.yaml                package locations and required tests per target
  tests/
    android/                   smoke.maestro.yaml + README.md (adb, Maestro)
    desktop/                   smoke.spec.ts + README.md (binary launch, Playwright)
```

The 12 specialist skills sit next to it as top-level skills so the agent loads them by name: `app-project-analyzer`, `app-compatibility-engine`, `app-environment-manager`, `app-frontend-adapter`, `app-backend-bridge`, `app-tauri-builder`, `app-capacitor-builder`, `app-native-capabilities`, `app-security-auditor`, `app-device-tester`, `app-build-repair`, `app-release-manager`.
