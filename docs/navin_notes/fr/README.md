# Module Notes - Vue d'ensemble

Le module **Notes** (barre latérale → **Notes**, route `#/notes`) est l'espace de connaissance de Navin : un éditeur par blocs façon Notion, des bases de données structurées, un graphe de connaissance, des tâches, des fichiers - et surtout une **mémoire IA native** : vos notes deviennent du contexte interrogeable par l'agent, et une note peut lancer du vrai travail (blocs agent).

Tout est **local-first** : chaque note est un fichier Markdown avec un frontmatter YAML sous `~/.navin/notes/`. Pas de format propriétaire, pas de lock-in - vos notes restent lisibles avec n'importe quel éditeur.

## Disposition

```
┌────────────┬──────────────────┬──────────────────────────────┐
│ Rail       │  Liste des notes │  Éditeur                     │
│ Sections   │  (recherche,     │  titre, tags, blocs,         │
│ Dossiers   │   épinglées,     │  autosave + conflits         │
│ Tags       │   aperçus)       │                              │
└────────────┴──────────────────┴──────────────────────────────┘
```

Les trois colonnes défilent indépendamment. Le **mode focus** masque le chat pour écrire en plein écran. Le chat Navin reste accessible à droite dans la vue standard.

## Sections

| Section | Contenu |
| --- | --- |
| **Toutes les notes** | Liste complète, recherche plein texte (Ctrl+K), épinglées en premier |
| **Base de données** | Les notes comme données structurées : vues **Table** (cellules éditables) et **Kanban** (glisser-déposer par statut), propriétés personnalisées (texte, nombre, date, statut, sélection...), vues enregistrées avec filtres |
| **Graphe** | Graphe de connaissance : nœuds notes + tags, arêtes `[[wikilinks]]` et associations de tags ; clic sur un nœud pour ouvrir la note ou filtrer par tag |
| **Tâches** | Tous les items `- [ ]` de toutes les notes, cochables ici, ajout rapide sans ouvrir de note, priorités `p1`/`p2` |
| **Fichiers** | Toutes les pièces jointes (`_files/`), upload direct, indication des notes qui les référencent, **aperçu intégré** au clic |
| **Corbeille** | Notes supprimées : restaurer, supprimer définitivement, ou vider la corbeille (confirmations dans l'app, jamais de dialogue navigateur) |
| **Commandes** | Référence de toutes les commandes slash et astuces, générée depuis le vrai catalogue |

Sous les sections : l'arborescence des **dossiers** (création inline, suppression avec mise à la corbeille du contenu) et la liste des **tags** avec compteurs.

## Éditeur

Éditeur par blocs (Tiptap) avec Markdown natif : ce que vous tapez est ce qui est stocké.

### Commandes slash

Tapez `/` en début de ligne :

| Commande | Bloc |
| --- | --- |
| `/title` `/h2` `/h3` | Titres |
| `/text` | Paragraphe |
| `/todo` | Liste de tâches à cocher |
| `/bullet` `/numbered` | Listes |
| `/quote` | Citation |
| `/code` | Bloc de code avec coloration syntaxique |
| `/table` | Tableau |
| `/divider` | Séparateur |
| `/link` | Insérer un lien hypertexte (dialogue dans l'app : texte + URL) |
| `/image` | Téléverser et intégrer une image |
| `/file` | Joindre un fichier |
| `/agent` | **Bloc agent exécutable** (voir plus bas) |

La recherche du menu tolère les accents et les synonymes français/anglais (`/tache` trouve To-do).

### Barre de mise en forme

Sélectionnez du texte : une barre flottante apparaît avec **gras**, **italique**, **lien**, six **couleurs de texte** et six **surlignages** (rouge, orange, ambre, vert, bleu, violet - pratique pour marquer des priorités), plus une gomme pour effacer les couleurs. Les couleurs survivent à l'aller-retour Markdown (HTML inline) et restent lisibles en thème clair comme sombre.

### Liens et connaissance

- `[[Titre d'une note]]` crée un **wikilink** ; les titres et les **alias** sont résolus sans casse.
- `/link` ou le bouton lien de la barre de sélection insèrent un lien web classique `[texte](url)` ; Ctrl+clic (Cmd+clic sur macOS) l'ouvre dans le navigateur OS (via le gateway sur desktop / Tauri, où `window.open` est bloqué).
- Les **rétroliens** (backlinks) apparaissent automatiquement sous chaque note.
- Les `#tags` du frontmatter alimentent la section Tags et le Graphe.

### Enregistrement

- **Autosave** (800 ms après la dernière frappe) et Ctrl+S pour forcer.
- **Détection de conflit** : le contrôle de version et l'écriture partagent un verrou inter-processus, donc deux auteurs ne peuvent pas s'écraser silencieusement.
- **Historique de versions** conservé dans `.history/<id-note>/`.

### Pièces jointes

Trois façons d'ajouter un fichier à une note : `/image` ou `/file`, **glisser-déposer**, ou **coller** (Ctrl+V, pratique pour les captures d'écran). Le fichier part dans `_files/` et la note garde un chemin relatif portable (jamais de token dans le Markdown).

Au clic, un **aperçu intégré au produit** s'ouvre (important pour les builds desktop Windows/macOS/Linux : pas d'onglet navigateur, pas d'URL avec token) :

- images (png, jpg, gif, webp, svg...),
- PDF (viewer Chromium embarqué),
- vidéo et audio (lecteurs avec contrôles),
- Markdown (rendu riche : titres, tableaux, code coloré),
- texte/code (affichage monospace),
- autres types : écran « pas d'aperçu » avec bouton Télécharger (via blob, le token ne quitte jamais l'app).

## Mémoire IA et Navin IA

La barre d'outils de la note propose un menu **Navin IA** (à côté d'Exporter) :

| Action | Effet |
| --- | --- |
| **Demander à Navin** | Préremplit le chat avec le titre + le chemin `~/.navin/notes/….md` pour que vous finissiez la question |
| **Résumé** | Envoie un prompt pour écrire une section `## Résumé` **en haut** du même fichier |
| **Traduction** | Envoie un prompt pour ajouter une section `## Traduction` **sous** le texte existant |
| **Correction** | Envoie un prompt pour **corriger en place** (orthographe, grammaire, clarté) sans dupliquer la note |

Exemple de prompt : `Résume cette note : « Sans titre » (fichier ~/.navin/notes/sans-titre-3.md)`.

- Les notes sont **indexées sémantiquement** en arrière-plan : découpage par sections Markdown, embeddings (par défaut Ollama `nomic-embed-text` via l'endpoint semanticSearch), recherche **hybride** sémantique + lexicale avec fusion RRF.
- La recherche plein texte utilise un index SQLite FTS5 persistant sur les titres, corps, tags et alias. Un fallback lexical déterministe prend le relais sans FTS5. Les modifications externes sont détectées par empreinte.
- L'agent dispose d'un outil `notes` (lecture seule) : recherche et lecture de vos notes comme contexte pendant n'importe quel tour - vos notes deviennent la mémoire du workspace.

Les dossiers Markdown, vaults Obsidian et exports zip Markdown de Notion peuvent être importés avec extraction sandboxée et gestion explicite des conflits `rename`, `skip` ou `overwrite`.

## Export

Menu **Exporter** par note dans la barre d'outils :

| Format | Comportement |
| --- | --- |
| **Markdown (.md)** | Télécharge la note (HTML TipTap des couleurs / surlignages conservé pour le round-trip) |
| **Texte (.txt)** | Texte seul (balises HTML retirées) |
| **PDF** | Ouvre la boîte d'impression à partir du HTML de l'éditeur (couleurs, listes, tableaux, tâches) - choisir « Enregistrer au format PDF » |

## Blocs agent (`/agent`)

Un bloc agent est un bloc de code clôturé avec le langage `agent` (round-trip Markdown parfait, lisible par l'agent avec les outils fichiers). Le modèle inséré décrit **Objectif / Sources / Sortie**. Le bloc s'affiche en carte avec un bouton **Run** : le clic construit un prompt structuré (spécification du bloc + titre + chemin de la note) et le place dans le compositeur du chat ; l'agent exécute et ajoute le résultat dans la note sous `## Résultat`.

C'est la brique « la note ne décrit plus le travail : elle lance le travail ».

## Stockage et format

```
~/.navin/notes/
├── <dossier>/<note>.md      # Markdown + frontmatter YAML
├── _files/                  # pièces jointes (préfixe hash)
├── .history/                # historique par note
├── .notes-manifest.json     # index atomique id vers chemin
├── .search.sqlite3          # index plein texte persistant
├── _views.json              # vues de la base de données
└── .trash/                  # corbeille
```

Frontmatter d'une note :

```yaml
---
id: 9f2c1a
title: Lancement V3
tags: [produit, roadmap]
aliases: [V3]
pinned: false
archived: false
folder: projets
props: { statut: "en cours", priorite: 1 }
created: 2026-08-10T18:00:00.000000Z
updated: 2026-08-10T19:30:00.000000Z
---
```

Les fichiers critiques utilisent un temporaire dans le même dossier, `fsync` du fichier, `os.replace` atomique et `fsync` du dossier lorsque le système le permet. Notes, vues, historique, manifeste et générations de l'index mémoire sont protégés contre les écritures partielles.
