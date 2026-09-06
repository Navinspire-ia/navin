# Mobile

Run and preview Expo, React Native, and Flutter apps inside the Dev workbench.

Full technical reference: [docs/mobile.md](../../mobile.md).

## Where is the Mobile tab?

Dev workbench (`#/code`) → center tab strip, next to web **Preview**:

**Code** · **Preview** · **Mobile** · Graph · …

Also: toolbar button **Run Mobile**, and Actions → Quality → **Run Mobile**.

## Quick start

1. Open an Expo / React Native / Flutter project in Dev.
2. In chat: `/mobile android` (or click **Run Mobile**).
3. Ensure an Android emulator or USB device appears in `adb devices`.
4. Ask for a live preview (`mobile(action="preview_start")`) - the **Mobile** tab opens and starts automatically (or open the tab and click **Start preview**).
5. Click the screen to tap; drag to swipe. Use Back / Home / Recents in the toolbar.

## Chat command

| Command | Example |
|---|---|
| `/mobile` | `/mobile android`, `/mobile doctor`, `/mobile web` |

Loads the `mobile-dev` skill and drives the `mobile` tool (detect → doctor → run → preview).

## Agent tool (summary)

```text
mobile(action="detect")
mobile(action="doctor")
mobile(action="run", target="android")
mobile(action="preview_start")
mobile(action="tap", x=540, y=1200)
mobile(action="ui_dump")
mobile(action="logs")
mobile(action="stop")
```

## Requirements

- Node.js (Expo / React Native) or Flutter SDK
- Android SDK platform-tools (`adb`) for device preview
- Optional: rebuild Rust accelerator with `make native` for faster capture

## Limits

- iOS needs a Mac with Xcode.
- Preview is Android (adb screencap). Expo **web** target skips the device panel.
- Component → source file mapping uses `ui_dump` (testID / labels), not a full inspector yet.
