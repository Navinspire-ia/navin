# Actions Ads - 15 cartes

Chaque carte envoie `/ads` avec un brief plateforme. Connectez le MCP correspondant dans **Réglages → MCP** pour des données live.

## Google Ads

| Action | Livre |
| --- | --- |
| Vue compte | Clients accessibles, carte campagnes/ad groups, métriques 7 jours via MCP `google-ads` (sinon export). Sous `ads/google/` + `ads-report-*.html`. |
| Structure de campagne | Structure Search/PMax depuis lectures live : campagne → ad group → mots-clés/thèmes, négatifs, budgets, checklist tracking. |
| Optimisation hebdo | Rapport kill/scale : gagnants, perdants, négatifs search terms, budgets - pas de mutation sans accord. |

## Meta Ads

| Action | Livre |
| --- | --- |
| Vue compte | Comptes, campagnes/ad sets, perfs via MCP `meta-ads` (`https://mcp.facebook.com/ads`). |
| Structure de campagne | Audience → ad set → créas, placements, checklist pixel/CAPI ; nouvelles entités en pause sauf demande explicite. |
| Boucle créa & budget | Fatigue créative, CPA élevés, réallocations budgétaires avec preuves uniquement. |

## TikTok Ads

| Action | Livre |
| --- | --- |
| Vue advertiser | Advertisers autorisés, campagnes et rapports via MCP `tiktok-ads`. |
| Structure de campagne | Campagne → ad group → ads short-form, notes Spark/In-Feed, checklist pixel/events. |
| Boucle perf | Recommandations CPA/CTR cut/scale et rotation créative. |

## Reddit Ads

| Action | Livre |
| --- | --- |
| Vue compte | Comptes, campagnes, ad groups, perfs 7 jours via MCP `reddit-ads` (tier `read` par défaut). |
| Plan ciblage communauté | Structure subreddit/intérêts via outils MCP search si dispo. |
| Optimisation hebdo | Pause/scale et swaps créatifs ; pas d'écriture spend sans accord. |

## LinkedIn Ads

| Action | Livre |
| --- | --- |
| Vue compte | Comptes, groupes de campagnes, métriques 7 jours via MCP `linkedin-ads` ou export Campaign Manager. Sous `ads/linkedin/` + `ads-report-*.html`. |
| Structure titre / secteur | Groupe → campagne → créa avec ICP B2B serré (titre, secteur, séniorité). Nouvelles entités en pause sauf demande explicite. |
| Optimisation B2B hebdo | Kill/scale à partir du CPL et des démographies ; lecture seule d'abord, pas de mutation de spend sans accord. |
