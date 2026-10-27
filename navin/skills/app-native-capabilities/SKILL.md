---
name: app-native-capabilities
description: App Factory native features. Add only the native capabilities the app really uses - Capacitor plugins (Camera, Filesystem, Preferences, Push Notifications, Geolocation, App back button, StatusBar, Keyboard, SplashScreen) and Tauri 2 plugins (fs, dialog, notification, shell with scope, updater, store) - always with minimal permissions.
metadata: {"navin":{"emoji":"📲","category":"development"}}
---

# App Native Capabilities

## When to use

During phase 4, after the shell exists, when the web app uses a feature that needs native access, plus the baseline mobile UX plugins.

## Inputs

- Features the web app uses (file upload, camera input, geolocation, notifications, downloads), target.

## Procedure: Capacitor (inside `os/<target>`)

1. Baseline: `npm install @capacitor/app @capacitor/status-bar @capacitor/keyboard @capacitor/splash-screen`.
2. Back button: `App.addListener("backButton", ({ canGoBack }) => canGoBack ? history.back() : App.exitApp())`.
3. Only when used: `@capacitor/camera`, `@capacitor/filesystem`, `@capacitor/preferences`, `@capacitor/push-notifications` (needs `google-services.json` from the user, never committed if private), `@capacitor/geolocation`.
4. Permissions in `android/app/src/main/AndroidManifest.xml` only for used plugins (`CAMERA`, `ACCESS_FINE_LOCATION`, `POST_NOTIFICATIONS`); iOS `Info.plist` usage strings (`NSCameraUsageDescription`, ...). Request at the moment of use, never at startup.
5. `npx cap sync android`.

## Procedure: Tauri (inside `os/<target>`)

1. `npx tauri add <plugin>` for `fs`, `dialog`, `notification`, `shell`, `updater`, `store`.
2. `src-tauri/capabilities/default.json`: list exact permissions, for example `"fs:allow-read-text-file"` with a scope `[{ "path": "$APPDATA/**" }]`, never `fs:default` over `$HOME/**`.
3. Shell: only `shell:allow-open` or a scoped sidecar `{ "identifier": "shell:allow-execute", "allow": [{ "name": "binaries/<sidecar>", "sidecar": true }] }`.
4. Updater: endpoint HTTPS, `pubkey` in config, private key from env (`app-release-manager`).

## Verification

Each plugin call exercised in a device or desktop test; permission list reviewed by `app-security-auditor`.

## Failure modes and fixes

- Plugin not implemented on web: guard with `Capacitor.isNativePlatform()`.
- Tauri `not allowed` error: permission missing from the capability file.

## Deliverable

Plugin list with the permissions each one adds; report step `native`.
