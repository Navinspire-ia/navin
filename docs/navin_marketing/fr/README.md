# Marketing Agent OS - Vue d'ensemble

Le module **Marketing** (barre laterale → **Marketing**, route `#/marketing`) est un **desk marketing local** : moissonner un site produit live (ou scanner le projet lie), figer la Memoire de marque, planifier une campagne, ecrire les variantes par canal, produire les images de marque et de posts (video / audio si un provider est configure), passer le QA visuel, injecter metriques et classements SEO mesures, puis laisser une boucle murale mesurer et ameliorer les gagnants.

Le livre vivant est Studio `#/marketing` et l'outil `marketing` (meme store que Tauri sur Linux / Windows / macOS, `navin marketing`, et `python -m navin.marketing.desk_cli`). N'inventez pas de trafic, CTR, spend, ni un post publie qui n'est pas dans ce store.

Pour les **comptes media payants live** (MCP Google / Meta / TikTok / Reddit Ads), utilisez le studio **Ads** (`#/ads`). Pour les demos produit live et les exports video sociaux, utilisez **Montage** (`#/montage`).

## Fonctionnement

1. Ouvrez **Marketing** dans la barre laterale (`#/marketing`).
2. Si le produit a deja une URL, **Moissonner le site live**. Le desk recupere titre, one-liner, titres, CTA, couleurs, polices, logo, image OG et liens sociaux, puis remplit marque, SEO, contenus et ads. Sinon **Utiliser le projet courant** (scan workspace + Memoire de marque).
3. **Approuver** la campagne a lancer.
4. Dans Studio, **Generer le kit marque** / **Generer les images de posts** (`produce` avec `pack=brand` ou `pack=posts`). Une carte se genere avec `creative_id`. Video et voix restent sautees tant qu'aucun provider n'est configure.
5. **Demarrer la boucle** (jour / jours ouvres / week-end / semaine / mois + heure) ou **Lancer le produit** (fichiers Markdown sous `launch/`). Pause ou changement d'horaire a tout moment. Le gateway mesure puis ameliore sur ce calendrier tant que Navin tourne.
6. Le heartbeat ne remonte que les gagnants (et les changements concurrents). Il ne publie jamais, ne depense jamais, ne demarre jamais la boucle.

```
/marketing
/marketing action=status
/campaign persiste ce lancement sur le desk marketing puis briefe LinkedIn + X
```

Ne creez pas une cron de chat qui tick ou relit les KPI. La boucle du desk est le cycle autonome.

## Happy path

| Etape | UI | Action store |
| --- | --- | --- |
| Moisson | Moissonner le site live | `harvest` puis `fill_from_site` (SEO, contenus, social, ads) |
| Comprendre | Utiliser le projet courant | `pipeline` (understand → position → research → plan → content → creative → launch kit) |
| Approuver | Approuver dans Campagnes | `approve` |
| Produire | Generer le kit marque / images de posts | `produce` (`pack` ou `creative_id`) |
| Recurrent | Demarrer la boucle | `start` + planning sauve |
| Un cycle | Lancer un cycle | `tick` avec `force=true` |
| Kit | Lancer le produit | `launch` (fichiers Markdown sur disque) |

Le desk est arme quand un nom de produit ou une marque (company / product) est pose. Start loop reste desactive tant que le desk n'est pas arme.

## La commande `/marketing`

| | |
| --- | --- |
| Commande | `/marketing [launch\|pipeline\|loop]` |
| Cycle de vie | Workflow agent (route modele `docs`) |
| Skills precharges | `marketing-strategist`, `growth-marketing`, `digital-marketing`, `email-marketing`, `marketing-analytics` (+ skills campagne / montage si `/campaign` ou `/montage`) |
| Outil | `marketing` (meme livre que le desk Studio) |
| Sortie | Livre JSON `~/.navin/marketing/` + UI Track A `marketing-report-*` optionnelle |

`/campaign` produit encore des livrables dans le chat (copies, visuels, rapports). Persistez marque, campagne, contenus et creatives avec `marketing action=plan` / `content` / `creative` pour que la boucle puisse les mesurer.

## Un seul livre, quatre portes

| Porte | Comment |
| --- | --- |
| Studio | `#/marketing` → HTTP `/api/marketing?action=` |
| Agent | outil `marketing` |
| CLI | `navin marketing` ou `python -m navin.marketing.desk_cli` |
| Gateway | Supervisor `navin-marketing-loop` + heartbeat `watch` |

## Ce que la machine fait (et ne fait pas)

| Fait | Ne fait pas |
| --- | --- |
| Moissonne une URL live ou comprend le projet lie | Invente du trafic live, des classements ou des posts publies |
| Ecrit positionnement, recherche, campagnes, copies | Auto-publie sur LinkedIn, X, Meta ou email |
| Produit les images de marque / posts (video / audio si un provider est pose) | Depense un budget ads |
| Score les gagnants sur metriques injectees (`by_content`) et clone des variantes | Invente des positions SEO (seulement `ingest_ranking`) |
| Alerte (WebUI + Telegram / email / WhatsApp optionnels) une fois par gagnant | Chasse le web public a chaque heartbeat |
| Recupere un cycle bloque, evite les chevauchements, retente en backoff | Bloque le gateway si un cycle rame |

Les images moissonnees et produites vivent sous `~/.navin/marketing/assets/` et sont servies par `/api/marketing/file`. Le QA visuel liste les **creatives du desk** et les captures workspace. Montage reste le studio des exports demo live (`#/montage`).

## Voir aussi

- [Interface desk](./desk.md)
- [Boucle de croissance](./loop.md)
- [Heartbeat](./heartbeat.md)
- [Desktop (Tauri)](./desktop.md)
- [Actions](./actions.md)
- [Couche IA](./ai.md)
- Contrat loop des desks : [desk-loop](../../studio/desk-loop.md)
- Ads : [navin_ads](../../navin_ads/fr/README.md)
- Montage : [navin_montage](../../navin_montage/fr/README.md)
