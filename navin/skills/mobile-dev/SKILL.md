---
name: mobile-dev
description: Build, run, and debug Expo and React Native mobile apps - detect the stack, doctor the Android/Node toolchain, start Metro/Expo, fix redbox errors, and ship UI changes with hot reload. Use for mobile Dev sessions and any React Native / Expo task.
metadata: {"navin":{"emoji":"📱","category":"development"}}
---

# Mobile Developer (Expo / React Native)

Work like a senior mobile engineer inside the workspace. Prefer the `mobile`
tool over raw shell guesses for setup and packager lifecycle.

## When to use

- The workspace is (or should become) an Expo or React Native app.
- The user asks to run on an emulator/device, fix Metro/Gradle/redbox errors,
  add screens, restyle UI, or make the layout responsive.
- Flutter is detected: report it, use `mobile(action=detect|plan)`, and say
  Flutter run automation is plan-only in this release.

## Operating loop

Stay in **Agent** execution. Do **not** switch to Plan mode for Mobile setup.
When preparing the environment, show a short numbered checklist in chat and
tick items as you finish them (1/4, 2/4, …). Prefer doing installs over
writing a long plan. Progress bars come from the tools - do not invent fake %.

1. **Detect** - `mobile(action="detect")` as soon as the session opens on a
   mobile-looking repo. Do not assume Expo vs bare React Native.
2. **Bootstrap** - for a ready Android toolchain call
   `mobile(action="bootstrap")` (preferred over hand-rolled sdkmanager shells).
   It installs under `~/.navin` (adb → JDK → cmdline-tools → SDK → AVD) and
   emits live progress. First bootstrap step is OS-aware:
   - **WSL**: if KVM group not active yet, **tell the user** to open Windows
     PowerShell (not Ubuntu) and run `wsl --shutdown`, then reopen WSL. Never
     run `wsl` from inside Ubuntu. **Or** ask them to Play an AVD in Android
     Studio on Windows → Refresh Mobile.
   - **Windows**: ASK user to open Android Studio → Device Manager → Play an AVD
     (winget installs Studio when missing).
   - **macOS**: Android Studio via `brew install --cask android-studio` when missing
   - **Linux**: KVM (`usermod -aG kvm`) then logout/login (not `wsl --shutdown`)
   **Never** start `-accel off` / swiftshader. USB phones always OK when in
   `adb devices`. Then `mobile(action="doctor")` / `mobile(action="devices")`.
3. **Doctor / setup** - if bootstrap is unnecessary (device already online),
   `mobile(action="setup")` then `mobile(action="doctor")` is enough.
4. **Run** - `mobile(action="run", target="android")` (or `ios` / `web` /
   `metro`). Use `mobile(action="logs")` while iterating. Stop with
   `mobile(action="stop")`.
5. **Preview (mandatory)** - once a device/emulator is online, call
   `mobile(action="preview_start")` (opens the Dev **Mobile** tab) or
   `open_preview(kind="mobile")`. For `target="web"`, after the packager URL
   responds, call `open_preview(kind="web", url=...)`. Keep the app running so
   you can fix redboxes the user sees in Preview. Tell the user to click
   **Start preview** only if the panel is already open and idle.
6. **Code** - small, focused edits. Follow the project's router, theme, and
   component patterns (Expo Router vs React Navigation, NativeWind vs StyleSheet).
7. **Verify (mandatory)** - after meaningful edits, run `verify action=check`
   or `test_run`. Also watch Fast Refresh / `mobile(action="logs")`; fix redboxes
   before claiming done. Add a minimal test for new behavior when the stack allows it.
8. **Review (light)** - short `critic-reviewer` pass (or `code_review` on larger
   diffs); fix blockers, then deliver.
9. **Deliver** - summarize files changed, how to re-run (`mobile run` or the
   packager command), test/preview status, and any remaining device/SDK setup.

## Delivery gate (do not skip)

Before ending a turn that built or changed a mobile/web app:

1. App is **running** (`mobile run` / packager / web target).
2. Preview is open (`preview_start` or `open_preview`).
3. Tests / `verify` ran; failures fixed or explicitly reported.
4. Light review done; blockers fixed.

## Tool cheat sheet

| Goal | Call |
|------|------|
| What stack is this? | `mobile(action="detect")` |
| Full Android install cycle | `mobile(action="bootstrap")` |
| Is the machine ready? | `mobile(action="doctor")` |
| List phones / AVDs | `mobile(action="devices")` |
| Show commands only | `mobile(action="plan", target="android")` |
| Start Expo/Metro (+ AVD if needed) | `mobile(action="run", target="android")` |
| Follow logs | `mobile(action="logs")` |
| Stop packager / emulator | `mobile(action="stop")` |
| Live Android preview | `mobile(action="preview_start")` (auto-opens Dev **Mobile**) or `open_preview(kind="mobile")` |
| Tap / swipe / keys | `mobile(action="tap"\|"swipe"\|"key"\|"text", ...)` |
| See the screen (vision) | `mobile(action="screenshot")` - returns a real image to the model |
| Map on-screen controls | `mobile(action="ui_dump")` then match testID / text / bounds |
| Perf snapshot | `mobile(action="metrics")` |

If `run` is blocked, do not invent alternate start commands until doctor is
clean - fix the reported missing tools first.

The Mobile Preview panel (Dev workbench → Mobile) shows the device screen,
logcat, FPS/memory. Click = tap, drag = swipe. `preview_start` opens that panel
for the user automatically; use `tap` / `ui_dump` yourself when verifying a fix.

## Stack recipes

### Expo (preferred)

- Start: `mobile(action="run", target="android")` or `target="web"` for a quick
  UI loop without an emulator.
- Entry is often `app/` (Expo Router) or `App.tsx`.
- Use Expo APIs (`expo-router`, `expo-image`, `expo-secure-store`) before adding
  bare native modules. If a library needs custom native code, confirm whether
  the app is managed Expo, prebuild, or a dev client.
- Common fix: delete stale Metro cache with `npx expo start -c` via `exec` only
  after a normal start fails on cache.

### React Native CLI

- Prefer project scripts (`npm run android` / `start`) surfaced by
  `mobile(action="plan")`.
- Native folders `android/` and `ios/` matter: Gradle errors are real; read the
  first failure, not the last warning.
- After native dependency changes: rebuild the app (`run-android`), not only
  Metro.

### Flutter

- Detected via `pubspec.yaml`. Prefer `mobile(action="run", target="android")`
  or `target="ios"`. Use the Mobile Preview panel the same way as Expo/RN once
  the device is online.
- Never pass a platform to `-d`: `flutter run -d ios` matches no device and the
  run hangs. The tool resolves the real adb serial / iPhone UDID; pass
  `device=<id>` to pick one when several are online.
- On a physical iPhone, the first run takes many minutes (Xcode prepares
  debugger support, then signs). Poll `mobile(action="logs")` and tell the user
  what is happening instead of waiting silently.

## UI / product patterns

- **New screen**: match existing navigation (Expo Router file in `app/`, or a
  React Navigation stack/tab). Wire the route, empty/loading/error states, and
  a way to open the screen from the current IA.
- **Material / theme**: reuse the project's theme provider or tokens; do not
  invent a second palette. For React Native Paper / NativeWind / Tamagui,
  follow whatever is already installed.
- **Responsive**: prefer flex + constraints over fixed pixel widths; test
  mentally for small phones and tablets; avoid hardcoded `width: 400`.
- **Dark mode**: use the project's color scheme / theme tokens; never hardcode
  only light colors if dark is already supported.

## Error playbook

- **Metro port in use**: `mobile(action="stop")`, then run again; if needed
  kill the stale process on 8081 via `exec`.
- **SDK / adb missing**: quote the doctor `Fix:` lines; do not pretend the
  emulator started.
- **Gradle / compileSdk**: open the cited `build.gradle` / `gradle.properties`
  and align versions with the React Native / Expo version in package.json.
- **Redbox "Element type is invalid"**: usually a wrong default vs named
  import - check the export of the file you just touched.
- **iOS on Linux/Windows**: say clearly that Xcode/macOS is required; offer
  Android or Expo web instead.

## Guardrails

- Do not commit secrets from `google-services.json`, keystores, or `.env`.
- Do not run `npm install -g` for expo-cli; use `npx expo`.
- Do not claim the app is running without packager logs or a connected device
  when the task required a device.
- Keep changes scoped; mobile UI refactors easily sprawl across navigation,
  theme, and assets - one concern per turn when possible.
