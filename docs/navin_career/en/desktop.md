# Career on Tauri - Linux, Windows, macOS

The Career desk is not a per-OS binary. **One Rust shell** (`desktop/src-tauri`) opens the splash, starts the Python gateway, then navigates the WebView to `http://127.0.0.1:<port>/#/career`. The `navin-dist` sidecar (Linux / Windows / macOS / macos-x64) does not embed Career logic: it is the same frozen Python as Marketing, Tenders, Leads and Trading.

PyInstaller collects `navin.career` on every OS. Sidecar and smoke tests import `navin.career.desk_cli` before the bundle is shipped.

## WebView origins

`is_desktop_app_url` (Rust) and `isDesktopAppUrl` (TypeScript) keep the hash in the app.

| OS | Typical origin | Example that stays in the WebView |
| --- | --- | --- |
| Linux | `http://127.0.0.1:<port>` | `http://127.0.0.1:8766/#/career?job=abc` |
| Windows | splash `https://tauri.localhost` (WebView2), then loopback | `https://tauri.localhost/#/career` |
| macOS | `tauri://localhost` | `tauri://localhost/#/career` |

`on_navigation`: internal URL → stay. External URL → OS browser, navigation denied.
`on_new_window`: `target=_blank` on `#/career` applies the hash on the main window (`apply_in_app_navigation`). Official boards leave.

`gateway-opener.json` capability: platforms `linux`, `macOS`, `windows`. Remote hosts: `http://127.0.0.1:*`, `http://tauri.localhost`, `https://tauri.localhost` (WebView2), `tauri://localhost`.

## Splash vs UI bundle

`frontendDist` points at `desktop/ui`: boot page only. Studio UI is the Vite build served by the gateway:

- Source: `webui/`
- Build: `tsc -p tsconfig.build.json && vite build`
- Output: `navin/web/dist/` (lazy chunk `CareerWorkspace-*.js`)

A per-OS `tauri build` does not add Career code. Integration check = same interceptor + same opener + same dist.

## Links and tests

- Official boards leave the IDE (`official_career_boards_leave_the_ide`).
- Rust tests: `tauri_custom_protocol_stays_in_the_webview` (all three origins plus `?job=`).
- JS tests: `webui/src/lib/desktop.test.ts`, career UI tests.
- Wiring: `tests/test_career_wiring.py`, `tests/test_desk_tauri_packaging.py`.

The agent sandbox allows writes under `~/.navin/career`.

See also: [Overview](./README.md), [Loop](./loop.md).
