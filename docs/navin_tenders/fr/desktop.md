# Tenders dans Tauri - Linux, Windows, macOS

Le desk Tenders n'est pas un binaire par OS. **Un seul shell Rust** (`desktop/src-tauri`) ouvre le splash, demarre le gateway Python, puis navigue le WebView vers `http://127.0.0.1:<port>/#/tenders`. Le sidecar `navin-dist` (Linux / Windows / macOS / macos-x64) n'embarque pas de logique Tenders : c'est le meme Python gele que Career, Marketing, Leads et Trading.

PyInstaller collecte `navin.tenders` sur chaque OS. Les builds sidecar et les smoke tests importent `navin.tenders.desk_cli` avant l'expedition.

## Origines WebView

`is_desktop_app_url` (Rust) et `isDesktopAppUrl` (TypeScript) gardent le hash dans l'app.

| OS | Origine typique | Exemple qui reste dans le WebView |
| --- | --- | --- |
| Linux | `http://127.0.0.1:<port>` | `http://127.0.0.1:8766/#/tenders?notice=abc` |
| Windows | splash `https://tauri.localhost` (WebView2), puis loopback | `https://tauri.localhost/#/tenders?pane=tenders` |
| macOS | `tauri://localhost` | `tauri://localhost/#/tenders` |

`on_navigation` : URL interne → rester. URL externe → navigateur OS, navigation refusee.
`on_new_window` : `target=_blank` sur `#/tenders` applique le hash dans la fenetre principale (`apply_in_app_navigation`). Les portails officiels sortent.

Capability `gateway-opener.json` : platforms `linux`, `macOS`, `windows`. Hosts distants : `http://127.0.0.1:*`, `http://tauri.localhost`, `https://tauri.localhost` (WebView2), `tauri://localhost`. Permissions `https://**` pour TED et le reste.

## Splash vs bundle UI

`frontendDist` pointe vers `desktop/ui` : page de boot seulement. L'UI Studio est le build Vite servi par le gateway :

- Source : `webui/`
- Build : `tsc -p tsconfig.build.json && vite build`
- Sortie : `navin/web/dist/` (chunk lazy `TendersWorkspace-*.js`)

Un `tauri build` par OS n'ajoute pas de code Tenders. Verifier l'integration = meme interceptor + meme opener + meme dist.

## Liens et tests

- Catalog : `webui/src/lib/tender-catalog-urls.json` - chaque URL **quitte** l'IDE.
- Tests Rust : `tauri_custom_protocol_stays_in_the_webview` (les 3 origines + `?notice=` / `?pane=tenders`).
- Tests JS : `webui/src/lib/desktop.test.ts`, `tenders-api.test.ts`.
- Cablage : `tests/test_tenders_module.py`, `tests/test_desk_tauri_packaging.py`.

Le sandbox agent autorise l'ecriture dans `~/.navin/tenders`.

Voir aussi : [Vue d'ensemble](./README.md), [Actions](./actions.md).
