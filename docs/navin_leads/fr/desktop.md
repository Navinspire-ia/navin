# Leads dans Tauri - Linux, Windows, macOS

Le desk Leads n'est pas un binaire par OS. **Un seul shell Rust** (`desktop/src-tauri`) ouvre le splash, demarre le gateway Python, puis navigue le WebView vers `http://127.0.0.1:<port>/#/leads`. Le sidecar `navin-dist` (Linux / Windows / macOS / macos-x64) n'embarque pas de logique Leads : c'est le meme Python gele que Career, Marketing, Tenders et Trading. PyInstaller collecte `navin.leads` sur chaque OS. Les builds sidecar et les smoke tests importent `navin.leads.desk_cli` avant l'expedition.

## Origines WebView

`is_desktop_app_url` (Rust) et `isDesktopAppUrl` (TypeScript) gardent le hash dans l'app.

| OS | Origine typique | Exemple qui reste dans le WebView |
| --- | --- | --- |
| Linux | `http://127.0.0.1:<port>` | `http://127.0.0.1:8766/#/leads?lead=abc` |
| Windows | splash `https://tauri.localhost` (WebView2), puis loopback | `https://tauri.localhost/#/leads?pane=book` |
| macOS | `tauri://localhost` | `tauri://localhost/#/leads` |

`on_navigation` : URL interne → rester. URL externe → navigateur OS, navigation refusee.
`on_new_window` : `target=_blank` sur `#/leads` applique le hash dans la fenetre principale (`apply_in_app_navigation`). Les portails officiels sortent.

Capability `gateway-opener.json` : platforms `linux`, `macOS`, `windows`. Hosts distants : `http://127.0.0.1:*`, `http://tauri.localhost`, `https://tauri.localhost` (WebView2), `tauri://localhost`. Permissions `https://**` pour Hunter, SIRENE, Apollo, etc.

## Splash vs bundle UI

`frontendDist` pointe vers `desktop/ui` : page de boot seulement. L'UI Studio est le build Vite servi par le gateway :

- Source : `webui/`
- Build : `tsc -p tsconfig.build.json && vite build`
- Sortie : `navin/web/dist/` (chunk lazy `LeadsWorkspace-*.js`)

Un `tauri build` par OS n'ajoute pas de code Leads. Verifier l'integration = meme interceptor + meme opener + meme dist.

## Liens et tests

- Catalog providers : `webui/src/lib/leads-catalog-urls.json` - chaque URL **quitte** l'IDE (`every_leads_catalog_url_leaves_the_ide`).
- Tests Rust : `tauri_custom_protocol_stays_in_the_webview` (les 3 origines + `?lead=` / `?pane=book`).
- Tests JS : `webui/src/lib/desktop.test.ts`, `leads-api.test.ts`, `leads-ui.test.ts`.
- Cablage : `tests/test_leads_wiring.py`, `tests/test_desk_tauri_packaging.py` (opener 3 OS, sidecars sans `leads` / `heartbeat`).

Le sandbox agent autorise l'ecriture dans `~/.navin/leads`.

Voir aussi : [Bureau](./desk.md), [CLI et API](./cli-api.md).
