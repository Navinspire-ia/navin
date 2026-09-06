# Career dans Tauri - Linux, Windows, macOS

Le desk Career n'est pas un binaire par OS. **Un seul shell Rust** (`desktop/src-tauri`) ouvre le splash, demarre le gateway Python, puis navigue le WebView vers `http://127.0.0.1:<port>/#/career`. Le sidecar `navin-dist` (Linux / Windows / macOS / macos-x64) n'embarque pas de logique Career : c'est le meme Python gele que Marketing, Tenders, Leads et Trading.

PyInstaller collecte `navin.career` sur chaque OS. Les builds sidecar et les smoke tests importent `navin.career.desk_cli` avant l'expedition.

## Origines WebView

`is_desktop_app_url` (Rust) et `isDesktopAppUrl` (TypeScript) gardent le hash dans l'app.

| OS | Origine typique | Exemple qui reste dans le WebView |
| --- | --- | --- |
| Linux | `http://127.0.0.1:<port>` | `http://127.0.0.1:8766/#/career?job=abc` |
| Windows | splash `https://tauri.localhost` (WebView2), puis loopback | `https://tauri.localhost/#/career` |
| macOS | `tauri://localhost` | `tauri://localhost/#/career` |

`on_navigation` : URL interne → rester. URL externe → navigateur OS, navigation refusee.
`on_new_window` : `target=_blank` sur `#/career` applique le hash dans la fenetre principale (`apply_in_app_navigation`). Les boards officiels sortent.

Capability `gateway-opener.json` : platforms `linux`, `macOS`, `windows`. Hosts distants : `http://127.0.0.1:*`, `http://tauri.localhost`, `https://tauri.localhost` (WebView2), `tauri://localhost`.

## Splash vs bundle UI

`frontendDist` pointe vers `desktop/ui` : page de boot seulement. L'UI Studio est le build Vite servi par le gateway :

- Source : `webui/`
- Build : `tsc -p tsconfig.build.json && vite build`
- Sortie : `navin/web/dist/` (chunk lazy `CareerWorkspace-*.js`)

Un `tauri build` par OS n'ajoute pas de code Career. Verifier l'integration = meme interceptor + meme opener + meme dist.

## Liens et tests

- Les boards officiels quittent l'IDE (`official_career_boards_leave_the_ide`).
- Tests Rust : `tauri_custom_protocol_stays_in_the_webview` (les 3 origines + `?job=`).
- Tests JS : `webui/src/lib/desktop.test.ts`.
- Cablage : `tests/test_career_wiring.py`, `tests/test_desk_tauri_packaging.py`.

Le sandbox agent autorise l'ecriture dans `~/.navin/career`.

Voir aussi : [Vue d'ensemble](./README.md), [Loop](../en/loop.md).
