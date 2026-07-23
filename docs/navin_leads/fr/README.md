# Module Leads & Ventes — Vue d'ensemble

Le module **Leads** (barre latérale → **Leads**, route `#/leads`) est un bureau de prospection et de vente B2B d'élite. L'agent chasse les entreprises et les décideurs depuis les sources web ouvertes, détecte les signaux d'achat, qualifie et enrichit chaque lead, construit l'outreach, et formate le tout pour votre CRM — avec une URL source sur chaque donnée.

## Fonctionnement

1. Ouvrez **Leads** dans la barre latérale.
2. (Optionnel) Tapez un **brief** en haut : votre ICP, produit, territoire, volume cible. Il est joint à chaque action.
3. Choisissez une carte d'action dans l'un des quatre groupes — **Prospecter**, **Qualifier**, **Contacter**, **Pipeline** (voir [Actions](./actions.md)).
4. Le chat s'ouvre et `/leads` part automatiquement avec la spécification de l'action et votre brief.
5. L'agent recherche, construit le livrable (CSV ou tableau enregistré dans l'espace de travail), et termine par les meilleurs leads et les prochaines actions suggérées.

Usage direct dans n'importe quel chat :

```
/leads trouve 30 SaaS RH en France, 50-200 salariés, avec leurs DRH
/leads scanne les signaux d'achat sur la liste de comptes ci-jointe
```

## La commande `/leads`

| | |
| --- | --- |
| Commande | `/leads [icp\|entreprise\|brief]` |
| Cycle de vie | Workflow agent (tour d'agent complet) |
| Skills préchargés | `lead-prospector`, `buying-signals`, `lead-generation`, `lead-qualification`, `account-research`, `entity-research`, `outreach-sequencer`, `cold-email-writer`, `customer-persona-builder`, `pipeline-analyst` |
| Sortie | Listes de leads sourcées (CSV/markdown), tableaux de signaux, séquences d'outreach, fiches comptes — dans l'espace de travail |

## Ce que l'agent sait chercher

| Cible | Sources utilisées |
| --- | --- |
| Entreprises | Annuaires, registres officiels (OpenCorporates, Pappers, Companies House…), palmarès, écosystèmes des concurrents, listes d'exposants |
| Personnes | Profils publics, pages équipe/direction, citations presse, bios de conférences, signatures d'articles, brevets, organisations GitHub |
| Offres d'emploi | Pages carrières et job boards — les annonces révèlent stack, projets et douleurs mot pour mot |
| Signaux | Levées de fonds, vagues de recrutement, changements de direction, expansions, changements techniques, échéances réglementaires |
| Contexte de contact | Emails publiés et patterns, standards officiels, profils sociaux — avec niveaux de confiance |

## Règles de données (ce qui rend le résultat fiable)

- **Chaque donnée est sourcée** : URL + date de collecte par champ.
- **Rien d'inventé** : les champs non vérifiés sont marqués `unverified` ; les patterns d'emails portent un niveau de confiance, jamais présentés comme des adresses vérifiées.
- **Sources publiques uniquement** : pas de scraping derrière login, robots et CGU respectés.
- **Dédupliqué** : même domaine = même entreprise ; la qualité prime sur le volume.

## Prospection continue

Combinez avec les capacités d'autonomie de Navin :

- `/goal surveille les levées de fonds fintech FR et construis une liste de leads chaque semaine` — un objectif de fond.
- Tâches cron pour des scans de signaux et revues de pipeline planifiés.
- Le skill `crm-update-agent` pour pousser les résultats dans HubSpot/Salesforce quand c'est configuré.

## Astuces

- Donnez-lui vos meilleurs clients : « voici nos 5 meilleurs clients, trouve 50 lookalikes ».
- Enchaînez les groupes dans un même chat : ICP → entreprises → personnes → signaux → séquence.
- Demandez l'export CRM en dernier — il consolide tout ce qui a été collecté dans la session.
