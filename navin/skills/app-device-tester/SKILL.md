---
name: app-device-tester
description: App Factory tests on real runtimes - Android emulator or device via the `mobile` tool and adb, Maestro flows, desktop binary launch and Playwright against the web build - with honest passed / failed / not_run reporting. A compiled build is not a working app.
metadata: {"navin":{"emoji":"🧪","category":"development"}}
---

# App Device Tester

## When to use

Phase 5, after an artifact exists.

## Inputs

- Artifact path, app id, test account if auth is required (ask the user; never invent credentials).

## Required tests

install, start, auth, navigation, API calls, main features.

## Procedure: Android

1. `mobile action=devices`; none -> `mobile action=preview_start`.
2. `adb install -r <apk>` (install).
3. `adb shell monkey -p <app_id> -c android.intent.category.LAUNCHER 1`, then `adb shell pidof <app_id>` (start).
4. `mobile action=ui_dump` / `mobile action=screenshot`; `mobile action=tap` through login and main screens (auth, navigation).
5. `adb logcat -d -s AndroidRuntime:E Capacitor:* chromium:*` for crashes, CORS and network errors (API calls).
6. Maestro: copy `app-factory/tests/android/smoke.maestro.yaml` to `os/android/tests/`, adapt, `maestro test -e APP_ID=<app_id> ...`.

## Procedure: Desktop

1. Launch the binary (see `app-factory/tests/desktop/README.md`), check the process is alive after 10 s, no panic in stderr. Linux headless: `xvfb-run -a`.
2. Playwright: serve the dist (`npx serve -s <dist> -l 4173`), `BASE_URL=http://localhost:4173 npx playwright test` with `app-factory/tests/desktop/smoke.spec.ts`.
3. WebDriver via `tauri-driver` on Windows and Linux when deeper UI tests are needed.

## Honest reporting

Each test: `{name, status: passed|failed|not_run, evidence}`. No device, no credentials, wrong host -> `not_run` with the reason. Never mark a test passed from a build log alone.

## Failure modes and fixes

- White screen: check logcat / devtools console, then `app-build-repair`.
- `INSTALL_FAILED_UPDATE_INCOMPATIBLE`: `adb uninstall <app_id>` then reinstall.

## Deliverable

The `tests` array for `appfactory action=report`.
