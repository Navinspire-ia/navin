# Module SEO — Vue d'ensemble

Le module **SEO** (barre latérale → **SEO**, route `#/seo`) est une agence SEO complète : audits techniques, recherche de mots-clés avec cartographie d'intention, contenu optimisé prêt à publier, balisage schema, analyse d'écarts concurrentiels et stratégie de liens. Quand vous donnez une URL, l'agent récupère les vraies pages — les constats s'appuient sur le site réel, pas sur des suppositions.

## Fonctionnement

1. Ouvrez **SEO** dans la barre latérale.
2. (Optionnel) Tapez un **brief** en haut : URL du site, activité, marché cible, langue. Il est joint à chaque action.
3. Choisissez une carte d'action dans l'un des trois groupes — **Audit**, **Recherche**, **Optimisation** (voir [Actions](./actions.md)).
4. Le chat s'ouvre et `/seo` part automatiquement avec la spécification de l'action et votre brief.
5. L'agent livre un résultat actionnable classé par impact, enregistré en fichiers quand c'est substantiel.

Usage direct dans n'importe quel chat :

```
/seo https://exemple.fr — audit technique complet
/seo recherche de mots-clés pour boulangerie artisanale à Lyon
```

## La commande `/seo`

| | |
| --- | --- |
| Commande | `/seo [url\|sujet]` |
| Cycle de vie | Workflow agent (tour d'agent complet) |
| Skills préchargés | `seo-technical-auditor`, `keyword-research`, `on-page-seo-optimizer`, `seo-content-writer`, `backlink-strategy`, `competitor-seo-analysis`, `local-seo`, `geo-ai-search-optimizer` |
| Sortie | Constats priorisés, tableaux de mots-clés, articles optimisés, balisage JSON-LD — fichiers dans l'espace de travail |

## Couverture

| Domaine | Ce que fait l'agent |
| --- | --- |
| Technique | Indexabilité (robots, canonicals, sitemaps), structure meta et titres, données structurées, maillage interne, signaux de performance |
| Mots-clés | Expansion de seeds, classification d'intention (informationnelle/commerciale/transactionnelle), estimation de difficulté et de valeur, clustering, priorisation |
| Contenu | Briefs et articles complets : titre, structure H, entités, liens internes, FAQ, balisage schema.org |
| Concurrents | Stratégie de contenu, structure de site, mots-clés ciblés, analyse d'écarts avec opportunités |
| Liens | Plans de maillage interne et acquisition de backlinks avec modèles d'outreach |
| Local | Fiche d'établissement, citations, avis, pages localisées |
| Recherche IA (GEO) | Optimisation pour les réponses générées par IA et les moteurs de réponse |

## Suivi continu

Combinez avec les capacités d'autonomie de Navin :

- `/goal surveille les positions de exemple.fr chaque semaine et alerte-moi en cas de chute` — un objectif de fond que l'agent poursuit.
- Tâches cron (via le skill `cron`) pour des audits et rapports planifiés.
- Les skills `seo-monitoring` et `website-monitor` pour les vérifications récurrentes.

## Astuces

- Donnez toujours l'URL pour les audits ; l'agent récupère les vraies pages.
- Enchaînez : audit technique → liste de correctifs → `/forge` (dans Dev) pour appliquer les corrections à votre code.
- Demandez le format de livrable voulu : tableau, fichier CSV, ou article complet en markdown/HTML.
