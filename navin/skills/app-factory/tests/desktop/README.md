# Desktop tests

## Launch the built binary

- Windows: `Start-Process os\windows\src-tauri\target\release\<app>.exe; Start-Sleep 10; Get-Process <app>`
- Linux: `./os/ubuntu/src-tauri/target/release/<app> & sleep 10; kill -0 $! && echo alive` (headless: `xvfb-run -a`)
- AppImage: `chmod +x <file>.AppImage && xvfb-run -a ./<file>.AppImage & sleep 10; pgrep -f <file>`
- macOS: `open os/macos/src-tauri/target/release/bundle/macos/<App>.app; sleep 10; pgrep -x <App>`

The process must still be alive after 10 s and stderr must show no panic.

## WebDriver

`tauri-driver` (`cargo install tauri-driver --locked`) gives WebDriver on Windows (needs `msedgedriver` matching the WebView2 version) and Linux (needs `WebKitWebDriver`, package `webkit2gtk-driver`). It is not available on macOS.

## Playwright against the web build

```bash
npx serve -s <frontendDist> -l 4173 &
BASE_URL=http://localhost:4173 npx playwright test smoke.spec.ts
```

Copy `smoke.spec.ts` to `os/<target>/tests/` and adapt the selectors.
