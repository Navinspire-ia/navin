---
name: app-release-manager
description: App Factory delivery and publishing - versionCode/versionName, Tauri version, signing (Android keystore, Windows code signing, macOS notarization), Play Store .aab, store checklists, CI runners for hosts that cannot build locally, and the final appfactory report.
metadata: {"navin":{"emoji":"🚀","category":"development"}}
---

# App Release Manager

## When to use

Phase 6, and when a target needs a CI runner (MSI on Windows, iOS/macOS on macOS).

## Inputs

- Artifacts, test results, security findings, version from the site `package.json`.

## Procedure

1. Versions: Android `versionCode` (integer, always increasing) and `versionName` in `android/app/build.gradle`; Tauri `version` in `src-tauri/tauri.conf.json`; iOS `CFBundleShortVersionString` / `CFBundleVersion`.
2. Signing (secrets only from env or a secret store, ask the user before creating keys):
   - Android: keystore + `signingConfigs` (`app-capacitor-builder`); keep the keystore backed up outside the repo; Play App Signing recommended.
   - Windows: `bundle.windows.certificateThumbprint` + `digestAlgorithm sha256` + `timestampUrl`, or `signtool sign /fd sha256 /tr <tsa> /td sha256 <file>`.
   - macOS: `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD` (app specific), `APPLE_TEAM_ID` for notarization during `npx tauri build`.
   - Tauri updater: `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.
3. CI when the host cannot build: GitHub Actions matrix `windows-latest` (msi, nsis), `macos-latest` (dmg, ios), `ubuntu-22.04` (deb, rpm, appimage) with `tauri-apps/tauri-action` or Gradle; secrets as repository secrets. Ask before pushing workflows (`cicd-agent`).
4. Store checklists:
   - Play Store: signed `.aab`, target API level current, privacy policy URL, data safety form, content rating, screenshots, icon 512px.
   - App Store: `.ipa` via Xcode / Transporter, privacy manifest, usage strings, screenshots.
   - Microsoft Store / direct: signed installer; macOS notarized `.dmg`.
5. `app-security-auditor` must pass before any upload. Uploads only with explicit user consent.
6. `appfactory action=report target=<id> steps=[...] tests=[...]`; status `delivered` only with a package and all required tests passed.

## Verification

`apksigner verify --print-certs <apk>`, `jarsigner -verify <aab>`, `signtool verify /pa <exe>`, `spctl -a -vv <app>`.

## Failure modes and fixes

- Play rejects: versionCode already used, increment it.
- Notarization fails: hardened runtime and valid Developer ID.

## Deliverable

Signed artifact(s), `.navin/appfactory/<id>/report.md`, final journal line "Application livree : <artifact>".
