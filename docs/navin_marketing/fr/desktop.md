# Marketing dans Tauri - Linux, Windows, macOS

Le desk Marketing n'est pas un binaire par OS. **Un seul shell Rust** (`desktop/src-tauri`) ouvre le splash, demarre le gateway Python, puis navigue le WebView vers `http://127.0.0.1:<port>/#/marketing`. Le sidecar `navin-dist` (Linux / Windows / macOS / macos-x64) n'embarque pas de logique Marketing : c'est le meme Python gele que Career, Tenders, Leads et Trading.

PyInstaller collecte `navin.marketing` (et les quatre autres desks) sur chaque OS. Les builds sidecar et les smoke tests importent `navin.marketing.desk_cli` avant l'expedition.

## Origines WebView

`is_desktop_app_url` (Rust) et `isDesktopAppUrl` (TypeScript) gardent le hash dans l'app.

| OS | Origine typique | Exemple qui reste dans le WebView |
| --- | --- | --- |
| Linux | `http://127.0.0.1:<port>` | `http://127.0.0.1:8766/#/marketing?chat=websocket:1` |
| Windows | splash `https://tauri.localhost` (WebView2), puis loopback | `https://tauri.localhost/#/marketing` |
| macOS | `tauri://localhost` | `tauri://localhost/#/marketing` |

`on_navigation` : URL interne → rester. URL externe → navigateur OS, navigation refusee.
`on_new_window` : `target=_blank` sur `#/marketing` applique le hash dans la fenetre principale (`apply_in_app_navigation`). Les consoles providers et ads sortent.

Capability `gateway-opener.json` : platforms `linux`, `macOS`, `windows`. Hosts distants : `http://127.0.0.1:*`, `http://tauri.localhost`, `https://tauri.localhost` (WebView2), `tauri://localhost`.

## Splash vs bundle UI

`frontendDist` pointe vers `desktop/ui` : page de boot seulement. L'UI Studio est le build Vite servi par le gateway :

- Source : `webui/`
- Build : `tsc -p tsconfig.build.json && vite build`
- Sortie : `navin/web/dist/` (chunk lazy `MarketingWorkspace-*.js`)

Un `tauri build` par OS n'ajoute pas de code Marketing. Verifier l'integration = meme interceptor + meme opener + meme dist.

## Liens et tests

- Tests Rust : `tauri_custom_protocol_stays_in_the_webview` (les 3 origines + `?chat=`).
- Tests JS : `webui/src/lib/desktop.test.ts`.
- Cablage : `tests/test_marketing_integration.py`, `tests/test_desk_tauri_packaging.py` (opener 3 OS, sidecars sans `marketing` / `heartbeat`, import smoke de `desk_cli`).

Le sandbox agent autorise l'ecriture dans `~/.navin/marketing`. Les fichiers produits et moissonnes sont servis par `/api/marketing/file`.

Voir aussi : [Bureau](./desk.md), [Actions](./actions.md).
