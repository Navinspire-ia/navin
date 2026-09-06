# Skills de développement

Les skills sont des guides en markdown que l'agent charge à la demande (ou automatiquement via les commandes workflow). Gérez-les dans **Réglages → Skills** ou listez-les avec `/skill`. Les skills ci-dessous alimentent le module Dev ; le catalogue complet en contient beaucoup d'autres (marketing, SEO, documents, RH, ventes…).

## Ingénierie

| Skill | Rôle |
| --- | --- |
| `fullstack-dev` | Construction de fonctionnalités de bout en bout : frontend, backend, base de données. Défauts web : un DS officiel (MUI / Fluent / Carbon, `ask_user` si absent) + `ui-ux-pro-max` + `framer-motion`. |
| `ui-ux-pro-max` | Tokens / landings mappés sur MUI, Fluent ou Carbon + `framer-motion` obligatoire. Jamais Tailwind par défaut. Utilisé par `/forge` `/cruise` `/mission` `/blueprint`. |
| `make-interfaces-feel-better` | Polish UI : motion, rayons, ombres, typo (complète `ui-ux-pro-max`). |
| `mobile-dev` | Expo / React Native / Flutter : detect, doctor, run, preview, tap/swipe, correction redbox (utilisé par `/mobile`). |
| `api-engineer` | Conception et implémentation d'API. |
| `task-planner` | Découpage du travail en étapes ordonnées et vérifiables (utilisé par `/blueprint`). |
| `test-generator` | Écriture de suites de tests pertinentes. |
| `code-reviewer` | Méthodologie de revue de code structurée (utilisé par `/inspect` + outil `code_review`). |
| `critic-reviewer` | Seconde passe de revue contradictoire. |
| `skill-creator` / `skill-vetter` | Créer et auditer de nouveaux skills. |
| `pack-builder` | Créer et auditer des packs de plugins. |
| `debug-live` | Debug avec preuves : DebugMCP, branche isolée, `debug_repair`, rapport HTML (utilisé par `/debug`). |
| `studio-html-report` | Contrat des rapports HTML expert / studios (File Preview auto pour Review/Security/Debug). |

## Sécurité

| Skill | Rôle |
| --- | --- |
| `security-auditor` | Audits de sécurité systématiques (utilisé par `/fortify` + outil `security_scan`). |
| `vulnerability-scanner` | Chasse aux failles : OWASP, CVE, secrets (utilisé par `/probe`). |
| `prompt-injection-defender` | Détection et neutralisation des surfaces d'injection de prompt. |
| `secrets-manager` | Manipulation sûre des identifiants et tokens. |
| `permission-guard` | Application des frontières de permissions. |
| `audit-logger` | Traçabilité des opérations sensibles. |

## Performance & qualité

| Skill | Rôle |
| --- | --- |
| `performance-auditor` | Profilage et optimisation (utilisé par `/turbo`). |
| `metrics-analyst` | Collecte et notation des métriques projet (utilisé par `/pulse`). |
| `kpi-reporter` | Tableaux de bord et rapports de KPI. |
| `quality-gate` | Évaluation pass/fail multi-critères. |

## Ops & infrastructure

| Skill | Rôle |
| --- | --- |
| `docker-operator` | Conteneurs : build, exécution, débogage. |
| `kubernetes-operator` | Déploiements K8s et dépannage. |
| `terraform-agent` | Infrastructure as code. |
| `cicd-agent` | Pipelines et livraison continue. |
| `observability-agent` | Câblage logs, traces, métriques. |
| `backup-rollback` | Snapshots d'état et restaurations sûres. |
| `shell-sandbox` | Bonnes pratiques d'exécution shell sandboxée. |
| `tmux` | Gestion de sessions terminal de longue durée. |

## Données

| Skill | Rôle |
| --- | --- |
| `database-explorer` | Découverte de schémas et requêtage. |
| `sql-analyst` | Analyse et optimisation SQL. |
| `supabase-operator` | Projets Supabase : BDD, auth, stockage. |
| `stripe-operator` | Intégration et opérations Stripe. |
| `data-migration-agent` | Migrations de schémas/données sûres. |
| `data-quality-agent` | Validation et nettoyage des données. |

## Autonomie de l'agent

| Skill | Rôle |
| --- | --- |
| `multi-agent-orchestration` | Coordination de sous-agents sur une tâche. |
| `adaptive-reasoning` | Choix de la bonne profondeur de raisonnement. |
| `model-router` | Choix du bon modèle par tâche (auto via Routage par tâche + `/pilot`). |
| `context-compressor` | Maintien des sessions longues dans le contexte. |
| `self-healing-retry` | Récupération après étapes échouées. |
| `proactive-agent` | Anticipation des besoins pendant les objectifs longs. |
| `human-approval` | Pause pour validation humaine sur les étapes sensibles. |
| `memory` | Conventions de mémoire long terme (fonctionne avec Dream). |

Les skills sont chargés automatiquement par les commandes workflow (ex. `/fortify` précharge `security-auditor`, `permission-guard`, `secrets-manager`) ou sur demande : demandez simplement à l'agent « utilise le skill code-reviewer ».

Outils agent derrière Review / Security / Debug : [Outils expert](./expert-tools.md).
