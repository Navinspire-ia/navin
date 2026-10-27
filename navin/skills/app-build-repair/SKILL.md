---
name: app-build-repair
description: App Factory recovery for failed mobile/desktop builds and broken runtimes - Gradle/JDK mismatch, SDK licenses, minSdk, missing webkit2gtk-4.1, MSVC link.exe not found, WebView2 missing, Rust target missing, frontendDist empty, white screen from absolute asset paths, CORS, SameSite cookies on capacitor://localhost and https://tauri.localhost, mixed content.
metadata: {"navin":{"emoji":"🔧","category":"development"}}
---

# App Build Repair

## When to use

Any failed command or broken runtime during phases 4 and 5 (`app-factory/orchestrator/recover.md`).

## Inputs

- Failing command, full log, target.

## Procedure

1. Read the first real error in the log (not the last line).
2. Match the table below; fix only in `os/<target>` or the toolchain (with consent).
3. Re-run the failed step only. Stop after 3 attempts on the same error and report it.

## Failure modes and fixes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Unsupported class file major version` / `requires Java 21` | Gradle/JDK mismatch | Install Temurin 21, set `JAVA_HOME`, `./gradlew --version` |
| `You have not accepted the license agreements` | SDK licenses | `sdkmanager --licenses` |
| `SDK location not found` | `ANDROID_HOME` unset | set it or write `android/local.properties` `sdk.dir=...` |
| `minSdkVersion X cannot be smaller than Y` | plugin minSdk | raise `minSdkVersion` in `android/variables.gradle` |
| `webkit2gtk-4.1 was not found` | Linux deps | `apt-get install libwebkit2gtk-4.1-dev` / `dnf install webkit2gtk4.1-devel` |
| `linker link.exe not found` | MSVC missing | VS Build Tools with VCTools workload (`app-environment-manager`) |
| App does not start on Windows, WebView2 error | WebView2 runtime missing | `winget install Microsoft.EdgeWebView2Runtime`; NSIS `webviewInstallMode` |
| `target may not be installed` | Rust target missing | `rustup target add <triple>` |
| `frontendDist ... does not exist` or empty window | web not built or wrong path | run the web build, fix the relative `frontendDist` / `webDir` |
| White screen, 404 on `/assets/...` | absolute asset paths / `<base href="/">` | relative base (`--base ./`, `PUBLIC_URL=.`, `--base-href ./`) |
| `blocked by CORS policy` | origin not allowed | add `capacitor://localhost`, `http://localhost`, `https://tauri.localhost`, `tauri://localhost` (`app-backend-bridge`) |
| Logged out after login | `SameSite=Lax` cookie on `capacitor://localhost` / `https://tauri.localhost` | bearer token or `SameSite=None; Secure`, `CapacitorCookies` |
| `Mixed Content` | `http://` API | HTTPS API URL |
| `Execution failed for task ':app:mergeDebugResources'` | stale build | `./gradlew clean` then rebuild |
| `npx cap sync` copies nothing | `webDir` wrong | fix `capacitor.config.ts` `webDir` |

## Verification

The failed step now succeeds and the next phase continues.

## Deliverable

Report step note with error excerpt, fix and attempts.
