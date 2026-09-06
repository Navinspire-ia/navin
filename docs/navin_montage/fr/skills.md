# Skills et outils Montage

## Skills

| Skill | Rôle |
| --- | --- |
| `montage-studio` | Playbook principal : doctor → démo → package → kit/calendrier → créas → HyperFrames |
| `playwright-browser` | Navigateur Agent live, dont `record_start` / `record_stop` |
| `studio-html-report` | Pattern de rapport HTML (`montage-report-*.html`) |

Gérez les skills sous **Réglages → Skills**. En mode Montage ces outils sont enregistrés - l'agent doit les appeler, pas affirmer qu'ils manquent.

## Actions de l'outil `montage`

| Action | Rôle |
| --- | --- |
| `detect` | Snapshot toolchain hôte |
| `doctor` | Checks avec statut + correctifs |
| `setup` | Install `core` \| `ffmpeg` \| `hyperframes` \| `remotion` \| `stock-*` |
| `stock_search` | Requête Pexels / Unsplash / Pixabay |
| `analyze` | Écrit `marketing/montage/project-kit.md` |
| `calendar` | Propose un calendrier (`days` 7 / 14 / 30) |
| `screenshot` | Enregistre des stills UI (`path` ou `paths`) |
| `demo_register` | Importe un enregistrement navigateur dans `demos/` |
| `package` | Exports plateforme (`profiles`, `srt` / `title` optionnels) |
| `render` | HTML HyperFrames → MP4 (`composition`, `profile` ou width/height/fps) |
| `profiles` | Liste les profils de rendu |

### Packages `setup`

- `core` - détection seule (pas de npm lourd)
- `ffmpeg` - gestionnaire OS ou binaire user-local
- `hyperframes` - npm lazy sous `~/.navin/montage`
- `remotion` - npm lazy optionnel sous `~/.navin/montage/remotion`
- `stock-pexels` / `stock-unsplash` / `stock-pixabay` - statut / clés seulement

## Enregistrement navigateur

```text
browser(action=record_start)
# … piloter l'UI produit …
browser(action=record_stop)
montage(action=demo_register, path=<recording>)
```

Préférer `open_preview` pour les apps locales. Arrêter avant de démarrer un second enregistrement.

## Profils de rendu

| Id profil | Label | Résolution | Ratio |
| --- | --- | --- | --- |
| `youtube_landscape` | YouTube Landscape | 1920×1080 | 16:9 |
| `youtube_4k` | YouTube 4K | 3840×2160 | 16:9 |
| `youtube_shorts` | YouTube Shorts | 1080×1920 | 9:16 |
| `instagram_reels` | Instagram Reels | 1080×1920 | 9:16 |
| `instagram_feed` | Instagram Feed | 1080×1080 | 1:1 |
| `tiktok` | TikTok | 1080×1920 | 9:16 |
| `linkedin` | LinkedIn | 1920×1080 | 16:9 |
| `cinematic` | Cinematic | 2560×1080 | 21:9 |

- `package` par défaut = set social (hors `youtube_4k` et `cinematic`).
- `profiles=all` ou liste séparée par virgules pour les tailles opt-in.
- `render` avec `profile=youtube_shorts` fixe width/height depuis la table.

## Outils média associés

Quand configurés : `generate_image`, `generate_video`, `generate_music`. Confirmer le budget avant les lots. Les mutations media payantes passent par `/ads` uniquement avec accord explicite.
