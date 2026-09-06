# Mobile Agent

Build, run, preview, and fix Expo, React Native, and Flutter apps from the Navin Dev workbench. The agent detects the stack, doctors the Android/Node toolchain, starts the packager, and can drive a live Android device or emulator.

## Why you need it

Web-oriented Dev mode already edits files and runs shells. Mobile work adds:

- stack detection (Expo vs bare React Native vs Flutter);
- environment checks (Node, adb, Android SDK, AVD);
- one-click / composer-driven run of Metro, Expo, or Flutter;
- an integrated **Mobile** preview tab (screen + logcat + light metrics);
- tap / swipe / key injection so the agent can verify UI changes.

iOS still requires a Mac with Xcode. Visual regression screenshots and deep source mapping (React DevTools / Flutter Inspector) are not in this release.

## Quick start

1. Open the Dev module on an Expo, React Native, or Flutter project.
2. Run `/mobile android` in chat (or click **Run Mobile** in the workbench toolbar).
3. Wait until a device or emulator appears online.
4. Ask the agent to start preview, or open the **Mobile** tab and click **Start preview**.
5. Click the image to tap; drag to swipe.

Preview works without a native accelerator; a faster path is used when the optional native mobile session is available in your build.

## Slash actions

| Action | Args | Effect |
|---|---|---|
| `/mobile` | `[android\|ios\|web\|metro\|doctor]` | Loads mobile-dev skill, runs detect → doctor → run (or doctor-only). Tracked on the board. |

Examples:

```text
/mobile
/mobile android
/mobile doctor
/mobile web
```

## Mobile tool (agent)

Prefer the built-in `mobile` tool over inventing adb/Metro commands.

### Packager lifecycle

| Action | Purpose |
|---|---|
| `detect` | Identify Expo / React Native / Flutter and package manager |
| `doctor` | Check Node, npm/yarn/pnpm/bun, npx, Java, adb, SDK, emulator, devices |
| `setup` | Discover adb/SDK and auto-install when possible; otherwise return OS-specific steps |
| `devices` | List devices and AVDs |
| `plan` | Show commands without starting anything |
| `run` | Start packager (and optionally an AVD) |
| `logs` | Poll packager session output |
| `stop` | Terminate packager / emulator / agent preview session |

```text
mobile(action="detect")
mobile(action="doctor")
mobile(action="run", target="android")
mobile(action="logs")
mobile(action="stop")
```

`target` for `run` / `plan`: `android` (default), `ios`, `web`, `metro`.

### Interactive Android preview

| Action | Purpose |
|---|---|
| `preview_start` | Open an agent-side preview session |
| `preview_stop` | Stop that session |
| `screenshot` | Grab the latest PNG frame summary |
| `tap` | Tap device pixels (`x`, `y`) |
| `swipe` | Swipe (`x`, `y`, `x2`, `y2`, optional `duration_ms`) |
| `key` | Android keyevent (`BACK`, `HOME`, `ENTER`, …) |
| `text` | Type text into the focused field |
| `ui_dump` | UIAutomator XML hierarchy (testID / text / bounds) |
| `metrics` | FPS estimate, memory, CPU, resolution |

```text
mobile(action="preview_start")
mobile(action="tap", x=540, y=1200)
mobile(action="ui_dump")
mobile(action="metrics")
```

The Dev workbench **Mobile** tab uses the same preview stack independently of the agent tool session.

## Skill

`mobile-dev` teaches the operating loop: detect → doctor → run → edit → logs / preview → verify. Fullstack Dev points mobile workspaces at this skill.

## Workbench UI

In the Dev workbench tab strip:

**Code** · **Preview** (web) · **Mobile** · Graph · Board · …

**Mobile** shows:

- live PNG frames from the device;
- logcat sidebar;
- FPS / memory / CPU readouts;
- Back / Home / Recents keys;
- click = tap, drag = swipe.

Localhost-only (same rule as integrated terminals).

## Stack notes

### Expo

Detected via `expo` dependency and/or `app.json` / `app.config.*`. Default run starts Expo for Android (or the package-manager equivalent).

### React Native CLI

Detected via `react-native` without Expo. Uses project scripts (`start`, `android`) when present.

### Flutter

Detected via `pubspec.yaml` + Flutter SDK. Run targets Android (or chrome / ios). Preview uses the same Android path once a device is online.

## Limitations

- **iOS**: needs macOS + Xcode; Linux/Windows should use Android or Expo web.
- **Source mapping**: `ui_dump` helps match controls by text/testID/bounds; not a full component inspector.
- **Performance metrics**: approximate (screencap FPS, dumpsys/top samples), not a profiler.
- **WSL + USB**: device bridging often needs USB tools on Windows; emulators are usually simpler.

## Related docs

- Chat from a phone (usage PWA, not this preview): [mobile-usage-app.md](./mobile-usage-app.md)
- Dev workbench overview: [navin_dev](./navin_dev/README.md)
- English workbench page: [navin_dev/en/workbench.md](./navin_dev/en/workbench.md)
- French Mobile page: [navin_dev/fr/mobile.md](./navin_dev/fr/mobile.md)
