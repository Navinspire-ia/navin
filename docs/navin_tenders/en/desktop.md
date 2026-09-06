# Tenders on Tauri - Linux, Windows, macOS

The Tenders desk is not a per-OS binary. **One Rust shell** (`desktop/src-tauri`) opens the splash, starts the Python gateway, then navigates the WebView to `http://127.0.0.1:<port>/#/tenders`. The `navin-dist` sidecar (Linux / Windows / macOS / macos-x64) does not embed Tenders logic: it is the same frozen Python as Career, Marketing, Leads and Trading.

PyInstaller collects `navin.tenders` on every OS. Sidecar and smoke tests import `navin.tenders.desk_cli` before the bundle is shipped.

## WebView origins

`is_desktop_app_url` (Rust) and `isDesktopAppUrl` (TypeScript) keep the hash in the app.

| OS | Typical origin | Example that stays in the WebView |
| --- | --- | --- |
| Linux | `http://127.0.0.1:<port>` | `http://127.0.0.1:8766/#/tenders?notice=abc` |
| Windows | splash `https://tauri.localhost` (WebView2), then loopback | `https://tauri.localhost/#/tenders?pane=tenders` |
| macOS | `tauri://localhost` | `tauri://localhost/#/tenders` |

`on_navigation`: internal URL → stay. External URL → OS browser, navigation denied.
`on_new_window`: `target=_blank` on `#/tenders` applies the hash on the main window (`apply_in_app_navigation`). Official portals leave.

`gateway-opener.json` capability: platforms `linux`, `macOS`, `windows`. Remote hosts: `http://127.0.0.1:*`, `http://tauri.localhost`, `https://tauri.localhost` (WebView2), `tauri://localhost`. `https://**` permissions for TED and the rest.

## Splash vs UI bundle

`frontendDist` points at `desktop/ui`: boot page only. Studio UI is the Vite build served by the gateway:

- Source: `webui/`
- Build: `tsc -p tsconfig.build.json && vite build`
- Output: `navin/web/dist/` (lazy chunk `TendersWorkspace-*.js`)

A per-OS `tauri build` does not add Tenders code. Integration check = same interceptor + same opener + same dist.

## Links and tests

- Catalog: `webui/src/lib/tender-catalog-urls.json` - every URL **leaves** the IDE.
- Rust tests: `tauri_custom_protocol_stays_in_the_webview` (all three origins plus `?notice=` / `?pane=tenders`).
- JS tests: `webui/src/lib/desktop.test.ts`, `tenders-api.test.ts`.
- Wiring: `tests/test_tenders_module.py`, `tests/test_desk_tauri_packaging.py`.

The agent sandbox allows writes under `~/.navin/tenders`.

See also: [Overview](./README.md), [Actions](./actions.md).
