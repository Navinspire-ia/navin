# Packages et toolchain Montage

Montage n'embarque jamais HyperFrames ni Remotion au démarrage à froid de Navin.

FFmpeg fait exception : les builds desktop **embarquent un FFmpeg statique**, pour que l'assemblage vidéo, les exports sociaux et l'analyse des vidéos jointes fonctionnent dès l'installation, sans rien télécharger. Un binaire présent dans le `PATH` reste prioritaire, donc vous pouvez imposer votre propre build. Les installations depuis les sources et les conteneurs n'ont pas de copie embarquée et retombent sur le binaire système ou user-local.

## Catalogue

| Id | Tier | Taille (approx.) | Rôle |
| --- | --- | --- | --- |
| `stock-pexels` / `stock-unsplash` / `stock-pixabay` | builtin | 0 | Clients HTTP uniquement. Clés développeur gratuites. Masqués de la liste Install Studio. |
| `ffmpeg` | system | ~80 MB | Encodage, crop, sous-titres, mix audio pour `package` / démos |
| `hyperframes` | lazy | ~120 MB | HTML/CSS/GSAP → MP4 sous `~/.navin/montage` |
| `remotion` | lazy (optionnel) | ~350 MB | Compositions React sous `~/.navin/montage/remotion` |

Installation via l'onglet **Système** du studio, ou :

```text
montage(action=setup, package=ffmpeg|hyperframes|remotion|core)
```

`package=core` détecte seulement stock + FFmpeg ; aucun stack npm lourd.

## Détection

| Contrôle | Méthode |
| --- | --- |
| FFmpeg | `PATH`, puis le `tools/ffmpeg` embarqué, puis `~/.navin/montage/bin/ffmpeg` (`.exe` sous Windows) |
| HyperFrames | CLI sous `~/.navin/montage` |
| Remotion | CLI sous `~/.navin/montage/remotion` |
| Chrome | Chrome/Chromium système, ou cache Playwright (`ms-playwright`) |

Le doctor (`montage(action=doctor)`) renvoie ok / warn / missing avec une chaîne de correctif.

## Ordre d'install FFmpeg (bouton Installer)

1. **Gestionnaire OS** s'il peut tourner sans interaction :
   - Windows : `winget` (`Gyan.FFmpeg`) puis `choco`
   - macOS : `brew install ffmpeg`
   - Linux : `apt-get` / `dnf` / `yum` / `pacman` avec `sudo -n` quand disponible
2. **Fallback user-local** (sans root) : build statique extrait dans `~/.navin/montage/bin`
   - Linux amd64 / arm64 (static johnvansickle)
   - Windows (zip Gyan essentials)
   - macOS Intel en secours si brew absent ; Apple Silicon privilégie Homebrew

Si FFmpeg affiche « not on PATH » sans bouton Installer, rafraîchir après mise à jour du gateway - un FFmpeg manquant doit exposer Installer dès qu'une recette est exécutable (manager ou user-local).

### Commandes manuelles

| OS | Commande |
| --- | --- |
| Ubuntu / Debian | `sudo apt-get install -y ffmpeg` |
| Fedora / RHEL (dnf) | `sudo dnf install -y ffmpeg` |
| CentOS (yum) | `sudo yum install -y ffmpeg` |
| Arch | `sudo pacman -S --noconfirm ffmpeg` |
| macOS | `brew install ffmpeg` |
| Windows | `winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements` |

## HyperFrames et Remotion

| Package | Install | Requis pour |
| --- | --- | --- |
| HyperFrames | `setup package=hyperframes` | Compositions HTML → MP4 (`render`) |
| Remotion | `setup package=remotion` | Scènes React uniquement - optionnel |

Préférer HyperFrames pour le motion / promos produit. Ne pas installer Remotion sauf demande React explicite.

## Modèles média IA (onglet studio)

Distinct des packages OS. Avec plan Navin géré ou BYOK, l'onglet **Fournisseurs média IA** liste les modèles curés pour Image, Vidéo, Musique (Lyria), STT et TTS. Le changement met à jour les Réglages. L'UI Montage retire le bruit de marque fournisseur des libellés.

## Dépannage

| Symptôme | Cause probable | Correctif |
| --- | --- | --- |
| FFmpeg manquant, pas d'Installer | Gateway trop ancien | Redémarrer le gateway sur l'arbre courant ; rafraîchir Studio |
| Install échoue avec hint sudo | Mot de passe interactif requis | Exécuter le `sudo …` affiché, ou laisser le fallback user-local |
| Exports package partiels | FFmpeg toujours absent | Installer FFmpeg, relancer `package` |
| Render HyperFrames demande Chrome | Pas de navigateur | Installer Chrome/Chromium ou `playwright install chromium` |
| L'agent propose un autre produit | Projet non lié / non exécutable | Lier le projet ou donner l'URL live une fois |
