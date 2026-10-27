# Android tests

## ADB

```bash
adb devices                                   # emulator or device listed as "device"
adb install -r os/android/android/app/build/outputs/apk/debug/app-debug.apk
adb shell monkey -p <app_id> -c android.intent.category.LAUNCHER 1
adb shell pidof <app_id>                      # non empty = running
adb logcat -d -s AndroidRuntime:E Capacitor:* chromium:*   # crashes and WebView errors
adb exec-out screencap -p > .navin/appfactory/android/screen.png
```

The emulator can be started and driven with the `mobile` tool: `preview_start`, `ui_dump`, `tap`, `screenshot`.

## Maestro

```bash
maestro test -e APP_ID=<app_id> -e HOME_TEXT="Home" -e LOGIN_TEXT="Sign in" -e LOGIN_SCREEN_TEXT="Password" smoke.maestro.yaml
```

Copy `smoke.maestro.yaml` to `os/android/tests/` and adapt the texts to the real app. A missing Maestro is reported as `not_run`, never as passed.
