# Commandes slash - référence complète

Navin embarque des commandes slash intégrées. Tapez `/` dans le composer de n'importe quel chat pour ouvrir la palette. Les commandes se répartissent en trois cycles de vie :

- **Workflow agent** - réécrit votre message en brief de mission (avec les bons skills préchargés) et exécute un tour d'agent normal.
- **Canal direct** - répond immédiatement sans consommer de tour d'agent.
- **Contrôle de tour** - gère le chat/tour courant lui-même.

La plupart des workflows sécurité / qualité sont aussi accessibles via le [menu Actions](./actions.md) du module Code. Les modes du composer (Plan / Agent / Review / Security / Debug) sont documentés dans [Modes](./modes.md).

## Workflows agent

### Build & qualité

| Commande | Titre | Arguments | Ce qu'elle fait |
| --- | --- | --- | --- |
| `/blueprint` | Mode plan | `[tâche]` | Conçoit un plan et un Task Ledger de mission avant d'écrire du code : contraintes, faits, infos manquantes, critères d'acceptation, étapes avec validation. Aucune modification de code. Bascule le composer en Plan. Voir [Mode Plan](./plan-mode.md). |
| `/forge` | Mode build | `[tâche]` | Build autonome : boucle Progress Ledger (claim → work → valider avec evidence → next), replan local en cas de stall. Bascule le composer en Agent. |
| `/cruise` | Build autopilot | `[tâche]` | Planifie, exécute, teste, détecte stall/boucles, replanifie localement jusqu'à done ou pause - sans attendre Build. Bascule le composer en Agent. |
| `/mission` | Mission longue | `[objectif]` | Mission multi-tours durable avec ledger, checkpoints, reprise, sous-agents et rapport final. Bascule le composer en Agent. |
| `/mobile` | Lancer Mobile | `[android\|ios\|web\|metro\|doctor]` | Détecte Expo / React Native / Flutter, diagnostique l'environnement, démarre le packager (et éventuellement un émulateur Android), puis utilise l'onglet Mobile pour voir et interagir avec l'appareil. |
| `/atlas` | Atlas du projet | `[init\|refresh\|query <question>]` | Construit ou actualise `.metadata/` (rôle, nature et dépendances de chaque fichier), alimente l'onglet Graphe du Dev workbench, et répond aux questions « où est X ? » depuis l'index plutôt qu'avec des grep. |
| `/inspect` | Code review | `[chemin\|diff\|scope]` | Revue experte (mode Review) : correctness, SQL/données, API, frontend, odeurs sécu, tests, perf, maintenabilité. Outils : `code_review` (scope/filter/report). Chaque finding : `fichier:ligne`, impact, fix et exemple réel. Clôture avec `review-report-*.html` (File Preview auto) et un plan de remédiation numéroté - demande quel `#` démarrer. `pr_comments` optionnel (PR + `gh`). Lecture seule sauf demande de fix. |
| `/debug` | Mode debug | `[signal\|chemin\|scope]` | Reproduire l'échec, prouver la cause racine avec des preuves réelles (stack/logs/test/DebugMCP). Outils : `debug_repair` (mcp_status/start_branch/report). Clôture avec `debug-report-*.html` (File Preview auto) et des choix de fix numérotés. Demande quel `#` démarrer. Applique du code seulement si demandé. |
| `/turbo` | Audit performance | `[chemin\|scope]` | Profile et optimise : chemins chauds, requêtes N+1, I/O bloquantes, caches manquants, taille de bundle, mémoire. Mesure avant de recommander. |
| `/pulse` | Métriques & analytics | `[focus]` | Tableau de bord santé du projet : taille/complexité du code, fraîcheur des dépendances, lint, couverture, dette TODO, KPIs. |
| `/board` | Tableau de tâches | `[task <id>\|loop\|status\|plan <goal>]` | Travaille le board partagé du projet : planifier, prendre des tâches, dispatcher, reporter. |
| `/goal` | Objectif de fond | `<objectif>` | Enregistre la demande comme objectif de longue durée que l'agent poursuit sur plusieurs tours jusqu'à complétion ou arrêt. |

### Audit sécurité (défensif)

| Commande | Titre | Arguments | Ce qu'elle fait |
| --- | --- | --- | --- |
| `/fortify` | Revue sécurité | `[chemin\|scope]` | Passe AppSec experte (mode Security) : carte de surface, `security_scan`, scanners hôtes si présents, injection/données, frontend, authz, réseau, chaîne d'appro, vie privée, LLM/agent. Chaque finding : sévérité, preuve, PoC réel, correctif minimal. Clôture avec `security-report-*.html` (File Preview auto) et choix de durcissement numérotés - demande quel `#` démarrer. `pr_comments` optionnel. Lecture seule sauf demande de fix. |
| `/probe` | Scan de vulnérabilités | `[chemin\|scope]` | Chasse aux failles exploitables : OWASP Top 10, secrets en dur, versions de dépendances vulnérables, SSRF/path traversal, injection de prompt. |
| `/unmask` | Scan de secrets | `[chemin\|scope]` | Clés, tokens et mots de passe dans le code, la config et l'historique git. |
| `/lineage` | Audit chaîne d'appro | `[chemin\|scope]` | Dépendances : CVE, paquets obsolètes, intégrité des lockfiles, licences. |
| `/xray` | Analyse statique | `[chemin\|scope]` | SAST : trace l'entrée utilisateur jusqu'aux sinks dangereux. |
| `/gatekeeper` | Contrôle d'accès | `[chemin\|scope]` | Auth et autorisation : sessions, tokens, rôles, IDOR. |
| `/perimeter` | Surface API & web | `[chemin\|scope]` | Endpoints exposés : authz, CORS, CSRF, SSRF, en-têtes, quotas. |
| `/bastion` | Infra & IaC | `[chemin\|scope]` | Docker, Kubernetes, Terraform, CI/CD et config cloud. |
| `/vault` | Données & vie privée | `[chemin\|scope]` | PII, chiffrement, fuites de logs, rétention, écarts RGPD. |

### Offensif & conformité

| Commande | Titre | Arguments | Ce qu'elle fait |
| --- | --- | --- | --- |
| `/recon` | Reconnaissance | `[chemin\|cible]` | Cartographie la surface d'attaque : routes, services, sous-domaines, empreinte techno. |
| `/threatmap` | Modèle de menace | `[chemin\|scope]` | Surface d'attaque, frontières de confiance et menaces STRIDE avec mitigations. |
| `/dast` | Test dynamique | `[cible\|scope]` | Lance l'app et sonde en direct : injection, auth, IDOR, SSRF, XSS/CSRF. |
| `/redteam` | Simulation d'attaque | `[chemin\|scope]` | Enchaîne des faiblesses en chemins d'exploit réalistes avec PoC sans risque. |
| `/pentest` | Pentest autonome | `[cible\|scope]` | Cycle complet : recon, modèle de menace, scan, exploitation avec PoC, rapport validé. |
| `/comply` | Conformité | `[framework\|scope]` | Écarts vs OWASP ASVS, CIS, SOC 2, ISO 27001, PCI-DSS. |
| `/report` | Rapport de pentest | `[format\|scope]` | Compile les findings validés en rapport partageable, prêt pour la conformité. |

### Studios

| Commande | Titre | Arguments | Ce qu'elle fait |
| --- | --- | --- | --- |
| `/studio` | Studio documents | `[format + brief]` | Crée des documents PowerPoint, Word, PDF ou Excel soignés. Voir [navin_contenant](../../navin_contenant/README.md). |
| `/campaign` | Studio marketing | `[brief]` | Génère des campagnes de bout en bout : copies pub, posts sociaux, images produit, scripts vidéo. Voir [navin_marketing](../../navin_marketing/README.md). |
| `/seo` | Studio SEO | `[url\|sujet]` | SEO complet : audits techniques, recherche de mots-clés, contenu optimisé, analyse concurrentielle. Voir [navin_seo](../../navin_seo/README.md). |
| `/leads` | Studio leads & ventes | `[icp\|entreprise\|brief]` | Chasse desk (open data d'abord), BANT-F, Start loop, heartbeat watch. Voir [navin_leads](../../navin_leads/README.md), [loop](../../navin_leads/fr/loop.md), [desktop](../../navin_leads/fr/desktop.md). |
Toutes les commandes workflow acceptent une cible optionnelle. Sans cible, l'agent déduit lui-même le périmètre le plus utile.

## Utilitaires (canal direct)

| Commande | Arguments | Ce qu'elle fait |
| --- | --- | --- |
| `/checkpoint` | `[save [note]\|list\|restore <nom> [all\|chat\|code]\|delete <nom>]` | Les checkpoints sont sauvegardés automatiquement avant chaque prompt. Revenez en arrière sur la conversation, le code, ou les deux. Ils complètent git, sans le remplacer. |
| `/pilot` | `[tâche]` | Bascule manuelle modèle-par-tâche. Tâches : `search`, `plan`, `review`, `security`, `dev`, `fast`, `deep`, `docs`. Assignez chaque rôle à un **preset nommé** dans **Réglages → Modèles → Routage par tâche** (`modelRoutes`). Les workflows (`/forge`, `/blueprint`, audits, studios…) appliquent ces routes automatiquement ; `/pilot review` bascule aussi le preset de session. Sans argument, affiche le mapping courant. |
| `/pack` | `[list\|install <url-git\|chemin>\|enable <nom>\|disable <nom>\|remove <nom>]` | Gère les packs de plugins (skills + serveurs MCP). Voir [Plugins](./plugins.md). |
| `/model` | `[preset]` | Affiche ou change le preset de modèle actif. |
| `/status` | - | Statut du runtime, du provider et des canaux (quota de recherche web inclus si disponible). |
| `/skill` | - | Liste tous les skills activés disponibles pour l'agent. |
| `/help` | - | Liste les commandes slash disponibles. |
| `/history` | `[n]` | Affiche les N derniers messages persistés de la conversation. |
| `/trigger` | `<nom>` | Crée un déclencheur CLI nommé lié à cette session de chat. |
| `/pairing` | `[list\|approve <code>\|deny <code>\|revoke <user_id>]` | Gère les demandes d'appairage DM par canal. |
| `/restart` | - | Redémarre le processus Navin. |

## Mémoire (Dream)

| Commande | Arguments | Ce qu'elle fait |
| --- | --- | --- |
| `/dream` | - | Déclenche manuellement la consolidation de mémoire en deux phases. |
| `/dream-log` | - | Montre ce que la dernière consolidation Dream a changé. |
| `/dream-restore` | - | Restaure la mémoire depuis un snapshot Dream précédent. |
| `/dream-prompt` | `[init]` | Personnalise la façon dont Dream organise la mémoire de cet espace de travail. |
| `/evaluator-prompt` | `[init]` | Personnalise le prompt de filtrage des notifications heartbeat. |

## Contrôle de tour

| Commande | Ce qu'elle fait |
| --- | --- |
| `/new` | Réinitialise ce chat et démarre une conversation vierge (finalise d'abord le tour actif). |
| `/stop` | Annule le tour d'agent actif de ce chat. |

## Astuces

- Modes du composer : choisissez Plan / Agent / Review / Security / Debug dans le menu Mode - le texte libre est préfixé automatiquement. Voir [Modes](./modes.md).
- Combinez avec la cible fichier : après ouverture d'un fichier dans l'éditeur, le menu **Actions** peut restreindre une commande à ce fichier (ex. `/inspect src/App.tsx`).
- Enchaînez les workflows : `/blueprint` d'abord, validez le plan, puis `/forge`. Utilisez `/cruise` en autopilot et `/mission` pour le travail durable (voir [Mode Plan](./plan-mode.md)).
- Après Review / Security / Debug : ouvrez le rapport HTML dans File Preview, répondez `Start with #1` (ou un autre numéro), puis Agent / `/forge`.
- Cycle pentest : `/recon` → `/threatmap` → `/probe` ou `/dast` → `/redteam` → `/comply` → `/report` (ou `/pentest` pour tout enchaîner).
- Le routage est automatique pour les workflows (`/forge` → `dev`, `/blueprint` → `plan`, `/inspect` → `review`, `/fortify` → `security`, `/debug` → `deep`, …). Utilisez `/pilot <tâche>` surtout en chat libre quand vous voulez rester sur un rôle.
- `/checkpoint save avant-refactor` avant une opération risquée, puis `/checkpoint restore avant-refactor code` pour ne restaurer que les fichiers.
- Référence complète du menu : [Actions](./actions.md).
