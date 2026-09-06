# Project Home

Project Home (`#/project`) est le hub de continuité pour les projets qui durent des semaines ou des mois. Il répond en quelques secondes à une seule question : **où en étions-nous, et quelle est la suite ?** Tout ce que la page affiche lit les mêmes fichiers et les mêmes API que le workbench Code et que l'agent lui-même : ce que vous voyez est toujours ce que l'agent voit.

Ouvrez-le depuis l'entrée **Projet** de la sidebar, ou directement : `#/project?chat=<clé-de-session>`.

## Un seul cerveau, toutes les surfaces

Project Home est une fenêtre sur le pack durable créé dans chaque projet :

```
<projet>/
  .navin/
    board/            board.json, milestones.json, activity.jsonl,
                      settings.json (consentement autonomie), mission.json
    continuity/       RESUME.md, DECISIONS.md
  memory/MEMORY.md    faits durables et contraintes
  SOUL.md, USER.md    persona de l'agent et préférences utilisateur
```

Code, Project Home et tous les studios (Marketing, SEO, Documents, ...) partagent ce pack, et l'agent le relit à chaque tour. Éditer ici change ce que l'agent sait dès sa prochaine réponse.

## Onglets

| Onglet | Ce qu'il montre | Propulsé par |
| --- | --- | --- |
| **Vision 360** | L'audit complet du projet : santé globale et scores par domaine, modules API, architecture, explorateur d'API, permissions / RBAC, requêtes SQL, findings sécurité et performance, export PDF | Même composant `DevProjectAudit` que Code |
| **Tasks** | Le board complet - exactement le même kanban que Code : colonnes, bandeau plan (prêtes / bloquées / chemin critique), détail de tâche, dépendances, commentaires, toggle **Autonomy**, **Sync GitHub**, Run agent | Même composant `DevBoardPanel` que Code |
| **Git** | Timeline branches / commits / merges avec le graphe des relations | API timeline git |
| **Issues** | Les issues GitHub du dépôt (ouvertes / fermées / toutes) avec auteur, labels, nombre de commentaires, plus **Importer dans le board** et une action **Corriger avec l'agent** par issue | CLI `gh` via la gateway |
| **Evolutions** | Jalons roadmap, plan d'exécution, file prête, timeline d'activité avec filtres d'acteur | Même composant `DevEvolutions` que Code |
| **Graph** | Le métagraphe de dépendances : vues Fichiers / Packages, filtres, génération `.metadata` | Même composant `DevMetagraph` que Code |
| **Resume** | Brief de reprise, tâches ouvertes, plan de session, jalons avec progression, sessions récentes, reprise en un clic | APIs board + brain |
| **Brain** | `RESUME.md`, `DECISIONS.md`, `MEMORY.md`, `SOUL.md`, `USER.md` éditables, contraintes extraites, note de dérive | API project brain |
| **Timeline** | Le fil d'activité du board : chaque mutation humaine et agent, plus récent d'abord | `activity.jsonl` |

### Resume

Assemble une **graine de reprise** à partir du brief `RESUME.md`, des contraintes de `MEMORY.md` et des tâches ouvertes prioritaires, puis le bouton **Resume** ouvre un chat avec cette graine pré-remplie : l'agent reprend exactement là où le projet s'était arrêté, même après des semaines d'absence. Project Home s'ouvre sur **Vision 360** par défaut ; Resume vient après les onglets opérationnels.

### Tasks

Le board n'est pas une copie : c'est le même composant, la même API et les mêmes événements live `board_updated` que le workbench Code. Rien à faire deux fois - une carte déplacée ici est déplacée partout, y compris pour l'agent.

Depuis cet onglet vous pouvez aussi :

- activer l'**[autonomie du board](./board-autonomy.md)** (dialog de consentement : branche isolée par tâche, PR au done, sync des issues) ;
- **Sync GitHub** pour importer les issues ouvertes en tâches ;
- **Run agent on board** pour confier la file des tâches prêtes à l'agent.

Les cartes portent la piste d'autonomie : puce branche, lien **PR** et lien **Issue**, tous cliquables.

### Evolutions

Même vue roadmap / jalons / plan d'exécution / activité que l'onglet Evolutions du workbench Code : créer des jalons, voir ce qui est prêt maintenant, et filtrer le fil d'activité du board par humain, agent ou sous-agent. Lit `milestones.json` et `activity.jsonl` sous `.navin/board/`.

### Issues

Liste les issues GitHub du dépôt via le CLI `gh` authentifié : filtre d'état (ouvertes / fermées / toutes), auteur, labels, nombre de commentaires, liens directs. **Importer dans le board** crée une tâche par issue ouverte absente du board (dédupliquée par URL d'issue, labellisée `github` plus les labels de l'issue). Si `gh` manque ou si le dépôt n'a pas de remote GitHub, l'onglet le dit honnêtement au lieu d'afficher une coquille vide.

Chaque issue ouverte a aussi un bouton **Corriger avec l'agent** : un clic envoie à l'agent un brief strict - reproduire le problème, corriger sur une branche de tâche isolée, lancer les tests concernés jusqu'au vert complet, commit, PR si PR-au-done est actif, et seulement ensuite fermer l'issue sur GitHub et re-synchroniser le board. L'agent a pour consigne explicite de ne jamais fermer une issue dont le fix n'est pas testé et commité. Le même comportement sans clic passe par l'option de consentement **Corriger les issues en autonomie** décrite dans [Autonomie du board](./board-autonomy.md).

### Vision 360 et Graph

Les deux embarquent les panneaux du workbench Code tels quels : il n'existe qu'une seule implémentation à laquelle se fier - le moteur d'audit derrière **Vision 360** (scores de santé, findings sécurité/perf basés AST avec confiance + preuve, carte RBAC, inventaire SQL, export PDF) et le **métagraphe** de dépendances (Fichiers / Packages, analyse d'impact, génération `.metadata`). Voir [Graphe](./graph.md) pour la référence complète. Tasks, Evolutions et Graph suivent la même règle : un seul composant partagé avec Code.

### Brain

Accès direct en lecture-écriture aux fichiers de mémoire de l'agent, avec indicateur de modifications non enregistrées. Les contraintes listées sous `## Constraints` dans `MEMORY.md` sont extraites et affichées ; l'agent les reçoit à chaque tour.

## Synchronisation live

- Les mutations du board (humaines ou agent, quelle que soit la surface) diffusent `board_updated` ; chaque vue ouverte se rafraîchit.
- Un polling de secours à 15 s couvre les événements manqués.
- Le scaffold est idempotent : ouvrir un projet auquel il manque une partie du pack la crée sans jamais écraser les fichiers existants.

## Voir aussi

- [Autonomie du board](./board-autonomy.md) - branche auto, PR au done, sync des issues, consentement
- [Mode Plan](./plan-mode.md) - `/blueprint` `/forge` `/cruise` `/mission`
- [Atelier](./workbench.md) - le côté Code du même projet
- [Graphe](./graph.md) - référence du métagraphe
