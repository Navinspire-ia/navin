# Marketing on Tauri - Linux, Windows, macOS

The Marketing desk is not a per-OS binary. **One Rust shell** (`desktop/src-tauri`) opens the splash, starts the Python gateway, then navigates the WebView to `http://127.0.0.1:<port>/#/marketing`. The `navin-dist` sidecar (Linux / Windows / macOS / macos-x64) does not embed Marketing logic: it is the same frozen Python as Career, Tenders, Leads and Trading.

PyInstaller collects `navin.marketing` (and the four other desks) on every OS. Sidecar and smoke tests import `navin.marketing.desk_cli` before the bundle is shipped.

## WebView origins

`is_desktop_app_url` (Rust) and `isDesktopAppUrl` (TypeScript) keep the hash in the app.

| OS | Typical origin | Example that stays in the WebView |
| --- | --- | --- |
| Linux | `http://127.0.0.1:<port>` | `http://127.0.0.1:8766/#/marketing?chat=websocket:1` |
| Windows | splash `https://tauri.localhost` (WebView2), then loopback | `https://tauri.localhost/#/marketing` |
| macOS | `tauri://localhost` | `tauri://localhost/#/marketing` |

`on_navigation`: internal URL → stay. External URL → OS browser, navigation denied.
`on_new_window`: `target=_blank` on `#/marketing` applies the hash on the main window (`apply_in_app_navigation`). Provider and ads consoles leave.

`gateway-opener.json` capability: platforms `linux`, `macOS`, `windows`. Remote hosts: `http://127.0.0.1:*`, `http://tauri.localhost`, `https://tauri.localhost` (WebView2), `tauri://localhost`.

## Splash vs UI bundle

`frontendDist` points at `desktop/ui`: boot page only. Studio UI is the Vite build served by the gateway:

- Source: `webui/`
- Build: `tsc -p tsconfig.build.json && vite build`
- Output: `navin/web/dist/` (lazy chunk `MarketingWorkspace-*.js`)

A per-OS `tauri build` does not add Marketing code. Integration check = same interceptor + same opener + same dist.

## Links and tests

- Rust tests: `tauri_custom_protocol_stays_in_the_webview` (all three origins plus `?chat=`).
- JS tests: `webui/src/lib/desktop.test.ts`.
- Wiring: `tests/test_marketing_integration.py`, `tests/test_desk_tauri_packaging.py` (opener on 3 OS, sidecars without `marketing` / `heartbeat`, smoke import of `desk_cli`).

The agent sandbox allows writes under `~/.navin/marketing`. Produced and harvested files are served by `/api/marketing/file`.

See also: [Desk](./desk.md), [Actions](./actions.md).
