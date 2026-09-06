# Interface du desk Marketing

Studio `#/marketing` est le Marketing Agent OS. Il parle a `/api/marketing` (meme store que l'outil `marketing`). Le chat reste optionnel.

## Stack

| Couche | Ce que vous voyez |
| --- | --- |
| Fluent UI | Boutons, champs, MessageBar, ProgressIndicator, theme |
| framer-motion | Transitions d'onglets |
| three + R3F + drei | `MarketingScene` : la chaleur suit les gagnants / l'arme, le mouvement suit la boucle. 3D dessinee, pas un fond. Controles orbit. Pause si reduced-motion ou hors ecran. |

## En-tete

| Controle | Action | Test id |
| --- | --- | --- |
| Actualiser | `snapshot` | - |
| Chat | Ouvre / ferme le compositeur | - |
| Demarrer la boucle | Ouvre le planning, puis `start` | `marketing-start-loop` |
| Pause boucle | `stop` | `marketing-pause-loop` |
| Planning | `schedule` (horaires seuls, n'active pas) | `marketing-schedule-loop` |
| Lancer un cycle | `tick` avec `force=true` | `marketing-tick-loop` |

Start loop reste desactive tant que le desk n'est pas arme. Pendant une requete, les controles affichent `busy`.

Si le premier snapshot echoue, le desk montre l'erreur et **Reessayer**. Pendant le premier chargement, un ProgressIndicator s'affiche.

Quand la boucle est active, le desk se rafraichit toutes les 12 secondes.

## Onglets

| Onglet | Ce que vous faites |
| --- | --- |
| Apercu | KPI, etat de boucle, snapshot harvest (one-liner, titres, CTA, liens sociaux), Utiliser le projet courant, Moissonner le site live, Lancer le produit |
| Marque | Societe, ton, audience, couleurs / polices / logo moissonnes → `brand` |
| Produit | Understand / position / research (marche, tendances, mots-cles) |
| Campagnes | Plan 30 jours, Approuver (upsert, pas de campagnes en double) |
| Contenu / Social | Variantes par canal (upsert par canal + campagne) |
| Studio | **Generer le kit marque** (`pack=brand`), **Generer les images de posts** (`pack=posts`), **Generer celle-ci** (`creative_id`) |
| QA visuel | Creatives du desk + captures workspace (`visual_qa`, rapports, override humain) |
| SEO | Mots-cles harvest / recherche + **Injecter un classement mesure** (jamais invente) |
| Ads | Hint spend, apercus creatives harvest / produce, seed `/ads` (jamais de spend sans clic humain) |
| Analytics | Trafic / leads / inscriptions / revenu + vues / clics / conversions par contenu → `metrics` |
| Concurrent | Ajouter un concurrent nomme |
| Lancement | Construire le kit (fichiers Markdown sous `launch/`) |
| Journal | Lignes loop, watch et alertes. Etat vide si le livre est neuf. |

Les icones de nav sont Fluent. Les textes sont i18n (`studio.marketing.*`, `studio.marketingQA.*`) en francais et en anglais.

## Panneau planning

`TradingLoopSchedulePanel` avec les cles i18n Marketing :

- kind : daily, weekdays, weekend, weekly, monthly
- heure / minute, jour de semaine, jour du mois
- fuseau = zone IANA du navigateur
- option **Aussi lancer un cycle maintenant** (`run_now`)

## QA visuel

L'onglet QA est `MarketingQA`. Il liste les images du workspace, lance la porte, et enregistre un override humain audite. Le verdict machine reste sur disque.

## Ce qui n'est pas ce desk

| Route | Role |
| --- | --- |
| `#/ads` | MCP media payant live |
| `#/seo` | Studio SEO complet |
| `#/montage` | Demo live + exports video sociaux |

Ces studios ne remplacent pas le livre Marketing. Le spend Ads et les exports Montage restent propose-only ou clic humain.
