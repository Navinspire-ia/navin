# Atelier (Workbench)

Chaque partie de l'atelier Dev est interactive : les panneaux se redimensionnent en glissant les séparateurs, se replient avec les boutons de la barre d'onglets, et mémorisent leur état.

## Sélecteur de projet

En haut à droite de l'atelier. Il fonctionne comme le sélecteur de dossier de VS Code/Cursor :

- **Projets récents** - les derniers projets ouverts, un clic pour y revenir.
- **Accès rapides** - répertoire personnel, racine du système, et (sous WSL) les disques Windows comme `/mnt/c`.
- **Parcourir** - une boîte de dialogue de navigation dans le système de fichiers.
- **Saisir un chemin** - collez ou tapez n'importe quel chemin absolu (`/home/moi/app`, `/mnt/c/Users/moi/proj`, un chemin serveur monté…).

Sélectionner un projet définit le **périmètre de travail** de la session d'agent active : arborescence, terminaux, diagnostics, statut git et tous les outils de l'agent opèrent dans ce dossier. Si aucune session d'agent n'existe, une est créée automatiquement avec ce périmètre.

Environnements détectés : Linux, WSL (avec interop Windows), macOS, Windows.

## Explorateur

- Arborescence du projet chargée à la demande.
- Création, renommage et suppression de fichiers et dossiers.
- Clic sur un fichier pour l'ouvrir dans un onglet d'éditeur.
- Repliable via le bouton de panneau ; redimensionnable en glissant son bord.

## Éditeur

- Basé sur CodeMirror, coloration syntaxique par langage, thèmes clair/sombre.
- Onglets multiples, indicateur de brouillon non sauvegardé, sauvegarde via le bouton ou `Ctrl/Cmd+S`.
- **Diagnostics en ligne** : les fichiers Python sont vérifiés avec Ruff, les fichiers JSON avec un parseur. Erreurs et avertissements sont soulignés (squiggles) et comptés dans la barre d'état. Rafraîchissement à l'ouverture et à la sauvegarde.
- Les fichiers mentionnés par l'agent dans le chat sont cliquables et s'ouvrent directement dans l'éditeur.

## Terminaux

- Vrais terminaux PTY dans l'atelier, onglets multiples.
- Choix du shell : `bash`, `sh`, `zsh` quand disponibles - et sous WSL, les shells interop Windows : **PowerShell** et **cmd.exe**.
- Interactivité complète (flèches, touches ctrl, applications TUI), panneau redimensionnable et repliable.
- Les exécutions shell de l'agent défilent dans le chat ; vos terminaux restent les vôtres.

## Aperçu / Navigateur

- Volet d'aperçu intégré pour les applications web en cours d'exécution (serveurs de dev, etc.).
- La zone centrale bascule entre les modes **éditeur** et **navigateur** ; l'URL d'aperçu est éditable et rechargeable.
- **Publier** (à côté d'Ouvrir) expose le port local via un Cloudflare Quick Tunnel et affiche une URL publique `*.trycloudflare.com`. Détails : [Publier l'aperçu](./preview-publish.md).

## Preview Mobile

- Onglet central **Mobile** (à côté de **Preview** web) : écran Android en direct via adb, logcat, FPS / mémoire / CPU.
- Clic = tap, glisser = swipe ; boutons Retour / Accueil / Récents dans la barre.
- Démarrer avec `/mobile android` ou le bouton **Lancer Mobile**, puis **Démarrer le preview** une fois un appareil en ligne.
- Détails : [Mobile](./mobile.md) et la référence complète [docs/mobile.md](../../mobile.md).

## Panneau de chat agent

- Panneau de droite, redimensionnable en glissant le séparateur, masquable via le bouton de panneau.
- **Agents multiples** : chaque onglet est une session de chat indépendante avec sa propre boucle d'agent, sa mémoire, son nom et son périmètre. Création avec **+** (un nom vous est demandé), renommage par double-clic sur l'onglet, fermeture avec **×**.
- Un point vert signale les agents en cours d'exécution.
- Le chat affiche le travail de l'agent en direct : étapes de raisonnement, appels d'outils, diffs de fichiers, sorties de commandes - en blocs structurés façon Cursor.

## Barre d'état

En bas de l'atelier, façon VS Code :

| Élément | Signification |
| --- | --- |
| Environnement | `WSL`, `Linux`, `macOS`… détecté côté backend |
| Git | branche courante, indicateur de modifications, compteurs ahead/behind (rafraîchi périodiquement et à la sauvegarde) |
| Projet | nom du projet actif |
| Fichier actif | nom du fichier plus ses compteurs d'erreurs/avertissements issus des diagnostics |

## Checkpoints

Avant chaque prompt, Navin sauvegarde la session (conversation + contenu des fichiers suivis). Utilisez `/checkpoint list`, `/checkpoint restore <nom> [all|chat|code]`, `/checkpoint save [note]`, `/checkpoint delete <nom>`. Les checkpoints complètent git - ce sont des points de retour rapides, pas un gestionnaire de versions.

## Voir aussi

- [Graphe](./graph.md) - carte des dépendances, modes Fichiers/Packages, temps réel
- [Publier l'aperçu](./preview-publish.md)
- [Mobile](./mobile.md)
- [Commandes](./commands.md)
- [Actions](./actions.md)
