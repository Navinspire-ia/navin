# Actions Montage

Chaque carte prépare ou envoie `/montage` avec un brief concret. Préférez **Brief dans le chat** / **Éditer** quand vous devez ajuster chemins ou profils avant l'envoi.

## Cartes

| Action | Effet | Attentes agent |
| --- | --- | --- |
| Traduire et doubler une vidéo | `transcribe` → traduction `.srt` → TTS → `dub` | STT synchronisé par segment (découpe sur silences), timings intacts, nouvelle voix réinjectée (`original_gain_db` garde le fond d'origine). Jamais de transcript inventé. |
| Sous-titres traduits | `transcribe` → traduction `.srt` → incrustation `assemble` | Voix d'origine conservée, répliques traduites incrustées, `.srt` livré aussi. |
| Vérifier la chaîne | `montage(action=doctor)` + `detect` | Prêt vs manquant avec correctifs exacts. Pas de readiness inventée. |
| Démo produit (navigateur) | Formulaire de **brief** (URL + étapes + login optionnel) | Ouvrir l'URL → `browser(record_start)` → parcours exact → `record_stop` → `demo_register`. Sauver sous `marketing/montage/demos/`. Ne pas inventer un autre produit. |
| Exporter pour les réseaux | `montage(action=package, path=…, profiles=default)` | Dernière démo. Brief sous `exports/`. Jamais de publish auto. |
| Pipeline montage complet | Doctor → démo → package → analyze + calendrier 14 jours | Projet lié uniquement. Une seule demande d'URL si l'UI n'est pas lançable. |
| Bande son (Lyria Clip) | `generate_music` | Clip 30s par défaut. Lyria Pro seulement sur demande explicite. Confirmer Plus/BYOK. |
| Composer et rendre | HTML HyperFrames → MP4 | `setup` si besoin, rédiger sous `compositions/`, `render` avec un profil. |

## Brief démo live (champs)

Au clic **Brief agent**, Montage demande :

| Champ | Obligatoire | Exemple |
| --- | --- | --- |
| URL du produit | Oui | `https://app.example.com/login` ou URL de preview locale |
| Parcours à filmer | Oui | Étapes numérotées (login → créer → écran résultat) |
| Login / accès | Non | Compte démo, lien magique, ou « demande-moi au mur de login » |

Vous pouvez aussi filmer vous-même dans le navigateur Agent sans le formulaire, puis enregistrer le fichier avec `demo_register`.

## Usage slash

```text
/montage Enregistre l'app liée sur https://staging.example.com : login → créer un projet → dashboard. Sauve sous marketing/montage/demos/, puis package profiles=default.
```

```text
/montage Lance doctor, puis installe ffmpeg si manquant. N'installe pas Remotion sauf si je le demande.
```

## Garde-fous

- Un simple « salut » = conversation, pas une mission - pas de kit, pas d'analyze.
- Ne jamais affirmer que `record_start` / `montage` sont absents en mode Montage.
- Ne jamais publier automatiquement sur les réseaux ou Ads MCP sans demande explicite ultérieure.
