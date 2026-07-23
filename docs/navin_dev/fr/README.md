# Module Dev — Vue d'ensemble

Le module **Dev** transforme Navin en environnement de développement complet, dans le navigateur. Il combine un atelier façon VS Code (explorateur de fichiers, éditeur de code avec diagnostics en ligne, terminaux, aperçu live) avec un ou plusieurs chats d'agents autonomes capables de planifier, écrire, exécuter, tester et corriger le code de votre projet.

Ouvrez-le depuis la barre latérale (**Dev**) ou via `#/dev`.

## Disposition

```
┌──────────────┬─────────────────────────────┬──────────────────┐
│ Explorateur  │  Éditeur / Aperçu / Browser │  Chat(s) agent   │
│ (fichiers)   │  + onglets, diagnostics     │  onglets, stream │
├──────────────┴─────────────────────────────┤                  │
│  Terminaux (bash, sh, PowerShell, cmd)     │                  │
├────────────────────────────────────────────┴──────────────────┤
│  Barre d'état : env (WSL/Linux/macOS) · branche git · erreurs │
└───────────────────────────────────────────────────────────────┘
```

Chaque panneau est **redimensionnable, repliable et masquable** : glissez les séparateurs, utilisez les boutons de panneau dans la barre d'onglets, ou repliez entièrement l'explorateur / le terminal / le chat.

## Capacités clés

- **Sélecteur de projet** (en haut à droite) : ouvrez n'importe quel dossier — local, WSL, chemins serveur montés — en naviguant ou en tapant un chemin. Les projets récents sont mémorisés.
- **Onglets multi-agents** : plusieurs sessions d'agent indépendantes côte à côte, chacune avec son nom, sa mémoire et son périmètre de travail. Double-clic sur un onglet pour le renommer.
- **Menu Actions** : audits en un clic (code review, sécurité, vulnérabilités, performance, UX/UI, accessibilité, refactoring, docs) sur tout le projet ou le fichier actif, avec auto-correction optionnelle. Voir [Actions](./actions.md).
- **Commandes slash** : 32 commandes intégrées, dont des workflows agent (`/blueprint`, `/forge`, `/inspect`, `/fortify`, `/probe`, `/turbo`, `/pulse`) et des utilitaires (`/checkpoint`, `/pilot`, `/pack`). Voir [Commandes](./commands.md).
- **Checkpoints** : l'état de l'agent (conversation + fichiers) est sauvegardé automatiquement avant chaque prompt ; revenez en arrière sur le chat, le code, ou les deux avec `/checkpoint`.
- **Routage de modèles** : `/pilot <tâche>` bascule vers le preset de modèle associé à un type de tâche (search, plan, review, security, dev, fast, deep, docs).
- **Atlas du projet & métagraphe** : `/atlas` construit une base de connaissance `.metadata/` (rôle, nature et dépendances de chaque fichier). L'onglet **Graphe** l'affiche en carte interactive — nœuds colorés par nature (front, back, SQL, config, test), arêtes issues des imports analysés, clic sur un nœud pour ouvrir le fichier.
- **Permissions de l'agent** : un panneau graphique dans **Réglages → Sécurité → Permissions de l'agent** pour interdire ou autoriser des commandes shell (préfixes simples ou regex), activer la restriction au projet, et consulter les protections intégrées. Les changements s'appliquent à chaud à l'agent en cours.
- **Diagnostics** : les fichiers Python (Ruff) et JSON sont analysés en direct ; erreurs et avertissements sont soulignés dans l'éditeur et comptés dans la barre d'état.
- **Recherche dans le projet** : l'onglet **Recherche** du panneau latéral fouille tout le projet (basé sur ripgrep, options casse/regex) ; un clic sur un résultat ouvre le fichier à la ligne exacte.
- **Contrôle de source** : l'onglet **Git** liste les fichiers indexés, modifiés et non suivis ; un clic sur un fichier affiche son diff unifié (les fichiers non suivis apparaissent comme entièrement ajoutés) et permet d'ouvrir le fichier dans l'éditeur.
- **Intégration git** : la barre d'état affiche la branche courante, l'état modifié et les compteurs ahead/behind.
- **Support WSL** : sous Windows/WSL, les terminaux peuvent ouvrir des shells Linux et les shells interop Windows (PowerShell, cmd.exe), et les disques Windows (`/mnt/c`, …) sont navigables.
- **Fonctionnement continu** : objectifs de fond (`/goal`), tâches cron et heartbeat permettent aux agents de tourner en boucles longues et autonomes, avec des garde-fous intégrés.

## Démarrage rapide

1. Ouvrez **Dev** dans la barre latérale.
2. Choisissez un projet avec le **sélecteur de projet** (en haut à droite) — navigation, saisie de chemin, ou projet récent.
3. Créez un agent avec le bouton **+** de la barre d'onglets du chat et donnez-lui un nom.
4. Demandez ce que vous voulez (« ajoute un mode sombre »), utilisez une commande (`/forge implémente la page de login`), ou cliquez une **Action**.
5. Suivez le travail de l'agent : étapes, appels d'outils, modifications de fichiers et sorties de terminal défilent dans le chat. Les fichiers mentionnés sont cliquables et s'ouvrent dans l'éditeur.

## Pages liées

- [Atelier](./workbench.md) — chaque panneau en détail
- [Commandes](./commands.md) — référence complète des commandes slash
- [Actions](./actions.md) — le menu d'actions rapides
- [Skills](./skills.md) — les skills dev chargés par l'agent
- [Plugins](./plugins.md) — étendre Navin avec des packs
