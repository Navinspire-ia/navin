# Skills documentaires

Skills préchargés par `/studio` et compléments disponibles pour l'agent.

## Préchargés par `/studio`

| Skill | Rôle |
| --- | --- |
| `pptx-generator` | Construit des fichiers PowerPoint avec python-pptx : mises en page, thèmes, graphiques, notes. |
| `docx-generator` | Construit des documents Word avec python-docx : styles, titres, tableaux, en-têtes/pieds. |
| `pdf-generator` | Produit des rapports et one-pagers PDF à la typographie cohérente. |
| `spreadsheet-analyst` | Construit et analyse des classeurs Excel avec openpyxl : formules, mise en forme conditionnelle, tableaux de bord. |
| `presentation-designer` | Principes de design de slides : arc narratif, hiérarchie visuelle, une idée par slide. |
| `professional-writer` | Écriture professionnelle : clarté, ton, structure. |
| `document-templates` | 8 thèmes visuels intégrés (palettes exactes, polices, règles de mise en page) + adaptation de fichiers templates fournis par l'utilisateur. |
| `archify` | Diagrammes d'architecture / séquence / workflow par défaut (HTML + export SVG sur les slides). |

## Skills complémentaires

| Skill | Rôle |
| --- | --- |
| `report-generator` | Rapports structurés récurrents. |
| `template-manager` | Réutilisation et adaptation de modèles de documents. |
| `proposal-writer` / `sales-proposal-writer` / `rfp-writer` | Rédaction spécialisée de propositions et d'appels d'offres. |
| `case-study-writer` | Études de cas clients. |
| `technical-writer` | Style de documentation technique. |
| `proofreader` / `style-editor` | Passe qualité sur le texte final. |
| `translation-localization` | Production de documents multilingues. |
| `pdf-ocr-extractor` | Extraction de contenu de PDF existants à réutiliser. |
| `invoice-reader` / `contract-extractor` | Extraction structurée de documents métier. |
| `contract-reviewer` | Revue de clauses en première passe. Préchargé seulement sur les 25 cartes juridiques `/studio`, jamais sur un pitch ou un rapport. |
| `fact-checker` | Vérification des affirmations avant qu'elles n'entrent dans un livrable. |
| `image-generation` | Illustrations et images de couverture pour decks et rapports. |

Les skills se chargent automatiquement avec `/studio` ; vous pouvez aussi en invoquer explicitement (« utilise le skill template-manager pour adapter le rapport du mois dernier »).
