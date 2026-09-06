# Module RiskLens - Vue d'ensemble

Le module **RiskLens** (barre latérale → **RiskLens**, route `#/risklens`) est le studio à ouvrir **avant** Code. Il suppose que votre plan, lancement, embauche ou décision a déjà échoué dans 6 mois, puis remonte chaque vraie raison d'échec pour produire un plan révisé et une checklist pré-lancement.

La méthode vient du psychologue Gary Klein (Harvard Business Review). L'idée centrale : demander « qu'est-ce qui pourrait mal tourner ? » donne des réponses prudentes ; dire « ça a déjà échoué - expliquez pourquoi » bascule le raisonnement en mode narratif et fait surgir des causes bien plus spécifiques.

Dans Navin, RiskLens est placé **juste avant le module Code** dans la barre latérale : expliquez et stress-testez l'idée avant de démarrer le projet.

## Fonctionnement

1. Ouvrez **RiskLens** dans la barre latérale (au-dessus de **Code**).
2. Cliquez une carte d'action dans l'un des trois groupes - **Lancer**, **Décisions**, **Focus** (voir [Actions](./actions.md)).
3. Le chat s'ouvre avec `/risklens` et le prompt de la carte déjà dans le compositeur (taille adaptée au texte). Complétez le plan dans le chat, puis envoyez.
4. L'agent rassemble le contexte manquant si besoin, pose le cadre d'échec, génère les raisons, deep-dive en parallèle, synthétise, puis enregistre les fichiers de rapport.

Usage direct dans le chat du module :

```
/risklens lancement d'un SaaS de facturation à 29€/mois pour freelances FR, 200 payants en 6 mois
```

## La commande `/risklens`

| | |
| --- | --- |
| Commande | `/risklens [plan\|lancement\|décision]` |
| Cycle de vie | Workflow agent (tour d'agent complet) |
| Skills préchargés | `risklens`, `multi-agent-orchestration`, `task-planner` |
| Sortie | Synthèse (échec le plus probable, le plus dangereux, hypothèse cachée, plan révisé, checklist) + fichiers HTML/MD dans l'espace de travail |

## Déroulement d'une session

| Étape | Ce que fait l'agent |
| --- | --- |
| Contexte | Vérifie qu'il a le minimum : quoi, pour qui, à quoi ressemble le succès. Pose des questions ciblées s'il manque un élément. |
| Cadre | Pose explicitement : « dans 6 mois, ce plan a déjà échoué ». |
| Raisons brutes | Liste toutes les vraies raisons d'échec, ancrées dans les détails du plan (pas de padding générique). |
| Deep-dives | Un sous-agent par raison, en parallèle (`spawn`) : histoire d'échec, hypothèse sous-jacente, signaux d'alerte. |
| Synthèse | Échec le plus probable, le plus dangereux, hypothèse cachée, plan révisé concret, checklist 3-5 actions. |
| Livrables | `risklens-report-[timestamp].html`, `risklens-transcript-[timestamp].md`, résumé court dans le chat. |

## Bonnes et mauvaises cibles

**Bonnes cibles**

- Produit ou feature sur le point d'être construits
- Lancement avec argent ou réputation en jeu
- Changement de pricing ou de modèle économique
- Embauche imminente
- Pivot de stratégie ou de positionnement
- Partenariat ou deal à évaluer
- Tout engagement où se tromper coûte cher

**Mauvaises cibles**

- Idées vagues sans plan concret (planifiez d'abord, puis risklens)
- Questions avec une seule bonne réponse
- Feedback créatif sur un brouillon (c'est de l'édition)
- Décisions déjà prises et irréversibles

## Scoping module

`/risklens` appartient au module RiskLens. Depuis **Code**, la commande est masquée de la palette et refusée si elle est tapée - ouvrez RiskLens dans la barre latérale, puis relancez. Les autres studios (`/seo`, `/campaign`, …) restent hors de RiskLens.

## Enchaînement recommandé

1. **RiskLens** - stress-testez le plan, obtenez le plan révisé et la checklist.
2. **Documents** (`/studio`) - si vous devez formaliser le brief ou le pitch.
3. **Code** (`#/code`) - seulement après, pour implémenter la version révisée.

## Astuces

- Donnez toujours les trois éléments de contexte : quoi, pour qui, succès.
- Le produit utile est la **synthèse** et le **plan révisé**, pas la liste brute des peurs.
- Exigez des révisions actionnables cette semaine (« pilote à 47€ avec 20 personnes »), pas des conseils vagues.
- L'agent ne doit **pas** commencer à coder dans ce module - c'est volontaire.
