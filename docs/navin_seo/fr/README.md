# Module SEO - Vue d'ensemble

Le module **SEO** (barre latérale → **SEO**, route `#/seo`) est un **bureau SEO senior** : audits grounded (fetch réel), mots-clés par intention, contenu prêt à publier, schema, gaps concurrents, liens/local/GEO - sous contrat expert. Sans API data (DataForSEO/Semrush), Search Console live (MCP) ou export GSC, les volumes/KD restent qualitatifs (`n/a - requires API data`), jamais inventés.

## Connecter Search Console

Pour des requêtes, pages et signaux d'indexation en live, connectez le preset MCP **Google Search Console** :

1. Ouvrez **Réglages → MCP**.
2. Installez le preset **Google Search Console** (`search-console`).
3. Fournissez **soit** le chemin OAuth Desktop `client_secrets.json` (`GSC_OAUTH_CLIENT_SECRETS_FILE`), **soit** un JSON de compte de service (`GSC_CREDENTIALS_PATH`, avec `GSC_SKIP_OAUTH=true`).
4. Prérequis : `uvx` (Astral uv) sur la machine du gateway. Les outils destructifs sitemap restent désactivés par défaut.

Les skills SEO préfèrent ce MCP aux exports CSV quand il est connecté. Les jobs cron/loop réutilisent le même env gateway.

## Moteur de preuves

`/seo` utilise d'abord l'outil intégré `seo`. Ses actions sont `crawl`, `audit`, `schema`, `psi`, `crux`, `serp_snapshot`, `serp_history`, `score`, `report` et `pipeline`. Le crawl est borné et réutilise le transport sécurisé du Scraping. PSI v5 et CrUX v1 renvoient un `data_gap` explicite sans identifiants optionnels. Les snapshots SERP exigent DataForSEO ou Semrush, portent une source et une confiance, puis sont enregistrés dans un historique append-only atomique. Aucune position, aucun volume et aucune métrique de performance ne sont déduits.

## Fonctionnement

1. Ouvrez **SEO** dans la barre latérale.
2. Cliquez une carte d'action dans l'un des trois groupes - **Audit**, **Recherche**, **Optimisation** (voir [Actions](./actions.md)).
3. Le chat s'ouvre avec `/seo` et le prompt déjà dans le compositeur. Ajoutez l'URL ou le sujet dans le chat, puis envoyez.
4. L'agent livre un résultat actionnable classé par impact, enregistré en fichiers quand c'est substantiel.

Usage direct dans un chat du module SEO :

```
/seo https://exemple.fr - audit technique complet
/seo recherche de mots-clés pour boulangerie artisanale à Lyon
```

## La commande `/seo`

| | |
| --- | --- |
| Commande | `/seo [url\|sujet]` |
| Cycle de vie | Workflow agent (tour d'agent complet) |
| Skills préchargés | `studio-expert-contract`, `critic-reviewer`, `seo-technical-auditor`, `keyword-research`, `on-page-seo-optimizer`, `seo-content-writer`, `backlink-strategy`, `competitor-seo-analysis`, `local-seo`, `geo-ai-search-optimizer`, `seo-data-provider` |
| Board | Run tracké (plan live via `project-board`) |
| Sortie | Fichiers sous `seo/` + `seo-report-*.html` + gate expert |

## Couverture

| Domaine | Ce que fait l'agent |
| --- | --- |
| Technique | Preuves issues d'un crawl borné pour statuts, redirections, robots, sitemaps, canonicals, hreflang, meta, titres, données structurées, liens internes, PSI et CrUX |
| Mots-clés | Expansion, intention, priorisation qualitative et métriques fournisseur uniquement quand leur source est disponible |
| Contenu | Briefs et articles complets : titre, structure H, entités, liens internes, FAQ, balisage schema.org |
| Concurrents | Stratégie de contenu, structure de site, mots-clés ciblés, analyse d'écarts avec opportunités |
| Liens | Plans de maillage interne et acquisition de backlinks avec modèles d'outreach |
| Local | Fiche d'établissement, citations, avis, pages localisées |
| Recherche IA (GEO) | Optimisation pour les réponses générées par IA et les moteurs de réponse |

## Suivi continu

Combinez avec les capacités d'autonomie de Navin :

- `/goal surveille les positions de exemple.fr chaque semaine et alerte-moi en cas de chute` - un objectif de fond que l'agent poursuit.
- Tâches cron (via le skill `cron`) pour des audits et rapports planifiés.
- Les skills `seo-monitoring` et `website-monitor` pour les vérifications récurrentes.

## Astuces

- Donnez toujours l'URL pour les audits ; l'agent récupère les vraies pages.
- Enchaînez : audit technique → liste de correctifs → `/forge` (dans Dev) pour appliquer les corrections à votre code.
- Demandez le format de livrable voulu : tableau, fichier CSV, ou article complet en markdown/HTML.
