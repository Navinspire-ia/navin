# Studio Montage - Vue d'ensemble

**Point fort :** développez votre app **web et mobile** dans Navin (Code / Mobile), puis laissez Montage créer des **vidéos et images liées au projet** pour les réseaux - footage UI réel d'abord, créas IA pour B-roll, stills, musique ou voix off.

**Montage** transforme un projet lié en bureau vidéo marketing *propose-only* : démo navigateur live → exports sociaux → créas IA et compositions HyperFrames optionnelles. Aucune publication automatique.

| | |
| --- | --- |
| Route | `#/montage` (Studio → Montage) |
| Mode composer | **Montage** (préfixe le texte libre avec `/montage`) |
| Commande | `/montage [brief]` |
| Sortie workspace | `marketing/montage/` (+ `montage-report-*.html`) |
| Routage modèle | rôle `docs` |

## Quand utiliser Montage

- Vous avez une **UI produit exécutable** (preview locale ou URL staging) et voulez du vrai footage, pas seulement du stock.
- Vous devez produire des **crops plateforme** (YouTube, Shorts, Reels, Feed, TikTok, LinkedIn) à partir d'une démo master.
- Vous voulez un **calendrier 14 jours** et un kit avant de dépenser en lots vidéo IA.
- Vous avez besoin de motion HTML/GSAP (HyperFrames) ou de scènes React optionnelles (Remotion) sans les embarquer dans l'install Navin.

Pour les comptes ads payants (Google / Meta / TikTok / Reddit), utilisez le studio **Ads** (`#/ads`) après validation du spend. Les campagnes copy sans packaging de démo restent sous `#/marketing` (`/campaign`).

## Onglets du studio

| Onglet | Rôle |
| --- | --- |
| Templates | Bibliothèque table de montage : frames et cuts (étalonnage, caméra, décors, finitions) plus univers commerce (startups & apps, agents IA, mode, beauté). Jusqu'à 6 références par tour ; les fichiers sont téléchargés depuis AWS pour l'agent |
| Système | Doctor toolchain + Installer pour les packages manquants (FFmpeg, HyperFrames, Remotion) |
| Modèle IA | Choix des modèles Image / Vidéo / Musique / STT / TTS gérés (Plus ou BYOK) |
| Actions | Briefs en un clic (traduire & doubler, sous-titres traduits, doctor, démo, package, pipeline, musique, HyperFrames) |
| Galerie | Assets sous `marketing/montage/` |
| Profils | Tailles d'export intégrées |

## Traduction et doublage vidéo

Montage localise n'importe quel clip de bout en bout avec des outils locaux uniquement : ffmpeg extrait et découpe l'audio sur les silences, le STT configuré écrit un `source.srt` synchronisé, l'agent traduit les répliques (timings intacts), le TTS génère la nouvelle voix, et `montage(action=dub)` la réinjecte - avec option de garder l'audio d'origine en fond atténué et d'incruster les sous-titres traduits.

- **Traduire et doubler une vidéo** - un clip anglais revient en français (ou toute langue cible), son réinjecté.
- **Sous-titres traduits** - voix d'origine conservée, sous-titres traduits incrustés proprement ; le `.srt` est aussi livré pour les sous-titres plateforme.

Les sorties atterrissent sous `marketing/montage/localization/<clip>/`. Si le STT ou le TTS n'est pas configuré, l'agent dit précisément lequel manque et s'arrête - jamais de transcript inventé.

Liez un projet via le sélecteur avant d'enregistrer. Montage cible **uniquement le workspace lié** - il ne doit pas inventer un autre produit.

## Accès et budget

1. **Navin Plus ou supérieur (recommandé)** - clé gérée pour Image / Vidéo / Musique (Lyria) / STT / TTS.
2. **BYOK** - clés sous Réglages → Providers, puis chaque section média.
3. **FFmpeg seul** - dès qu'un fichier démo existe, `package` peut crop/exporter sans spend IA.
4. Le plan Free seul ne lance pas les générations média gérées.

Expliquer Plus vs BYOK avant les lots `generate_video` / `generate_music`. Musique par défaut = **Lyria Clip 30s** ; Lyria Pro seulement si l'utilisateur demande une piste complète.

## Parcours type (démo live → exports)

1. Ouvrir Studio → Montage, lier le projet.
2. Onglet Système : installer FFmpeg si absent (voir [Packages](./packages.md)).
3. Actions → **Démo produit (navigateur)** → renseigner URL + étapes (ou filmer soi-même dans le navigateur Agent).
4. Agent : `record_start` → parcours → `record_stop` → `demo_register` → fichiers sous `marketing/montage/demos/`.
5. **Exporter pour les réseaux** → `marketing/montage/exports/` (profils défaut ; `profiles=all` ajoute 4K + ciné).
6. Optionnel : calendrier, B-roll IA, rendu HyperFrames, rapport HTML.

## Arborescence de livraison

```text
marketing/montage/
  demos/           # enregistrements enregistrés
  exports/         # MP4 plateforme + briefs
  captures/        # stills UI
  compositions/    # HTML HyperFrames
  project-kit.md
  calendar-*.md
montage-report-*.html
```

## Docs liées

- [Actions](./actions.md) · [Packages & toolchain](./packages.md) · [Modèles média](./media-models.md) · [Skills & outils](./skills.md)
- Modes composer (dont Montage) : [Modes](../../navin_dev/fr/modes.md)
- Mobile run/preview : [Mobile](../../navin_dev/fr/mobile.md)
- Campagnes marketing : [navin_marketing](../../navin_marketing/fr/README.md)
- Ads MCP : [navin_ads](../../navin_ads/fr/README.md)
