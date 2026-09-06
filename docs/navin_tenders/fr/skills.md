# Skills Tenders

## Precharges par `/tenders` et le module

| Skill | Role |
| --- | --- |
| `tender-agent` | Contrat desk : collect officiel, score, GO/NO-GO, write, loop. Jamais inventer un avis. Write doit reutiliser les extraits au dossier. |
| `tender-monitor` | Veille : follow / watch. Pas de collect ni write depuis le heartbeat. |
| `rfp-writer` | Matrice, mapping d'exigences, reponses structurees RFP / RFQ / DAO **apres** `write`. |
| `proposal-writer` / `sales-proposal-writer` | Allonger une offre commerciale sur les faits du dossier. |
| `contract-reviewer` | Lecture clauses / risques. |
| `critic-reviewer` | Revue avant livraison. |
| `scrape-operator` / `scrapling` | Listing public manque par Collect. Jamais un mur login. |
| `web-extractor` | Extract structure d'une page d'avis publique. |

## Complementaires

| Element | Role |
| --- | --- |
| `docx-generator` / `pptx-generator` / `pdf-generator` | Fichier de remise **apres** le brouillon write. |
| `pdf-ocr-extractor` | CDC scanne sans couche texte. |
| Notes | Base de connaissances hors desk. Le write lit le store Tenders, pas Notes. |
| MCP LinkedIn | Recherche acheteur (session user). Jamais un avis invente depuis un post. |
| MCP Exa | Recherche publique sur hotes officiels. |

Skills et write doivent citer `source_url`. Mode d'envoi par defaut : **approval**.
