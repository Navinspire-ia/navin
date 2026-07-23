# Module Équipe — Vue d'ensemble

Le module **Équipe** (barre latérale → **Équipe**, route `#/team`) est un bureau de chief of staff et de conception d'organisation. L'agent conçoit des structures d'entreprise et d'équipe à partir de la stratégie, rédige les fiches de rôle et les matrices RACI, construit les plans de recrutement et les kits d'entretien, cascade les OKRs, prépare les revues d'ops — et peut **lancer une équipe virtuelle d'agents IA**, chacun avec un rôle et un livrable, pour exécuter une mission en parallèle.

## L'organisation interactive

Le haut de la vue affiche votre **organigramme** : des membres avec avatar, nom, rôle, spécialité et leurs skills clés, disposés par ligne hiérarchique (niveau racine d'abord, puis les rattachés de chaque manager en dessous). Une organisation de départ est fournie — Atlas (Chief of Staff) et six leads : Forge (Ingénierie), Nova (Marketing), Vector (SEO), Compass (Ventes), Mentor (RH & Talents), Scribe (Documents).

- **Appeler** sur une carte membre ouvre le chat et route la conversation vers ce membre : sa commande de workflow (`/forge`, `/campaign`, `/seo`, `/leads`, `/studio`, `/team`…) plus son rôle, sa spécialité et ses skills comme persona.
- **Ajouter / modifier / supprimer un membre** : construisez votre propre organisation — nom, rôle, spécialité, avatar emoji, commande de workflow, skills et rattachement hiérarchique. Stockée côté serveur (`~/.navin/webui/team-roster.json`), partagée par tous les chats.

### Appeler les membres depuis n'importe quel chat

Tapez `@` dans n'importe quel composer : vos membres d'équipe apparaissent en premier dans la palette de mentions (avatar + rôle). En choisir un réécrit le message pour qu'il s'exécute en tant que ce membre — ex. `@nova lance une campagne pour notre nouvelle offre` devient `/campaign You are Nova, Marketing Lead… lance une campagne pour notre nouvelle offre`. Membres, apps CLI et serveurs MCP partagent la même palette `@`.

## Fonctionnement

1. Ouvrez **Équipe** dans la barre latérale.
2. (Optionnel) Saisissez un **brief** en haut : votre entreprise, stade, effectif, objectif. Il est joint à chaque action.
3. Choisissez une carte d'action dans l'un des quatre groupes — **Concevoir**, **Recruter**, **Piloter**, **Progresser** (voir [Actions](./actions.md)).
4. Le chat s'ouvre et `/team` est envoyé automatiquement avec la spécification de l'action et votre brief.
5. L'agent travaille et enregistre chaque artefact en fichiers sous `org/` ou `team/` dans l'espace de travail.

Utilisation directe dans n'importe quel chat :

```
/team conçois l'organisation d'une startup SaaS de 12 personnes qui sort du sales fondateur
/team lance une équipe virtuelle pour produire notre data room investisseurs : analyste, rédacteur, relecteur
```

## La commande `/team`

| | |
| --- | --- |
| Commande | `/team [mission\|brief org]` |
| Cycle de vie | Workflow agent (exécute un tour agent complet) |
| Skills préchargés | `org-designer`, `virtual-team-builder`, `multi-agent-orchestration`, `task-planner`, `recruitment-agent`, `job-description-writer`, `candidate-screening`, `kpi-reporter`, `human-approval` |
| Sortie | Organigrammes (Mermaid), fiches de rôle, RACI, rituels, plans de recrutement, OKRs, packs de revue — enregistrés dans l'espace de travail |

## L'équipe IA virtuelle

La capacité distinctive : l'agent peut staffer une mission avec de **vrais subagents**, pas seulement des documents.

1. Il dérive 2 à 5 rôles de la mission, chacun avec exactement un livrable et un chemin de sortie précis.
2. Il rédige un brief complet par agent (mission, contexte, skills à appliquer, limites) — les subagents ne partagent pas le contexte du parent.
3. Il les lance avec l'outil subagent, en parallèle ou en pipeline (recherche → rédaction → relecture).
4. Il suit l'effectif dans `team/roster.md`, vérifie chaque livrable contre le brief, et fusionne les résultats lui-même.

Patterns d'orchestration disponibles : spécialistes en parallèle, pipeline, producteur + critique, manager + squad. Les équipes sont plafonnées à 5 agents simultanés ; au-delà, l'agent vous demande d'abord.

## Principes de conception appliqués par l'agent

- **La structure suit la stratégie** — l'org est dérivée des flux de résultats et de la charge, jamais copiée d'un modèle.
- **Un propriétaire par résultat** — pas de rôle sans résultats et KPIs ; pas de résultat avec deux propriétaires.
- **Org minimale viable** — la plus petite structure qui fonctionne, plus une trajectoire de croissance avec des déclencheurs de recrutement métriques.
- **Droits de décision explicites** — RACI par processus clé, chemins d'escalade, aucune décision orpheline.

## Fonctionnement continu

À combiner avec les fonctions d'autonomie de Navin :

- `/goal maintiens le suivi des OKRs à jour et signale les key results à risque chaque semaine` — un objectif durable.
- Des jobs cron pour les packs de revue d'ops récurrents et les pulses de santé d'équipe.
- `/pilot` pour router le travail de conception lourd et les mises à jour rapides vers des modèles différents.

## Conseils

- Donnez du vrai contexte dans le brief : stade, effectif, revenu, ce qui casse — la conception d'org devient nettement meilleure.
- Enchaînez les groupes dans un même chat : conception → fiches de rôle → plan de recrutement → kits d'entretien.
- Pour les missions, préférez la carte équipe IA virtuelle quand il y a ≥3 chantiers séparables ; en dessous, l'exécution solo est plus rapide.
