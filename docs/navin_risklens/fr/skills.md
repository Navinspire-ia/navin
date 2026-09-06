# Skills RiskLens

## Préchargés par `/risklens`

| Skill | Rôle |
| --- | --- |
| `risklens` | Méthode complète : seuil de contexte, cadre « déjà échoué », raisons brutes, deep-dives, synthèse, rapports HTML/MD. |
| `multi-agent-orchestration` | Orchestration des sous-agents (`spawn`) - un investigateur par raison d'échec, en parallèle. |
| `task-planner` | Découpage clair des étapes et des critères de done pour la session. |

## Skills complémentaires

| Skill | Rôle |
| --- | --- |
| `customer-persona-builder` | Clarifier l'audience quand le brief est flou (souvent une source d'échec). |
| `competitor-intelligence` | Ancrer les scénarios concurrentiels dans des faits publics. |
| `deep-web-research` | Recherche multi-sources pour valider ou infirmer des hypothèses. |
| `project-board` | Transformer le plan révisé / la checklist en tâches suivables avant Code. |
| `document-templates` | Formaliser le plan révisé en deck ou memo via le module Documents. |

Les skills se chargent automatiquement avec `/risklens`. Vous pouvez en invoquer explicitement pour une passe ciblée (« utilise customer-persona-builder avant RiskLens »).
