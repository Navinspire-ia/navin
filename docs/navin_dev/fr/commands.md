# Commandes slash — référence complète

Navin embarque 32 commandes slash. Tapez `/` dans le composer de n'importe quel chat pour ouvrir la palette. Les commandes se répartissent en trois cycles de vie :

- **Workflow agent** — réécrit votre message en brief de mission (avec les bons skills préchargés) et exécute un tour d'agent normal.
- **Canal direct** — répond immédiatement sans consommer de tour d'agent.
- **Contrôle de tour** — gère le chat/tour courant lui-même.

## Workflows agent

| Commande | Titre | Arguments | Ce qu'elle fait |
| --- | --- | --- | --- |
| `/blueprint` | Mode plan | `[tâche]` | Conçoit un plan d'implémentation avant d'écrire du code : contraintes, options, approche retenue, liste d'étapes. Aucune modification de code. |
| `/forge` | Mode build | `[tâche]` | Mode construction autonome complet : planifier, coder, exécuter, tester et itérer jusqu'au bout. |
| `/atlas` | Atlas du projet | `[init\|refresh\|query <question>]` | Construit ou actualise `.metadata/` (rôle, nature et dépendances de chaque fichier), alimente l'onglet Graphe du Dev workbench, et répond aux questions « où est X ? » depuis l'index plutôt qu'avec des grep. |
| `/inspect` | Code review | `[chemin\|diff\|scope]` | Passe en revue les changements récents ou une cible : bugs, régressions de comportement, problèmes de sécurité, tests manquants, maintenabilité. Cite fichiers et lignes. |
| `/fortify` | Revue sécurité | `[chemin\|scope]` | Audit de sécurité : flux d'authentification, validation des entrées, surfaces d'injection, gestion des secrets, défauts dangereux, risques de dépendances. Constats notés critical→low. |
| `/probe` | Scan de vulnérabilités | `[chemin\|scope]` | Chasse aux failles exploitables : OWASP Top 10, secrets en dur, versions de dépendances vulnérables, SSRF/path traversal, injection de prompt. |
| `/turbo` | Audit performance | `[chemin\|scope]` | Profile et optimise : chemins chauds, requêtes N+1, I/O bloquantes, caches manquants, taille de bundle, mémoire. Mesure avant de recommander. |
| `/pulse` | Métriques & analytics | `[focus]` | Tableau de bord santé du projet : taille/complexité du code, fraîcheur des dépendances, lint, couverture, dette TODO, KPIs. |
| `/studio` | Studio documents | `[format + brief]` | Crée des documents PowerPoint, Word, PDF ou Excel soignés. Voir [navin_contenant](../../navin_contenant/README.md). |
| `/campaign` | Studio marketing | `[brief]` | Génère des campagnes de bout en bout : copies pub, posts sociaux, images produit, scripts vidéo. Voir [navin_marketing](../../navin_marketing/README.md). |
| `/seo` | Studio SEO | `[url\|sujet]` | SEO complet : audits techniques, recherche de mots-clés, contenu optimisé, analyse concurrentielle. Voir [navin_seo](../../navin_seo/README.md). |
| `/leads` | Studio leads & ventes | `[icp\|entreprise\|brief]` | Prospection experte : recherche entreprises/personnes/emplois, signaux d'achat, scoring, outreach. Voir [navin_leads](../../navin_leads/README.md). |
| `/team` | Studio équipe | `[mission\|brief org]` | Conception d'org, fiches de rôle, RACI, recrutement, OKRs — et équipes d'agents IA via subagents. Voir [navin_equipe](../../navin_equipe/README.md). |
| `/goal` | Objectif de fond | `<objectif>` | Enregistre la demande comme objectif de longue durée que l'agent poursuit sur plusieurs tours jusqu'à complétion ou arrêt. |

Toutes les commandes workflow acceptent une cible optionnelle. Sans cible, l'agent déduit lui-même le périmètre le plus utile.

## Utilitaires (canal direct)

| Commande | Arguments | Ce qu'elle fait |
| --- | --- | --- |
| `/checkpoint` | `[save [note]\|list\|restore <nom> [all\|chat\|code]\|delete <nom>]` | Les checkpoints sont sauvegardés automatiquement avant chaque prompt. Revenez en arrière sur la conversation, le code, ou les deux. Ils complètent git, sans le remplacer. |
| `/pilot` | `[tâche]` | Routage modèle-par-tâche. Tâches : `search`, `plan`, `review`, `security`, `dev`, `fast`, `deep`, `docs`. Définissez des presets portant ces noms dans **Réglages → Modèles** ; `/pilot review` bascule alors sur le preset `review`. Sans argument, affiche le mapping courant. |
| `/pack` | `[list\|install <url-git\|chemin>\|enable <nom>\|disable <nom>\|remove <nom>]` | Gère les packs de plugins (skills + serveurs MCP). Voir [Plugins](./plugins.md). |
| `/model` | `[preset]` | Affiche ou change le preset de modèle actif. |
| `/status` | — | Statut du runtime, du provider et des canaux (quota de recherche web inclus si disponible). |
| `/skill` | — | Liste tous les skills activés disponibles pour l'agent. |
| `/help` | — | Liste les commandes slash disponibles. |
| `/history` | `[n]` | Affiche les N derniers messages persistés de la conversation. |
| `/trigger` | `<nom>` | Crée un déclencheur CLI nommé lié à cette session de chat. |
| `/pairing` | `[list\|approve <code>\|deny <code>\|revoke <user_id>]` | Gère les demandes d'appairage DM par canal. |
| `/restart` | — | Redémarre le processus Navin. |

## Mémoire (Dream)

| Commande | Arguments | Ce qu'elle fait |
| --- | --- | --- |
| `/dream` | — | Déclenche manuellement la consolidation de mémoire en deux phases. |
| `/dream-log` | — | Montre ce que la dernière consolidation Dream a changé. |
| `/dream-restore` | — | Restaure la mémoire depuis un snapshot Dream précédent. |
| `/dream-prompt` | `[init]` | Personnalise la façon dont Dream organise la mémoire de cet espace de travail. |
| `/evaluator-prompt` | `[init]` | Personnalise le prompt de filtrage des notifications heartbeat. |

## Contrôle de tour

| Commande | Ce qu'elle fait |
| --- | --- |
| `/new` | Réinitialise ce chat et démarre une conversation vierge (finalise d'abord le tour actif). |
| `/stop` | Annule le tour d'agent actif de ce chat. |

## Astuces

- Combinez avec la cible fichier : après ouverture d'un fichier dans l'éditeur, le menu **Actions** peut restreindre une commande à ce fichier (ex. `/inspect src/App.tsx`).
- Enchaînez les workflows : `/blueprint` d'abord, validez le plan, puis `/forge` pour l'exécuter.
- Utilisez `/pilot deep` avant une investigation difficile, `/pilot fast` pour les tâches rapides.
- `/checkpoint save avant-refactor` avant une opération risquée, puis `/checkpoint restore avant-refactor code` pour ne restaurer que les fichiers.
