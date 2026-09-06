# Actions marketing - 17 cartes

Studio `#/marketing` : deux surfaces, un seul livre. Preferez l'outil desk pour tout ce que la boucle doit mesurer.

## Outil desk / HTTP / CLI

`POST /api/marketing?action=` ou outil `marketing` ou `navin marketing <action>`.

Le heartbeat ne peut lancer que les trois premieres.

| Action | Role |
| --- | --- |
| `status` / `snapshot` | Desk complet : marque, produit, campagnes, contenus, creatives, analytics, loop (`peek_loop`), journal, KPI, armed |
| `watch` | Passage silencieux gagnants / concurrents. Dedup. Notify optionnel |
| `brand` | Memoire de marque (company, tone, audience, couleurs, ...) |
| `settings` | `execution_mode`, `auto_publish` (stocke, jamais d'auto-publish), `winner_multiple`, canaux notify optionnels |
| `understand` / `product` | Scan workspace ou brief produit. Une URL `site` live declenche encore un harvest. |
| `harvest` | Recupere une URL live, parse titre / one-liner / titres / CTA / couleurs / polices / images / liens sociaux, applique a la marque + creatives, puis `fill_from_site` |
| `position` | Positionnement + ICP depuis le produit |
| `research` | Carte concurrentielle. Accepte des `hits` injectes. N'invente pas le trafic live |
| `competitor` | Upsert d'un concurrent nomme |
| `plan` / `campaign` | Campagne 30 jours (ou `days`) pour une cible d'inscriptions. Upsert d'une ligne draft / planned identique |
| `approve` | Approuve la campagne `id`, puis remplit contenus + creatives |
| `content` | Variantes par canal (titres / CTA harvest + marque). Upsert par canal + campagne. `hook` optionnel |
| `creative` | Briefs image / video / audio / banner (`status=brief`) |
| `produce` / `generate` | Ecrit de vrais fichiers. `pack=brand` / `pack=posts` ou `creative_id` optionnels. Video / voix sautees sans provider |
| `seo` | Mots-cles / pages depuis harvest + recherche. Classement mesure optionnel (`keyword`, `url`, `position`) via `ingest_ranking` |
| `vision` | PASS / WARN / BLOCK humain sur une creative `id` |
| `metrics` / `analytics` | Injecte trafic, leads, inscriptions, revenu, `by_content` |
| `improve` | Scoreboard, gagnants, variantes |
| `launch` | Kit de lancement : JSON plus fichiers Markdown sous `launch/` |
| `pipeline` | Understand → position → research → plan → content → creative → launch |
| `start` | Arme la boucle. `schedule`, `run_now`, `tz` optionnels |
| `stop` | Pause |
| `schedule` | Change les horaires |
| `tick` | Un cycle (`force` vrai par defaut en HTTP) |

Une action inconnue renvoie `MarketingError`. Un `days` / `score` invalide renvoie 400.

## Cartes chat (`/campaign`)

Chaque carte seed encore `/campaign` avec une spec precise. Persistez le resultat avec `marketing action=plan` / `content` / `creative` si la boucle doit le posseder.

### Creatif

| Action | Livre |
| --- | --- |
| Images produit | Packshot, lifestyle, variantes sociales via les outils image. QA visuel apres generation. |
| Video pub | Script 15-30 s, storyboard, voix off, video generee si un provider est pose. |
| Ouvrir Montage | Pont vers `#/montage`. Jamais d'auto-publish. |
| Visuels sociaux | Jeu carre / vertical / paysage. |
| Kit de marque | Pistes de logo, palette, type, voix. |

### Design

| Action | Livre |
| --- | --- |
| Affiche / visuel cle | Hero 4:5 plus recadrages 9:16 et 1:1, portes par `visual_qa`. |
| Pack bannieres | Master decline en six tailles reseau. |
| Page produit 3D | Orbit interactive avec fallback fixe. |
| Carrousel | 5-7 pages 1080x1350. |

### Contenu

| Action | Livre |
| --- | --- |
| Posts sociaux | LinkedIn, X, Instagram, script TikTok : accroches, hashtags, CTA. |
| Article de blog | Plan, corps, meta, extraits promo. |
| Sequence email | 5 emails avec objets A/B. |
| Copy landing | Hero, benefices, preuve, FAQ, CTA. Super render : `ui-ux-pro-max` (3D dessinee, pas un fond). |

### Strategie

| Action | Livre |
| --- | --- |
| Campagne 360 | Message house, plan canaux, puis chaque livrable. |
| Persona | ICP avec claims sources. |
| Plan 30 jours | Themes hebdo alignes sur le funnel. |
| Analyse concurrentielle | Pages publiques seulement. Stocker les noms avec `marketing action=competitor`. |

Ne jamais inventer ROAS / CPC. Etiqueter les estimations. Les lectures de comptes payants vont a `#/ads`.
