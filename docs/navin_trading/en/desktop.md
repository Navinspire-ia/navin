# Trading on Tauri - Linux, Windows, macOS

The Trading desk is not a per-OS binary. **One Rust shell** (`desktop/src-tauri`) opens the splash, starts the Python gateway, then navigates the WebView to `http://127.0.0.1:<port>/#/trading`. The `navin-dist` sidecar (Linux / Windows / macOS / macos-x64) does not embed Trading logic: it is the same frozen Python as Career, Marketing, Tenders and Leads.

PyInstaller collects `navin.trading` on every OS. Sidecar and smoke tests import `navin.trading.desk_cli` before the bundle is shipped.

## WebView origins

`is_desktop_app_url` (Rust) and `isDesktopAppUrl` (TypeScript) keep the hash in the app.

| OS | Typical origin | Example that stays in the WebView |
| --- | --- | --- |
| Linux | `http://127.0.0.1:<port>` | `http://127.0.0.1:8766/#/trading?chat=websocket:1` |
| Windows | splash `https://tauri.localhost` (WebView2), then loopback | `https://tauri.localhost/#/trading` |
| macOS | `tauri://localhost` | `tauri://localhost/#/trading` |

`on_navigation`: internal URL → stay. External URL → OS browser, navigation denied.
`on_new_window`: `target=_blank` on `#/trading` applies the hash on the main window (`apply_in_app_navigation`). Broker and chart sites leave.

`gateway-opener.json` capability: platforms `linux`, `macOS`, `windows`. Remote hosts: `http://127.0.0.1:*`, `http://tauri.localhost`, `https://tauri.localhost` (WebView2), `tauri://localhost`.

## Splash vs UI bundle

`frontendDist` points at `desktop/ui`: boot page only. Studio UI is the Vite build served by the gateway:

- Source: `webui/`
- Build: `tsc -p tsconfig.build.json && vite build`
- Output: `navin/web/dist/` (lazy chunk `TradingWorkspace-*.js`)

A per-OS `tauri build` does not add Trading code. Integration check = same interceptor + same opener + same dist.

## Links and tests

- Rust tests: `tauri_custom_protocol_stays_in_the_webview` (all three origins plus `?chat=`).
- JS tests: `webui/src/lib/desktop.test.ts`.
- Wiring: `tests/test_trading_wiring.py`, `tests/test_desk_tauri_packaging.py`.

The agent sandbox allows writes under `~/.navin/trading`. V1 is paper only. Never invent a fill.

See also: [Overview](./README.md), [Loop](./loop.md).
