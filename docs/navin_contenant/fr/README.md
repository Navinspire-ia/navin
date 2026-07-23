# Module Documents — Vue d'ensemble

Le module **Documents** (barre latérale → **Documents**, route `#/content`) est un studio documentaire : choisissez un modèle, ajoutez un brief optionnel, et l'agent conçoit puis génère un vrai fichier prêt à partager — PowerPoint, Word, PDF ou Excel — dans votre espace de travail.

L'ambition dépasse les outils de slides comme Gamma : le résultat est un véritable fichier bureautique éditable, avec du contenu documenté, une structure pensée et un thème visuel cohérent — produit par un agent qui peut aussi chercher sur le web, lire vos fichiers projet et itérer sur vos retours.

## Fonctionnement

1. Ouvrez **Documents** dans la barre latérale.
2. (Optionnel) Tapez un **brief** en haut : produit, cible, objectif, ton. Il est joint à chaque action cliquée.
3. Filtrez par thème si besoin (Projet, Investissement, Marketing, Ventes, RH & Formation).
4. Cliquez une carte de modèle. Le panneau de chat s'ouvre et la commande `/studio` part automatiquement avec la spécification du modèle et votre brief.
5. L'agent clarifie ou déduit les détails manquants, conçoit la structure (sections, arc narratif, une idée par slide/page), génère le fichier avec du vrai contenu — jamais de lorem ipsum — applique un thème visuel, et rapporte le chemin du fichier plus un sommaire.

Vous pouvez aussi utiliser la commande directement dans n'importe quel chat :

```
/studio un deck investisseurs de 12 slides pour une startup solaire B2B, thème bleu sobre
```

## La commande `/studio`

| | |
| --- | --- |
| Commande | `/studio [format + brief]` |
| Cycle de vie | Workflow agent (tour d'agent complet) |
| Skills préchargés | `pptx-generator`, `docx-generator`, `pdf-generator`, `spreadsheet-analyst`, `presentation-designer`, `professional-writer`, `document-templates` |
| Sortie | Un vrai fichier enregistré dans l'espace de travail (`.pptx`, `.docx`, `.pdf`, `.xlsx`) |

L'agent utilise `python-pptx`, `python-docx`, `openpyxl` et des outils PDF pour construire les fichiers par programmation : tout reste éditable ensuite.

## Formats

| Badge | Format | Usage typique |
| --- | --- | --- |
| PPTX | PowerPoint | Pitch decks, plans, supports de formation, decks de marque |
| DOCX | Word | Chartes, business plans, propositions, livrets |
| PDF | PDF | One-pagers, rapports, battlecards |
| XLSX | Excel | Roadmaps, budgets, modèles financiers, pipelines, calendriers |

## Thèmes visuels & templates personnels

Un sélecteur **Thème** se trouve sous le champ de brief, avec 8 thèmes visuels intégrés : Executive, Minimal, Tech, Bold, Chaleureux, Nature, Élégant, Corporate. Chacun a une spécification exacte (palette, polices, règles de mise en page) définie dans le skill `document-templates` ; choisissez-en un et chaque document généré l'applique de façon cohérente. **Auto** laisse l'agent choisir le thème adapté à l'audience.

Vous pouvez aussi utiliser **votre propre template** : déposez un fichier `.pptx` / `.docx` / `.xlsx` dans l'espace de travail (par convention sous `templates/`) et dites « utilise templates/pitch.pptx comme base ». L'agent ouvre le fichier, réutilise ses masques et styles, remplit le contenu et enregistre le résultat dans un nouveau fichier — votre template n'est jamais écrasé. Si vous donnez l'URL explicite d'un template gratuit, l'agent peut le télécharger dans `templates/` et l'adapter (en notant la licence).

## Catégories de modèles

25 modèles répartis en 5 catégories — voir [Modèles](./templates.md) pour chaque carte :

- **Projet** — pitch deck, charte, roadmap, rapport d'avancement, budget
- **Investissement** — deck investisseurs, business plan, modèle financier, one-pager, pack due diligence
- **Marketing** — plan marketing, brief de campagne, calendrier éditorial, étude de marché, deck de marque
- **Ventes** — proposition, deck de vente, pipeline & prévisions, battlecard, grille tarifaire
- **RH & Formation** — livret d'onboarding, support de formation, grille d'évaluation, fiche de poste, rapport RH

## Astuces

- Plus le brief est riche, meilleure est la première version. Précisez cible, objectif et ton.
- Itérez dans le même chat : « rends la slide 4 plus visuelle », « ajoute une section concurrents », « passe le thème en vert foncé ».
- Combinez avec la recherche web : « documente les vrais chiffres du marché avant d'écrire ».
- Demandez un ensemble cohérent : « produis maintenant la version one-pager PDF de ce deck ».
