# Couche IA Marketing

Trois boucles sur Studio `#/marketing`. Ne les mélangez pas.

| Boucle | Qui l'execute | Mutations autorisees |
| --- | --- | --- |
| Heartbeat | Gateway puis un court tour LLM | `status` / `snapshot` / `watch` seulement |
| Boucle desk | Supervisor `navin-marketing-loop` + Start / Pause / Cycle Studio | `maybe_tick`, sans chat |
| Agent chat | `/marketing`, `/campaign`, `/montage` + outil `marketing` | Ecritures desk completes, sauf sur un tour heartbeat |

## Outil `marketing`

Meme livre que Studio. Appelez `action=status` avant toute affirmation.

Lecture seule : `status`, `snapshot`, `watch`.

Ecritures : `brand`, `settings`, `understand`, `position`, `research`, `competitor`, `plan`, `approve`, `content`, `creative`, `vision`, `metrics`, `improve`, `launch`, `pipeline`, `start`, `stop`, `schedule`, `tick`.

Sur heartbeat, les ecritures renvoient `ToolResult.error`. L'API HTTP refuse le meme set.

`tick` force par defaut si l'outil est appele sans `force`. Preferez le planning sauve pour le recurrent.

## Commandes slash

| Commande | Route modele | Doit persister sur le desk |
| --- | --- | --- |
| `/marketing` | `docs` | Oui. Status d'abord. Start / stop / schedule pour la boucle. Pas de cron de chat. |
| `/campaign` | `docs` | Oui. `action=plan` puis `content` et `creative`. Copies et images peuvent aussi atterrir sous `marketing/` dans le projet. |
| `/montage` | `docs` | Assets sous `marketing/montage/`. Le seed loop doit appeler `marketing action=start`, pas une cron lundi. |

Ligne palette `/marketing` : understand, plan, create, measure, improve. Pas publish.

`studio-expert-contract` s'applique a `/marketing`, `/campaign`, `/seo` et `/leads`.

## Skills

Precharges sur `product_module=marketing` :

- `marketing-strategist` (cable a l'outil `marketing` et a la boucle)
- `growth-marketing` (experiences sur le desk, jamais une cron de chat)
- `digital-marketing`
- `email-marketing`
- `marketing-analytics` (preferer `marketing action=metrics` pour le livre)

`/campaign` et `/montage` ajoutent campaign-manager, ad-creative-generator, product-visuals, montage-studio et le contrat expert.

## Routage

| Cle | Valeur |
| --- | --- |
| `STUDIO_OWNED_TOOLS["marketing"]` | `marketing` |
| Vue `#/montage` | module produit `marketing` (possede `/campaign` + `/montage`) |
| Outils refuses en heartbeat | `scrape`, `browser`, `cron`, `web_search`, `trading` (les ecritures marketing restent gatees par l'outil) |
| Sandbox | `~/.navin/marketing` est inscriptible |

## Seed de loop de session

`createSeed.marketing` et `createSeed.montage` demandent a l'agent de demarrer la boucle **desk** (`marketing action=start`). Ils ne doivent pas creer une cron de chat qui tick les KPI ou re-enregistre une demo chaque lundi.

## Regles d'honnetete pour le modele

- Ne jamais inventer trafic, CTR, ROAS, ou un post publie.
- Ne jamais marquer un email verifie sans preuve API (connecteurs Ads / Leads, pas ce desk).
- Ne jamais depenser un budget ads depuis Marketing. Passer les comptes payants a `#/ads`.
- Images et video : appeler `generate_image` / `generate_video` / `montage`, puis le QA visuel.
- Pas de tiret long dans les copies, rapports ou UI.

Voir [Skills](./skills.md) et [Actions](./actions.md).
