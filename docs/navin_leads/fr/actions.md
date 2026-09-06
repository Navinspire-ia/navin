# Actions leads & ventes - 17 cartes

Chaque carte envoie `/leads` avec une spécification précise ; votre brief y est joint.

## Prospecter

| Action | Livre |
| --- | --- |
| Recherche d'entreprises | Liste d'entreprises dédupliquée et sourcée (search + scrape + MCP Exa/Firecrawl si dispo) : annuaires, registres, palmarès, écosystèmes. CSV + top 5 fits. |
| Recherche de personnes | Décideurs publics : pages équipe, presse, bios - personne, rôle, entreprise, URL, source, confiance. |
| Recherche d'offres d'emploi | Annonces carrières/boards avec stack, douleurs verbatim, intensité de recrutement. |
| Chasse web profonde | Corpus public (scrape + search + MCP) → CSV leads dédupliqué, validé `score_leads.py`, rapport HTML. |
| Trouver les contacts | `enrich_leads.py` si Hunter/Apollo ; sinon patterns `unverified`. Jamais de faux verified. |

## Qualifier

| Action | Livre |
| --- | --- |
| Définir l'ICP | Profil client idéal : firmographie, comité d'achat, douleurs et déclencheurs, critères d'exclusion, et 10 entreprises exemples qui correspondent. |
| Scorer & enrichir | Chaque lead scoré (fit ICP 0-100 + force de signal) avec grille explicite, champs manquants enrichis, tableau classé avec action recommandée par palier. |
| Fiche compte complète | Fiche complète : business model, taille/finances, organisation et personnes clés, actualités, indices techniques, douleurs, concurrents utilisés, et 3 angles d'approche - chaque source citée. |
| Scan signaux d'achat | Signaux levées, recrutement, direction, expansion, tech et réglementation, scorés force × fraîcheur, avec URL de preuve et angle suggéré - top 5 des comptes à contacter cette semaine. |

## Contacter

| Action | Livre |
| --- | --- |
| Séquence cold email | Séquence de 4-5 touches : accroches personnalisées par segment, corps orienté valeur, un CTA chacun, objets en A/B, timing. |
| Scripts réseaux pros | Variantes de notes de connexion (<300 caractères), séquence de 3 DM, tactique de warm-up par commentaires - personnalisés avec les signaux collectés. |
| Script d'appel & objections | Ouverture de 30 secondes, questions de découverte liées aux douleurs, narratif de valeur, tableau d'objections (reconnaître → explorer → répondre) pour les 8 objections les plus probables, script de messagerie vocale. |
| Cadence de relance | Cadence multicanale sur 3 semaines : plan jour par jour, canal et objectif par touche, critères de sortie, règles de personnalisation. |

## Pipeline

| Action | Livre |
| --- | --- |
| Export prêt pour CRM | CSV propre aux colonnes standard (entreprise, domaine, contact, rôle, pattern email, téléphone, pays, source, score, signal, prochaine action) plus le mapping d'import HubSpot/Salesforce. |
| Préparation de rendez-vous | Brief d'une page sur le compte et les participants, 3 hypothèses de douleurs avec preuves, questions de découverte, objections probables, prochaine étape idéale. |
| Revue de pipeline | Répartition par étape, deals qui traînent, forecast pondéré, taux de gain par segment, top 5 des actions à plus fort impact ce mois-ci. |
| Clients des concurrents | Liste de cibles de conquête construite sur des preuves publiques (études de cas, murs de logos, avis) : concurrent utilisé, URL de preuve, angle de bascule par compte. |
