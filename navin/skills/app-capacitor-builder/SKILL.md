---
name: app-capacitor-builder
description: App Factory mobile builder. Wrap the web frontend with Capacitor 8 inside os/<target> and build Android (.apk, .aab) then iOS (.ipa) - cap init, add android, sync, run, Gradle assembleDebug / bundleRelease, signing via keystore with secrets from env.
metadata: {"navin":{"emoji":"⚡","category":"development"}}
---

# App Capacitor Builder

## When to use

Targets `android` and `ios`. Capacitor 8 is the imposed mobile engine; never switch to React Native, Flutter or Tauri Mobile.

## Inputs

- `os/<target>` scaffold, frontend dist (`webDir`), app name, app id, package manager.

## Procedure (run inside `os/<target>`)

1. `appfactory action=scaffold target=<id>`.
2. `npm install @capacitor/core` and `npm install -D @capacitor/cli` (pnpm: `pnpm add`, yarn: `yarn add`, bun: `bun add`).
3. `npx cap init "<name>" <app_id> --web-dir <rel path to dist>` (skip when `capacitor.config.ts` exists).
4. Android: `npm install @capacitor/android`, `npx cap add android`.
   iOS (macOS only): `npm install @capacitor/ios`, `npx cap add ios`.
5. Build the web: `npm run build` (site script or `os/<target>/web`), then `npx cap sync android`.
6. Dev run: `npx cap run android` (emulator or device from `mobile action=devices`).
7. Debug APK (`exec background=true`): `cd android && ./gradlew assembleDebug` (Windows `gradlew.bat`). Output `android/app/build/outputs/apk/debug/app-debug.apk`.
8. Release signing: keystore outside the repo, `keytool -genkeypair -v -keystore ~/.navin/keys/<app>.jks -alias <app> -keyalg RSA -keysize 2048 -validity 10000`; in `android/app/build.gradle`:
   ```groovy
   signingConfigs {
     release {
       storeFile file(System.getenv("ANDROID_KEYSTORE_PATH"))
       storePassword System.getenv("ANDROID_KEYSTORE_PASSWORD")
       keyAlias System.getenv("ANDROID_KEY_ALIAS")
       keyPassword System.getenv("ANDROID_KEY_PASSWORD")
     }
   }
   buildTypes { release { signingConfig signingConfigs.release } }
   ```
9. Release: `./gradlew bundleRelease` (`.aab`, Play Store) and `./gradlew assembleRelease` (`.apk`).
10. iOS: `npx cap sync ios`, `xcodebuild -workspace ios/App/App.xcworkspace -scheme App -configuration Release archive -archivePath build/App.xcarchive`, then `xcodebuild -exportArchive` for the `.ipa`.
11. `appfactory action=artifacts target=<id>`.

## Verification

APK installs and launches (`app-device-tester`); `.aab` exists.

## Failure modes and fixes

See `app-build-repair`: JDK mismatch, SDK licenses, minSdk, white screen, CORS, cookies on `capacitor://localhost`.

## Deliverable

`.apk` / `.aab` / `.ipa` paths; report step `build`.
