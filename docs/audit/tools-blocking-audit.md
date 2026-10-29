# Audit anti-blocage des outils Navin (Windows, macOS, Windows + WSL)

Date : 2026-10-29. Branche : `prod-v2`. Portee : tous les outils de l'agent
(`navin/agent/tools/**`) et la couche plateforme sur laquelle ils reposent
(`navin/utils/proc.py`, `wsl*.py`, `git_*.py`, `navin/quality/*`, `navin/lsp/*`,
`navin/dap/*`, `navin/index/*`, `navin/process_runtime.py`, routes WebUI
fichiers / recherche / terminal / git).

## Pourquoi

Chaque outil tourne en `async def execute` sur l'unique boucle asyncio de la
gateway, partagee par toutes les conversations, la WebUI et le terminal. Un
appel synchrone lent ecrit directement dans une coroutine (un `subprocess.run`,
un `os.walk`, un `shutil.which` qui parcourt `/mnt/c`, une resolution DNS) ne
ralentit pas un outil : il gele toute l'application. Sur Windows s'ajoutent les
specificites du desktop sans console, des shims `.cmd`, des arbres de processus
a tuer, et, quand le projet est dans WSL, le redirecteur 9P (`\\wsl.localhost`)
ou un `stat` coute une milliseconde et un parcours d'arbre des minutes.

## Preuves

`~/.navin/logs/gateway.log` (Navin tournant dans WSL) : 403 rapports
`event loop blocked` et 24 571 routes HTTP lentes. Coupables classes :

| Coupable (fichier) | Rapports | Max | Statut |
|---|---|---|---|
| `_run_gateway` / `select` (processus non ordonnance, pause de la VM) | 81 | - | faux positif |
| `session.list_sessions` (scandir) | 49 | 4 s | deja en `to_thread` (`ws_http.py`) |
| `terminal_ws.available_shells` (PATH sur `/mnt/c`) | 18 | 11 s | corrige : voie synchrone `open()` refusee sur la boucle, `open_async` en thread |
| `cron._atomic_write` | 11 | 2 s | tick deja en thread ; sauvegardes synchrones volontaires (relecture disque par les autres instances) |
| `wsl.open_url_on_host` | 9 | 13 s | deja hors boucle cote appelant |
| `gitstore` / dulwich `directory_has_non_ignored_files` | 8 | 31 s | anterieur a la migration gitstore (16 oct) |
| `decrypt_config_data` / `load_config` | 14 | 1-3 s | corrige : `cloud_sandbox` via `to_thread` ; autres appelants deja en thread |
| `estimate_message_tokens` | 8 | 1 s | compteur de tokens, borne par la taille du contexte |
| `sidebar_state` (fsync) | 4 | 2,4 s | corrige : `to_thread` |
| `marketing_api.list_marketing_qa_assets` (`rglob` dans `node_modules`) | 2 | 31 s | corrige : `os.walk` elague + `to_thread` |
| `stall_watch` passe GC inactive | 2 | 3 s | deja bornee a deux passes |

## Criteres appliques

1. Spawn synchrone (`subprocess.run/call/check_*`, `Popen` + `wait`/`communicate`) sans timeout borne.
2. Appel synchrone lent dans une coroutine sans `asyncio.to_thread` / `run_in_executor` (ou subprocess asyncio sans `wait_for`).
3. Risque de deadlock de pipe (`PIPE` puis `wait()` avant de vider).
4. API POSIX-only (`fcntl`, `pty`, `termios`, `select.select` sur des pipes, `os.killpg`, `os.setsid`, `SIGKILL`) atteignable sur Windows sans garde de plateforme.
5. Specificites Windows : shim `.cmd`/`.bat` sans shell, decodage cp1252 d'une sortie UTF-8, arbre de processus laisse vivant apres annulation, console manquante.
6. WSL : commande routee via `wsl.exe` sans timeout ou sans `wsl_health.suspect` / `answered` ; outil du projet lance cote Windows (mauvais binaires, lectures 9P).
7. macOS : spawn pouvant ouvrir une invite graphique (Xcode CLT, permissions) sans timeout.
8. Accumulation non bornee (lecture d'un flux sans fin, reseau sans cap).

## Correctifs par zone

**Shell, exec, sessions, terminal** (`shell.py`, `process_runtime.py`, `terminal_ws.py`)
`wsl.exe` introuvable signale la distribution suspecte puis relance l'erreur ;
`shutil.which` mis en cache (TTL) au lieu d'un parcours de PATH par appel ;
`taskkill` (arbre de processus) borne a 10 s, sans console, hors boucle ;
fermeture d'un onglet terminal Windows hors boucle ; `TerminalManager.open()`
refuse de resoudre le shell sur une boucle en cours et renvoie vers
`open_async`.

**Fichiers, recherche, index** (`search.py`, `project_search.py`, `filesystem.py`, `index/semantic.py`)
ripgrep silencieux via `wsl.exe` tue a l'echeance (`threading.Timer`), lignes
partielles rendues ; `suspect`/`answered` sur chaque lancement WSL ;
verification « fichier modifie depuis la lecture » et instructions de projet
hors boucle ; lectures et ecritures de l'index semantique en thread.

**Qualite, git, projet, LSP, DAP** (19 correctifs)
resolution de la route git hors boucle (10 s a froid : `wsl -l -q`) ;
`wsl_route.note_launch` (suspect/answered) partout ou `wsl.exe` est lance
(git, git_state, verify, quality, linters, testing, git_setup, git_clone) ;
`product_ui` et `test_explorer` routes dans la distribution au lieu de lire via
9P ; sonde `xcode-select -p` en cache sur macOS pour ne plus declencher le
dialogue Xcode a chaque appel git ; serveur de preview, decouverte des apps et
`project` runtime hors boucle ; serveurs LSP tues a la sortie du processus ;
**serveur LSP lance dans la distribution** pour un projet `\\wsl.localhost`
(`WslPathMapping` : UNC vers `/home/...`, `C:\` vers `/mnt/c`, reponses
retraduites en chemins projet) ; **fast-fail** `wsl_health.refuse_reason` /
`wsl_route.refusal` : une fois WSL confirme bloque, git et la barre d'etat
repondent immediatement avec la raison et la commande `wsl --shutdown` au lieu
de payer chacun leur timeout.

**Web, navigateur, computer, MCP, reseau** (`browser.py`, `browser_use_bridge.py`, `mcp.py`, `scrape.py`, `cloud_sandbox.py`, `computer.py`)
arret de Chromium borne a 15 s par etape ; `evaluate` et CDP bornes a 60 s avec
erreur lisible ; resolutions DNS (garde SSRF) hors boucle ; browser-use :
demarrage 180 s, action 120 s ; shims `npx.cmd`/`npm.cmd`/`uvx` resolus pour
les serveurs MCP stdio et lecteur stderr ferme hors boucle ; fetch de sitemap
(httpx synchrone + back-off) deplace en worker ; chargement de la config et
construction du backend natif hors boucle.

**Plateforme** (`marketing_api.py`, `ws_http.py`)
parcours des assets marketing elague (`node_modules`, `.git`, `.venv`) et hors
boucle ; ecriture de l'etat de la barre laterale hors boucle.

**Desktop Linux** (`desktop/src-tauri/src/main.rs`)
WebKitGTK refusait silencieusement les notifications systeme que WebView2
affichait : la demande de permission `Notification` est maintenant traitee
comme le micro, accordee pour l'origine locale de Navin, refusee ailleurs.

## Garantie permanente

`tests/test_no_blocking_tools.py` parcourt l'arbre syntaxique du perimetre et
echoue des qu'un nouveau site enfreint une regle : spawn synchrone sans
`timeout=` ; `communicate()`/`wait()` de processus sans timeout ni
`asyncio.wait_for` ; `time.sleep`, `shutil.which`, `subprocess.*`, `os.walk`,
`rglob` ecrits directement dans une coroutine ; appel POSIX-only sans test de
plateforme ; import `fcntl`/`termios`/`tty`/`resource`/`pwd`/`grp` au niveau
module. Les exceptions sont nominatives et motivees (`_EXEMPT`), et un test
signale celles devenues obsoletes. `tests/test_no_console_window.py` couvre
deja la console Windows ; `tests/test_tool_latency.py` mesure l'ecart de boucle
par outil (au plus 28 ms sur cette machine).

## Verification

`ruff check navin/ tests/` propre. Tests cibles verts par zone : shell/terminal
211 + 31 (hors sandbox), fichiers/recherche 217, qualite/git/LSP 787, web/MCP
186, plateforme 189, LSP WSL + terminal + git 593, garde AST 5. Les trois
echecs `pty` observes dans le sandbox (« out of pty devices ») sont une limite du
sandbox, verts hors sandbox.

## Limites

- Aucune machine Windows ni macOS n'etait disponible : analyse statique et tests
  Linux avec simulation de plateforme (`sys.platform`, `wsl.exe` simule). Le
  « zero blocage » n'est donc pas mesure sur ces OS ; ce qui est garanti, c'est
  que chaque chemin audite a un timeout, s'execute hors boucle ou est garde par
  plateforme, et que la garde AST empeche la regression.
- Points laisses en l'etat, mineurs : `lsp/client.py` et `dap/client.py` font
  `kill()` sans `wait()` final (zombie possible sur POSIX) ; `testing.py`
  `_run_streaming` tue le runner mais pas ses enfants ; `lsp/manager.py`
  `default_workspace_seeds` parcourt le projet (deja hors boucle et plafonne).
- Tuer `wsl.exe` ne garantit pas la fin du processus Linux qu'il relaye ; les
  serveurs LSP lances dans la distribution recoivent la fin de stdin et
  s'arretent d'eux-memes dans la pratique.
