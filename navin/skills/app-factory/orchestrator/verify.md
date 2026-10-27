# Phases 5 and 6: Verify (Build, tests, delivery)

## Build

1. Run the build with `exec background=true` and poll with `write_stdin`:
   - Android: `cd os/android/android && ./gradlew assembleDebug`, then `./gradlew bundleRelease` (signed, see `app-release-manager`).
   - Desktop: `cd os/<id> && npx tauri build --debug --bundles <msi,nsis|dmg|deb|rpm|appimage>` first (fast, installable), then the same without `--debug` for the optimized package.
2. `appfactory action=artifacts target=<id>`: a package must exist with a non zero size.
3. Failure -> `recover.md`.

Journal: "Application compilee".

## Tests (`app-device-tester`)

Required tests: install, start, auth, navigation, API calls, main features.

- Android: `mobile action=preview_start`, `adb install -r <apk>`, launch, `mobile action=ui_dump`, `mobile action=tap`, Maestro flow `tests/android/smoke.maestro.yaml`, `adb logcat` for crashes.
- Desktop: launch the built binary, check the process is alive after 10 s, Playwright `tests/desktop/smoke.spec.ts` against the web build.
- Every test records `{name, status: passed|failed|not_run, evidence}`. A test that could not run is `not_run` with the reason, never `passed`.

Security (`app-security-auditor`) runs before delivery: permissions, CSP/capabilities, secrets scan, dependency audit.

Journal: "Tests Android valides" (or the honest count: "4 tests sur 6 executes, 2 non executes : ...").

## Delivery

1. `appfactory action=report target=<id> steps=[...] tests=[...]`.
2. Status is `delivered` only with a package and every required test passed; otherwise `partial` or `failed`. Never reword a `partial` as a success.
3. Final journal line: "Application livree : <artifact file name>" plus the report path `.navin/appfactory/<id>/report.md`.
4. Board: close the remaining tasks with evidence (artifact path, test results).
