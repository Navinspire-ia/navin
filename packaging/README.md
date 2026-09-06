# Packaging

La distribution est **100 % Tauri** : le seul produit livré aux utilisateurs
est l'app desktop native (fenêtre Rust + WebView système) qui embarque le
moteur Navin en *sidecar* (un exécutable PyInstaller autonome). Les machines
cibles n'ont besoin ni de Python, ni de npm, ni d'un checkout source, ni
d'une connexion Internet pour installer et lancer les fonctions locales.

Artefacts publiés, par plateforme :

| Plateforme | Artefacts | Sortie |
| --- | --- | --- |
| Windows x64 | `Navin-Desktop-<v>-windows-x64-setup.exe` (NSIS) + `.msi` (WiX) | `os/windows/x64/` |
| macOS arm64 / x64 | `Navin-Desktop-<v>-macos-<arch>.dmg` | `os/macos/<arch>/` |
| Linux x64 | `Navin-<v>-x86_64.AppImage` + `navin_<v>_amd64.deb` + `navin-<v>.x86_64.rpm` + `navin-<v>-1-x86_64.pkg.tar.zst` | `os/linux/{appimage,deb,rpm,pacman}/x64/` |

PyInstaller ne cross-compile pas : chaque cible se construit sur son propre
OS - localement ou via le workflow GitHub Actions `os-release.yml`.

## Construire

Chaque plateforme suit le même schéma en deux temps : d'abord le **sidecar**
(l'exécutable autonome du moteur), puis l'**app desktop Tauri** qui l'embarque.

### Linux

```bash
make linux       # sidecar → os/linux/bin/x64/navin-dist/
make appimage    # app desktop Tauri → AppImage + .deb + .rpm + .pkg.tar.zst dans os/linux/
```

`make appimage-release` fait le même build dans un conteneur Ubuntu 22.04
(Docker), pour que les bundles tournent aussi sur des distributions plus
anciennes (glibc >= 2.35). Machine de build : Rust (rustup), Node.js et les
paquets WebKitGTK listés dans [`desktop/README.md`](../desktop/README.md).

### Windows

```bash
make windows     # tout-en-un : prérequis (winget) + sidecar + app desktop
```

Depuis Windows ou depuis WSL (interop), cela exécute
`packaging/windows/build-desktop.ps1` : le script construit d'abord le sidecar
`navin-dist\navin.exe` (arborescence PyInstaller *onedir*, via
`build-offline.ps1`, jamais publiée seule - embarquée comme ressource pour un
démarrage instantané, sans auto-extraction), puis produit le `.msi` (WiX) et
le setup `.exe` (NSIS) dans `os/windows/x64/`.
Machine de build : Python 3.11+, Node.js, Rust (MSVC) et les VS C++ Build
Tools - installés automatiquement si absents.

### macOS

```bash
make macos        # sidecars : arm64 + x64 (via Rosetta sur Apple Silicon)
make desktop-dmg  # DMG Tauri : arm64 + x64 (selon sidecars présents)
```

Un seul `make macos` sur un Mac Apple Silicon produit **les deux**
architectures : la passe native donne `os/macos/arm64/navin-dist/`, puis le
script se relance sous Rosetta (`arch -x86_64`) pour produire un vrai sidecar
Intel `os/macos/x64/navin-dist/` (Macs d'avant 2021). Si Rosetta n'est pas
installée (`softwareupdate --install-rosetta`), seule l'arm64 est produite et
le DMG x64 est sauté avec un avertissement.

`make desktop-dmg` construit ensuite un DMG par architecture disponible (le
sidecar *onedir* est embarqué dans `Contents/Resources/navin-dist`, démarrage
instantané). Sans Mac, le workflow `os-release.yml` (cible `macos`) construit
arm64 sur `macos-14` et Intel sur `macos-15-intel`.

Le DMG contient `Navin.app` **et** un alias vers `/Applications` : c'est la
seule chose qui installe quoi que ce soit sur macOS, où il n'y a pas
d'installateur comme le NSIS Windows ou le `.deb`. Filet de sécurité côté app
(`desktop/src-tauri/src/main.rs`) : lancée depuis le volume monté ou depuis une
copie translocatée par Gatekeeper, elle se copie dans `Applications` (ou
`~/Applications` sans droits admin), relance la copie installée et éjecte
l'image. Sans ça, l'app tourne sur un volume en lecture seule : rien ne survit
à l'éjection, `navin` en terminal pointe dans l'image, et le *self-update*
refuse de remplacer un bundle qu'il ne peut pas écrire.

Signature : sans identité Apple, les DMG sont signés ad-hoc et Gatekeeper
demande une confirmation au premier lancement (clic droit → **Ouvrir**). Pour
une distribution sans friction, exporter `MACOS_SIGN_IDENTITY` (certificat
*Developer ID Application*) et les variables de notarisation avant le build.

## Publier

```bash
make aws-upload                 # publie os/ vers S3 sous v<version>/
make aws-upload VERSION=1.1.0   # autre version
```

`scripts/publish-os-to-s3.sh` lit les identifiants AWS dans `.env`, téléverse
chaque artefact trouvé sous `v<version>/` (même nom = écrasé, nouvelle
version = nouveau préfixe), génère le `SHA256SUMS.txt`, et met à jour
`releases.json` à la racine du bucket. Le site lit ce manifeste
dynamiquement : aucune édition manuelle de `site/src/lib/releases.ts` n'est
nécessaire pour publier une version. Les artefacts absents (macOS pas encore
construit, par exemple) sont signalés sans bloquer, et un second passage
complète le manifeste.

Le manifeste doit être lisible publiquement, sinon `getReleases()` retombe
sans erreur sur le catalogue de secours et la page de téléchargement continue
d'annoncer l'ancienne version alors que les binaires sont bien en ligne. La
politique du bucket n'ouvrait que `v*/*`, ce qui laissait cet objet à la racine
fermé : `scripts/s3-bucket-policy.json` est la politique complète à appliquer
(`aws s3api put-bucket-policy --bucket navinagent --policy
file://scripts/s3-bucket-policy.json`). Le script vérifie désormais l'URL après
l'envoi et échoue franchement si elle ne répond pas 200.

## Réinstaller par-dessus : ce qui se passe vraiment

Aucune plateforme ne fait « désinstaller puis installer », sauf le MSI.

| App desktop | Enchaînement | Fichiers de l'ancienne version |
| --- | --- | --- |
| Windows (NSIS / MSI) | Vrai installateur. Le MSI fait une mise à niveau majeure pilotée par le numéro de version : il retire la précédente puis installe. | Supprimés |
| macOS (DMG) | Aucun installateur : le Finder échange le dossier `Navin.app` entier (l'ancien part à la corbeille). | Supprimés |
| Linux (deb / rpm / pacman) | Mise à jour en place : dpkg/rpm/`sudo pacman -U` déploient les nouveaux fichiers et retirent ceux qui ne sont plus dans le paquet. | Supprimés |
| Linux (AppImage) | Rien : l'utilisateur écrase un fichier. | Sans objet |

Le produit étant monolithique (coquille Tauri + WebUI + sidecar `navin-dist`
dans un seul bundle), remplacer le bundle suffit à prendre tous les correctifs,
y compris en réinstallant la même version par-dessus.

**Bumper la version avant chaque build de test.** Le MSI, apt et rpm comparent
les versions pour décider quoi faire (rpm refuse une version identique), et le
vérificateur de mise à jour signée rejette un manifeste qui ne monte pas.
`make set-version VERSION=x.y.z` propage la source de vérité
(`desktop/src-tauri/tauri.conf.json`) vers `Cargo.toml`, `package.json` et
`pyproject.toml`.

Ce qui survit à toute réinstallation, sur les trois OS : `~/.navin`, c'est-à-dire
les données utilisateur (config, sessions, notes, clés, profil navigateur). Un
`config.json` d'une version antérieure est migré au chargement par
`_migrate_config` (`navin/config/loader.py`).

Restent les toolchains téléchargées à la demande, qui vivent aussi dans
`~/.navin` et qu'aucun installateur ne remplace : Montage/HyperFrames
(`~/.navin/montage`) et le SDK Android (`~/.navin/android-platform-tools`).
Leur setup étant idempotent, il répondait « already ready » indéfiniment.
`navin/toolchains.py` écrit donc un marqueur `.navin-build.json` à côté de
chaque toolchain avec la version qui l'a installée :

- Montage : un setup lancé par une autre version réinstalle HyperFrames au lieu
  de ne rien faire. ffmpeg n'est pas retéléchargé : les builds desktop en
  embarquent un (voir ci-dessous), et une copie user-local existante ne dépend
  pas de la version de Navin.
- Mobile : la readiness expose `toolchain_stale`, et relancer Prepare complète
  ce que la nouvelle version attend (chaque étape est idempotente). Seul le SDK
  géré par Navin est marqué, jamais celui d'Android Studio.

## Le sidecar (composant interne)

`packaging/pyinstaller/navin-onefile.spec` produit l'exécutable autonome que
l'app desktop embarque : runtime Python, backend, canaux, outils, providers,
compétences, modèles de documents et WebUI de production. Il reste dans les
répertoires de staging (`os/*/bin/`, `%LOCALAPPDATA%\NavinBuild`) et n'est
jamais publié tel quel.

L'app desktop lance le sidecar (`navin webui --yes --no-open`), attend la
passerelle locale, puis charge l'URL authentifiée de la WebUI ; fermer la
fenêtre arrête la passerelle. Ports dédiés (8766 WebUI / 18791 gateway) pour
coexister avec un checkout source sur les ports de développement.

## Parité avec une installation source

Un build packagé doit faire tout ce que `pip install -e .` permet. Deux
propriétés de PyInstaller cassent silencieusement cette parité, et les deux
sont traitées ici plutôt que découvertes par un utilisateur :

**`sys.executable` est Navin, pas un interpréteur.** Tout ce qui écrivait
`sys.executable -m pip` ou `sys.executable script.py` atteignait le parseur
d'arguments de Navin. L'interpréteur est dans le bundle, donc `navin python`
l'expose : `navin python report.py`, `navin python -m playwright install
chromium`. L'outil `exec` préfixe `~/.navin/bin` au PATH, où `python` et
`python3` sont des shims vers cette sous-commande. Installer des paquets est
la seule chose impossible : pip est absent, donc les recettes utilisent un
interpréteur trouvé sur le PATH.

**Les métadonnées de paquets ne survivent pas au gel.** `importlib.metadata`
déclarait Slack, Telegram, Discord et l'API server « manquants » alors que
leur code était dans le bundle. Les scripts de build écrivent donc
`navin/bundled_extras.txt` (gitignoré), et c'est ce fichier que l'app croit.
`NAVIN_BUNDLED_EXTRAS` le surcharge pour le débogage.

Même cause pour la version : `navin.__version__` lit les métadonnées de
`navin-ai` et retombe sinon sur un « 1.0.0 » codé en dur. Un build packagé
s'annonçait donc en 1.0.0 quelle que soit sa vraie version, ce qui aurait fait
reproposer indéfiniment une mise à jour déjà installée. `bundle_contents.py`
embarque désormais ces métadonnées (`_APP_METADATA`).

**Les outils qualité sont des sous-processus.** `lint`, `test_run`, `verify`
et `lsp` appellent ruff, pytest, yamllint et pylsp par leurs console scripts,
qu'un build gelé n'a pas. Les builds installent l'extra `dev-tools` et
`packaging/pyinstaller/bundle_contents.py` les embarque : ruff comme binaire
copié dans `tools/`, les autres lancés en `navin python -m pytest`. Un outil
appartenant au projet gagne toujours sur la copie embarquée, et
`navin doctor` affiche la commande résolue pour chacun.

**ffmpeg est embarqué.** `packaging/ffmpeg_vendor.py` télécharge un ffmpeg
statique avant PyInstaller et le pose dans `tools/`, à côté de ruff, pour la
cible du build ; les trois scripts `build-offline` l'appellent et échouent si
l'étape échoue. Sans lui, quatre fonctions étaient conditionnées à une
installation manuelle - assemblage vidéo, exports sociaux, transcodage des
enregistrements navigateur, analyse d'une vidéo jointe à une conversation - et
aucune ne marchait sur Apple Silicon, qui n'avait aucune URL de téléchargement.

Les sources sont figées par SHA-256 dans `packaging/ffmpeg-manifest.json` :
l'amont sert des URLs « release » mouvantes, donc une empreinte qui ne
correspond plus arrête le build. On revoit le nouveau build, puis on re-fige
délibérément avec `--update-manifest`. Les binaires (47 à 98 Mo selon la cible)
vivent dans `packaging/vendor/`, ignoré par git.

Ce sont des builds **GPL**. `THIRD_PARTY_NOTICES.md` porte l'attribution et
l'offre écrite de code source correspondante, et le texte de la licence voyage
dans `tools/` avec le binaire ainsi que dans les ressources Tauri. La copie
embarquée passe après le `PATH` : un opérateur peut donc imposer son propre
build sans repackager l'application.

**typescript est embarqué.** `packaging/typescript_vendor.py` télécharge le
paquet npm avant PyInstaller et le pose dans `tools/node_modules/typescript`
(23 Mo) ; les trois scripts `build-offline` l'appellent et vérifient sa présence
dans l'arbre produit. Sans lui, un projet TypeScript sans `npm install` local
perdait silencieusement le type-checking.

Contrairement à ffmpeg, un seul artefact sert toutes les plateformes : la ligne
5.x est du JavaScript pur. C'est aussi la version contre laquelle
`webui/package.json` construit, donc le repli embarqué rapporte les mêmes
diagnostics qu'une installation locale. TypeScript 7 est le portage natif en Go
et se découperait en un binaire par plateforme, ce qui ramènerait le problème de
staging par cible.

La résolution est la même que pour le reste : `node_modules/.bin/tsc` du projet,
puis le `PATH`, puis la copie embarquée, déclarée par la clé `node_script` de
`linters.json` et lancée via node. Il faut donc toujours node sur la machine,
comme pour le reste de l'outillage JavaScript. Le paquet est figé par SHA-256
dans `packaging/typescript-manifest.json` ; une version npm étant immuable, une
empreinte qui ne correspond plus signifie que le registre a servi autre chose et
le build s'arrête.

**L'interface est vérifiée, pas supposée.** L'app desktop n'embarque pas la
WebUI dans son binaire Tauri : `frontendDist` ne contient qu'un splash, et la
vraie interface est servie à l'exécution par le gateway du sidecar. Ce que voit
l'utilisateur est donc exactement le `navin/web/dist` gelé dans ce sidecar, et
rien ne le contrôlait. Trois façons de livrer une interface que personne n'a
testée : un bundle laissé par un `npm run build` plus ancien, une machine de
build qui résout ses propres dépendances, une copie incomplète (robocopy sur un
chemin réseau, artefact tronqué).

`npm run build` écrit donc `navin/web/dist/build-info.json` : un SHA-256 sur
tous les autres fichiers du bundle, plus la version, le commit et l'empreinte du
lockfile. `packaging/verify_bundle_stamp.py` recalcule ce condensé depuis les
fichiers réellement présents et le compare au tampon ; les trois scripts
`build-offline` l'appellent sur l'arbre produit, et le workflow de release
passe en plus `--expect <digest du job webui>`, ce qui fait de « les cinq
cibles servent l'interface qu'on a construite et testée une fois » un fait
vérifié et non une intention.

Une seule résolution de dépendances est acceptée : `npm ci` sur
`webui/package-lock.json`. Quand `bun.lock` était accepté aussi, les deux
fichiers résolvaient 112 paquets sur 878 dans des versions différentes
(esbuild 0.28.2 contre 0.28.1, `@codemirror/view` 6.43.7 contre 6.43.6) : le
bundle livré dépendait donc du gestionnaire installé sur la machine de build.
Les autres lockfiles sont gitignorés pour que le second ne revienne pas.

`make desktop-parity` ajoute le contrôle qui manquait avant un packaging local :
il refuse un bundle plus vieux que les sources de `webui/` (le cas classique du
« ça marche sur localhost:5173 » où le serveur Vite sert les sources vivantes
alors que l'app packagée sert le bundle d'hier) et vérifie son tampon.

`bundle_contents.py` existe aussi pour empêcher la dérive : chaque capacité
embarquée vient de cette liste unique, donc un outil manquant est un diff
d'une ligne, pas une régression silencieuse. Deux choses de plus que la spec
doit connaître : `navin/documents/*.py` et `navin/skills/*/scripts/*.py`
voyagent comme données (`collect_data_files` saute les `.py`), et reportlab,
pdfplumber, PyMuPDF et yt-dlp sont des hidden imports.

Ce qu'un build packagé prend encore à la machine : node/npx, ffmpeg, jq,
sqlite3, pandoc, LibreOffice, docker, et un navigateur famille Chromium.
`navin doctor` rapporte tout, avec la commande d'installation du gestionnaire
de paquets courant.

HyperFrames (mode Montage / compositions HTML) n'est **pas** embarqué : il
s'installe à la demande sous `~/.navin/montage` via `montage(action=setup)`,
comme la toolchain Mobile sous `~/.navin`.

## `navin` dans le terminal

Les installateurs Tauri (NSIS/MSI, DMG, deb/rpm/pacman) ne modifient pas le PATH :
c'est le *self-heal* de `navin/cli_link.py`, exécuté au premier lancement de
l'app, qui crée les liens (`navin` sur POSIX, `navin.cmd` + shim WSL sur
Windows) et étend le PATH utilisateur. Taper `navin .` dans PowerShell, WSL,
ou un terminal Linux/macOS ouvre l'éditeur sur le dossier courant.

## Mises à jour signées

Chaque build packagé vérifie les mises à jour et propose **Installer et
redémarrer**. Une mise à jour n'est acceptée que si la signature Ed25519 du
manifeste, la plateforme, la taille signée, l'empreinte SHA-256, l'origine
HTTPS et l'ordre des versions valident tous.

Deux formes d'installation, un seul manifeste (`navin/update/service.py`) :

- **App desktop** (setup NSIS, DMG, AppImage) : la WebUI affiche la carte
  "Navin X is available" et remplace l'application entière (clés
  `windows-setup-x64`, `macos-app-*`, `linux-appimage-x64`). Les paquets
  deb/rpm/pacman sont seulement signalés, jamais remplacés.
- **CLI** (`curl https://navin.live/install`, archives
  `navin-cli-<v>-<os>-<arch>.tar.gz|.zip`, clés `cli-<os>-<arch>`) : le dossier
  `navin-dist/` est l'unité de mise à jour. `navin update` télécharge
  l'archive signée, la déballe à côté (`navin-dist.new`), lance le nouveau
  `navin --version` pour prouver qu'il démarre, puis bascule le dossier.
  POSIX bascule sur place ; Windows confie la bascule à `NavinUpdater.exe
  --mode tree` (copié hors de l'arbre) dès que la commande a rendu la main.
  `navin update --check` ne fait que rapporter, `--yes` n'interroge pas.
  `navin-cli` et `navin gateway` affichent une ligne "navin X is available"
  au plus une fois par jour (`~/.navin/update-check.json`).

`make aws-upload` (`scripts/publish-os-to-s3.sh`) publie les artefacts
immuables sous `v<version>/`, `releases.json` pour le site, puis
`stable/manifest.json` et `stable/manifest.sig` signés avec
`UPDATE_SIGNING_KEY` (clé privée Ed25519 32 octets en base64, dans `.env`).
Les scripts de build dérivent et embarquent sa clé publique
(`packaging/update/env.sh`). Les données utilisateur sous `~/.navin` ne font
jamais partie du remplacement.

Test d'intégrité local :

```bash
.venv/bin/python packaging/update/smoke_test.py
```

## Icônes

`packaging/windows/navin.ico` est généré depuis `webui/public/logo/navin.png` :

```bash
.venv/bin/python -c "from PIL import Image; Image.open('webui/public/logo/navin.png').convert('RGBA').save('packaging/windows/navin.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
```

`packaging/macos/navin.icns` est généré depuis la marque vectorielle et
committé ; il se reconstruit depuis n'importe quel OS :

```bash
.venv/bin/pip install cairosvg

[Showing lines 1-300 of 302. Use :301 to continue]