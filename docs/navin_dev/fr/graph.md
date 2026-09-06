# Graphe (métagraphe)

L'onglet **Graphe** du module Dev affiche la carte des dépendances du projet : fichiers (ou packages) en nœuds, imports résolus en arêtes. Il s'appuie sur l'index code (`navin.index`) et les annotations `.metadata/index.json` produites par `/atlas`.

Ce n'est pas un outil d'architecture DSL (type C4) : la source de vérité reste le code analysé.

## Où le trouver

Dans l'atelier Dev (`#/dev`), ouvrez l'onglet **Graphe** du panneau latéral (à côté de Recherche, Git, etc.).

## Ce que montre le graphe

| Élément | Signification |
| --- | --- |
| Nœud | Un fichier (vue Fichiers) ou un dossier top-level (vue Packages) |
| Couleur | Nature : front, back, sql, config, test, docs, asset, other |
| Arête | Import / dépendance résolue (ou `depends_on` manuel dans `.metadata`) |
| Taille du point | Degré (plus un fichier est connecté, plus le point est grand) |
| Anneau | Rôle renseigné (ambre si le rôle est *stale* après une modification du fichier) |

Plafond d'affichage : 6000 nœuds (les plus connectés sont conservés en priorité).

## Modes

### Fichiers

Un nœud = un fichier du projet. Clic pour épingler le détail ; double-clic pour ouvrir le fichier dans l'éditeur.

### Packages

Les fichiers sont agrégés par dossier de premier niveau (`webui`, `navin`, `(root)` pour les fichiers à la racine). Double-clic (ou **Ouvrir les fichiers du package**) bascule en vue Fichiers filtrée sur ce dossier.

## Interactions

- **Filtres kind** - masquer / afficher front, back, sql…
- **Recherche** - filtrer par chemin
- **Connectés uniquement** - cacher les fichiers sans arête
- **Impact** - à partir d'un fichier sélectionné, met en évidence tous les fichiers qui en dépendent (cône transitif)
- **Chemin vers…** - après sélection d'un fichier source, cliquez une cible pour highlighter le plus court chemin de dépendance
- **Zoom / pan / fit** - molette, glisser, boutons en haut à droite du canvas

Le panneau de droite liste le rôle, le kind, les imports et les importés-par.

## Temps réel

Le graphe se met à jour sans refresh manuel :

1. L'agent écrit un fichier → l'index est marqué dirty → rebuild débouncé (~300 ms)
2. Une annotation `.metadata` est enregistrée → rebuild immédiat
3. Le serveur diffuse `metagraph_updated` (WebSocket) avec un **diff structuré** (nœuds / arêtes ajoutés, retirés, mis à jour) et les positions
4. Le client applique le diff si la `generation` est contiguë ; sinon il refetch le snapshot complet

Un poll de secours (20 s) couvre les cas où un event aurait été manqué.

## Atlas & métadonnées

| Commande | Effet |
| --- | --- |
| `/atlas init` | Première construction de `.metadata/index.json` |
| `/atlas refresh` | Actualise les rôles / dépendances |
| `/atlas query …` | Questions d'orientation sur l'index |

Sans `.metadata`, le graphe fonctionne quand même : imports parsés + rôles lus depuis les docstrings. Avec `.metadata`, les rôles manuels et `depends_on` enrichissent la carte ; les empreintes (`fingerprint`) détectent les rôles périmés.

## Tool agent `metagraph`

L'agent interroge la même carte que l'UI.

| Action | Rôle |
| --- | --- |
| `overview` | Synthèse : kinds, hubs, couverture des rôles, stale |
| `file` | Rôle + dépendances / dependents d'un chemin |
| `find` | Recherche par fragment de chemin / rôle / kind |
| `hubs` | Fichiers les plus connectés |
| `path` | Plus court chemin `from` → `to` |
| `impact` | Dependents transitifs d'un fichier (`radius` optionnel) |
| `cluster` | Membres d'un package / dossier |
| `annotate` | Enregistre un rôle (et optionnellement kind, tags, `depends_on`) |

Utiliser `code_index` pour le niveau symbole (définition, callers) ; `metagraph` pour le niveau fichier.

## Architecture technique

```
navin.index (Python)  →  snapshot  →  moteur Graph
                                      ├─ navin-core (Rust) si disponible
                                      └─ fallback Python sinon
                                              ↓
                              payload + positions + generation
                                              ↓
                         HTTP GET …/metagraph?view=files|packages
                         WS metagraph_updated (diffs)
                                              ↓
                              DevMetagraph + MetagraphCanvas
```

- **Build / layout / queries** : Rust (`graph_build`, `graph_layout`, `graph_diff`, `graph_query`) via PyO3 ; layout force-directed déterministe, positions sticky pour les diffs live.
- **Fallback** : `NAVIN_DISABLE_NATIVE=1` ou absence de wheel → assembly Python ; le canvas recalcule le layout côté TypeScript si le payload n'a pas de `positions`.
- **Installer le moteur natif** : `make native` à la racine du dépôt.

Ouvrez l'onglet **Graph** dans Project Home pour passer de la vue fichiers à la vue packages. La carte se met à jour pendant que vous travaillez.

## Voir aussi

- [Vue d'ensemble](./README.md)
- [Atelier](./workbench.md)
- [Commandes](./commands.md) (`/atlas`)
