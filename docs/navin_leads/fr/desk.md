# Bureau Leads - store unique

Le desk Leads est **local-first**. Toutes les surfaces lisent et ecrivent le meme repertoire : `~/.navin/leads/` (`get_runtime_subdir("leads")`).

| Surface | Entree |
| --- | --- |
| Studio | `#/leads`, `#/leads?pane=book`, `#/leads?lead=` |
| Tauri (Linux, Windows, macOS) | WebView vers le gateway, meme hash |
| Terminal | `navin leads …` |
| Fallback Vite / gateway ancien | `python -m navin.leads.desk_cli …` |
| HTTP | `GET/POST /api/leads?action=` |
| Agent | outil `leads` (`action=start\|stop\|schedule\|…`) |

Le chat du desk est masque par defaut (comme d'autres desks GTM). Les actions passent par l'API et la loop, pas par une cron de chat.

## Waterfall : open data d'abord

Ordre fixe :

1. **Open data / registres** - SIRENE (FR), Companies House, et catalogues publics.
2. **Web public** - recherche et pages societe. Jamais de scrape LinkedIn, jamais de login wall.
3. **BYOK** (Hunter, Apollo, Pappers, Places, …) **seulement** pour les champs encore vides.

Chaque donnee reste sourcée ou `unverified`. `email_status=verified` uniquement via une API de preuve.

## Scoring BANT-F (desk)

Le qualify du desk (`navin/leads/qualify.py`) ecrit `score`, `tier`, `bant`, `why`, `signals`, `next_action` :

| Palier | Seuil |
| --- | --- |
| A | score >= 80 |
| B | score >= 55 |
| C | sinon |

Les axes BANT-F sont `fit`, `need`, `timing`, `authority`, `budget`.

Les scripts CSV `/leads` (`score_leads.py` / `engine.py`) gardent leurs seuils 70 / 40. C'est volontaire : le desk et le rapport CSV ne sont pas la meme grille.

## Liens officiels

`openOfficialLeadUrl` ouvre le navigateur OS (`openInOsBrowser`). Jamais `window.open` : dans le WebView Tauri c'est un no-op ou une page blanche.

Les hashes internes (`#/leads`, `http://tauri.localhost/#/leads`, `tauri://localhost/#/leads`) ne sortent pas. Les hosts nus (`acme.com`) sont acceptes comme URL officielle.

## Verrou

`FileLock` sur le desk **n'est pas reentrant**. Un hunt qui tient le lock ne doit pas rappeler `peek_loop` (deadlock). Pause / horaire pendant un hunt passent par `loop.intent.json` et sont appliques a la fin.

Voir aussi : [Start loop](./loop.md), [Heartbeat](./heartbeat.md), [Desktop](./desktop.md), [CLI et API](./cli-api.md).
