# Builder les releases Navin

Toutes les commandes se lancent a la racine du repo. Chaque artefact atterrit
dans [`os/`](../os/README.md), avec son fichier de checksum SHA-256.
PyInstaller et Tauri ne cross-compilent pas : chaque OS se builde sur son
propre systeme (ou via le workflow GitHub Actions `os-release.yml`).

La distribution est **100 % Tauri** : le seul produit livre est l'app desktop
native (fenetre Rust + WebView systeme) qui embarque le moteur en sidecar
PyInstaller. Le sidecar (`make linux` / `make macos` / construit
automatiquement sur Windows) est un composant interne, jamais publie seul.

## Vue d'ensemble

| Cible | Commande | Ou builder | Sortie |
|---|---|---|---|
| Sidecar Linux (interne) | `make linux` | Linux x64 | `os/linux/bin/x64/navin-dist/` |
| App desktop Linux | `make appimage` | Linux | `os/linux/appimage/x64/` AppImage + `os/linux/deb/x64/` .deb + `os/linux/rpm/x64/` .rpm + `os/linux/pacman/x64/` .pkg.tar.zst |
| App desktop Linux (distribution) | `make appimage-release` | Linux avec Docker | idem, compatible glibc >= 2.35 |
| App desktop Windows | `make windows` | Windows ou WSL (interop) | `os/windows/x64/Navin-Desktop-<v>-windows-x64.msi` + `-setup.exe` |
| Sidecars macOS (interne) | `make macos` | Mac | `os/macos/{arm64,x64}/navin-macos-<arch>` (x64 via Rosetta) |
| App desktop macOS | `make desktop-dmg` | Mac | `os/macos/{arm64,x64,universal}/Navin-Desktop-<v>-macos-<arch>.dmg` |
| Publication S3 | `make aws-upload` | n'importe ou (AWS CLI + `.env`) | `s3://<bucket>/v<version>/` |

L'app desktop utilise des ports dedies (webui 8766, gateway 18791) et
coexiste avec un checkout source sur les ports de developpement.

## Linux

Prerequis (une fois) :

```bash
# Sidecar (PyInstaller) : Python 3.11+, venv du repo
# App desktop : Rust + Node.js + libs WebKitGTK
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
sudo apt-get install -y libwebkit2gtk-4.1-dev build-essential libxdo-dev \
  libssl-dev libayatana-appindicator3-dev librsvg2-dev
```

Build :

```bash
make linux              # 1. sidecar (prerequis du suivant)
make appimage-release   # 2. AppImage + .deb + .rpm + .pkg.tar.zst distribuables (Docker, glibc 2.35)
```

`make appimage` (sans Docker) builde plus vite mais herite de la glibc de la
machine : reserver aux tests locaux.

## Windows

Prerequis (une fois, dans un terminal Windows, pas WSL) - `make windows` les
installe automatiquement via winget s'ils manquent :

```powershell
winget install Rustlang.Rustup          # puis : rustup default stable-msvc
winget install OpenJS.NodeJS.LTS
winget install Microsoft.VisualStudio.2022.BuildTools --override `
  "--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

Build (depuis WSL, l'interop traverse vers Windows ; ou depuis PowerShell) :

```bash
make windows        # sidecar + app desktop : .msi (WiX) + -setup.exe (NSIS)
```

`packaging/windows/signing.env` (Azure Artifact Signing, meme identite a chaque
release) signe le sidecar, NavinUpdater, le sandbox, l'exe de fenetre, le MSI
et le setup. `verify-windows-signatures.ps1` exige `Status=Valid` sur les
installeurs publies et, si 7-Zip est la, sur les exe first-party qu'ils
contiennent. Ne jamais retoucher un fichier apres signature : un octet change
le hash et SmartScreen repart de zero. `Status=Valid` ne fait pas disparaitre
l'ecran bleu ; la reputation du hash se construit cote Microsoft.

## macOS

Prerequis (une fois, sur le Mac) :

```bash
xcode-select --install
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
brew install node
softwareupdate --install-rosetta   # Apple Silicon : requis pour le build x64
```

Build - un seul passage produit **arm64 + x64 + universal** :

```bash
make macos          # 1. sidecars arm64 + x64 (le x64 via Rosetta)
make desktop-dmg    # 2. DMG par architecture + DMG universal (lipo)
```

Sur un Mac Apple Silicon, `make macos` construit d'abord le sidecar arm64
natif puis se relance sous Rosetta pour produire le sidecar Intel : les Macs
d'avant 2021 recoivent ainsi leur DMG x64. Sans Rosetta, seule l'arm64 est
produite et les DMG x64/universal sont sautes avec un avertissement.

Distribution publique : signer et notariser avec un compte Apple Developer
(`MACOS_SIGN_IDENTITY`), sinon Gatekeeper affiche "developpeur non
identifie" au premier lancement.

## Publier sur S3 (synchronise avec navin.live)

```bash
make aws-upload                 # version de l'app desktop (tauri.conf.json)
make aws-upload VERSION=1.1.0   # autre version
make aws-upload VERSION=2.0.1 PLATFORM=linux    # n'écrase que Linux
make aws-upload VERSION=2.0.1 PLATFORM=windows  # n'écrase que Windows
make aws-upload VERSION=2.0.1 PLATFORM=macos    # n'écrase que macOS
```

Le script televerse chaque artefact de `os/` sous `v<version>/` : meme
version + meme nom = fichier ecrase, nouvelle version = nouveau prefixe.
`PLATFORM=linux|windows|macos` limite l'envoi a cet OS : les autres cles S3
de la meme version ne sont ni supprimees ni reuploadees. `SHA256SUMS.txt`,
`releases.json` et le manifeste d'update sont fusionnes avec ce qui est deja
en ligne. Sans `PLATFORM`, seuls les fichiers presents localement dans `os/`
partent ; les absents sont signales, pas detruits sur S3.

## Verifier un artefact

```bash
# Linux/macOS
(cd os/linux/appimage/x64 && sha256sum -c SHA256SUMS-appimage-x64.txt)
# Windows (PowerShell)
Get-FileHash os\windows\x64\Navin-Desktop-*.msi -Algorithm SHA256
```

Le detail de chaque chaine (signature, notarisation, parite avec une
installation source) est dans [`packaging/README.md`](../packaging/README.md) ;
le fonctionnement de l'app desktop dans [`desktop/README.md`](../desktop/README.md).
