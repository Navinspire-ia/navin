# Leads on Tauri - Linux, Windows, macOS

The Leads desk is not a per-OS binary. **One Rust shell** (`desktop/src-tauri`) opens the splash, starts the Python gateway, then navigates the WebView to `http://127.0.0.1:<port>/#/leads`. The `navin-dist` sidecar (Linux / Windows / macOS / macos-x64) does not embed Leads logic: it is the same frozen Python as Career, Marketing, Tenders and Trading. PyInstaller collects `navin.leads` on every OS. Sidecar and smoke tests import `navin.leads.desk_cli` before the bundle is shipped.

## WebView origins

`is_desktop_app_url` (Rust) and `isDesktopAppUrl` (TypeScript) keep the hash in the app.

| OS | Typical origin | Example that stays in the WebView |
| --- | --- | --- |
| Linux | `http://127.0.0.1:<port>` | `http://127.0.0.1:8766/#/leads?lead=abc` |
| Windows | splash `https://tauri.localhost` (WebView2), then loopback | `https://tauri.localhost/#/leads?pane=book` |
| macOS | `tauri://localhost` | `tauri://localhost/#/leads` |

`on_navigation`: internal URL → stay. External URL → OS browser, navigation denied.
`on_new_window`: `target=_blank` on `#/leads` applies the hash on the main window (`apply_in_app_navigation`). Official portals leave.

`gateway-opener.json` capability: platforms `linux`, `macOS`, `windows`. Remote hosts: `http://127.0.0.1:*`, `http://tauri.localhost`, `https://tauri.localhost` (WebView2), `tauri://localhost`. `https://**` permissions for Hunter, SIRENE, Apollo, and the rest.

## Splash vs UI bundle

`frontendDist` points at `desktop/ui`: boot page only. Studio UI is the Vite build served by the gateway:

- Source: `webui/`
- Build: `tsc -p tsconfig.build.json && vite build`
- Output: `navin/web/dist/` (lazy chunk `LeadsWorkspace-*.js`)

A per-OS `tauri build` does not add Leads code. Integration check = same interceptor + same opener + same dist.

## Links and tests

- Provider catalog: `webui/src/lib/leads-catalog-urls.json` - every URL **leaves** the IDE (`every_leads_catalog_url_leaves_the_ide`).
- Rust tests: `tauri_custom_protocol_stays_in_the_webview` (all three origins plus `?lead=` / `?pane=book`).
- JS tests: `webui/src/lib/desktop.test.ts`, `leads-api.test.ts`, `leads-ui.test.ts`.
- Wiring: `tests/test_leads_wiring.py`, `tests/test_desk_tauri_packaging.py` (opener on 3 OS, sidecars without `leads` / `heartbeat`).

The agent sandbox allows writes under `~/.navin/leads`.

See also: [Desk](./desk.md), [CLI and API](./cli-api.md).
