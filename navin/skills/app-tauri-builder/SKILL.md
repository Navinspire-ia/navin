---
name: app-tauri-builder
description: App Factory desktop builder. Wrap the web frontend with Tauri 2 + Rust inside os/<target> and build Windows (.exe NSIS, .msi), macOS (.dmg), Linux (.deb, .rpm, .AppImage) packages - tauri init, frontendDist, beforeBuildCommand, minimal capabilities, updater and signing keys from env.
metadata: {"navin":{"emoji":"🦀","category":"development"}}
---

# App Tauri Builder

## When to use

Targets `windows`, `macos`, `ubuntu`, `centos`, `appimage`. Tauri 2 is the imposed desktop engine; never switch to Electron or WinUI 3.

## Inputs

- `os/<target>` scaffold, frontend dist path, app name and identifier, package manager.

## Procedure (run inside `os/<target>`)

1. `appfactory action=scaffold target=<id>` (refuses to overwrite).
2. `npm install -D @tauri-apps/cli@^2` (`pnpm add -D`, `yarn add -D`, `bun add -d`).
3. If no `src-tauri/`: `npx tauri init --ci --app-name "<name>" --window-title "<name>" --frontend-dist <rel path to dist> --dev-url http://localhost:5173 --before-dev-command "<pm> run dev" --before-build-command "<pm> run build"`.
4. `src-tauri/tauri.conf.json`: `build.frontendDist` must point to static files (for example `../../../dist` or `../web/dist`), `build.beforeBuildCommand` builds them (with `cwd` handled by a script in `os/<target>`), `identifier` reverse DNS, `app.security.csp` set (no `unsafe-eval`).
5. `src-tauri/capabilities/default.json`: only `core:default` plus the plugin permissions really used (see `app-native-capabilities`).
6. Rust targets: `rustup target add x86_64-pc-windows-msvc` (Windows), `rustup target add aarch64-apple-darwin x86_64-apple-darwin` (macOS universal: `--target universal-apple-darwin`).
7. Dev: `npx tauri dev`.
8. Build (`exec background=true`):
   - Windows: `npx tauri build --bundles msi,nsis`
   - macOS: `npx tauri build --bundles dmg`
   - Ubuntu: `npx tauri build --bundles deb`
   - CentOS / Fedora: `npx tauri build --bundles rpm`
   - AppImage: `npx tauri build --bundles appimage`
9. Updater / signing: `npx tauri signer generate -w ~/.tauri/<app>.key` once; build with `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` from env, never committed. OS code signing: `app-release-manager`.
10. `appfactory action=artifacts target=<id>`; packages are under `src-tauri/target/release/bundle/<msi|nsis|dmg|deb|rpm|appimage>/`.

## Verification

Artifact present with size > 0; binary launches (`app-device-tester`).

## Failure modes and fixes

See `app-build-repair`: `frontendDist` empty, missing webkit2gtk-4.1, `link.exe` not found, WebView2 missing, Rust target missing, white screen.

## Deliverable

The desktop package path(s); report step `build`.
