# Passerelle LLM Navin - notes de discussion

Statut : cadrage uniquement. Aucun remplacement d'OpenRouter, aucune implementation ni modification de branche autorisee par ces notes.

## Orientation confirmee

- Conserver OpenRouter.
- Etudier une passerelle LiteLLM avec des fournisseurs directs, sans decider encore de son integration.
- L'utilisateur souhaite creer une nouvelle branche avant une eventuelle implementation.
- Continuer la discussion avant toute modification applicative.

## Conditions fournisseurs communiquees par l'utilisateur

Ces conditions sont declarees, non verifiees contractuellement.

| Fournisseur / offre | Condition annoncee | Base de calcul provisoire |
| --- | --- | --- |
| Z.AI Coding Plans | Reduction de 50 %, maximum de 10 000 plans | 50 % du prix du plan ; quotas et droits d'usage a confirmer |
| Z.AI API | Reduction de 20 % | 80 % du tarif public applicable |
| Alibaba | Reduction de 30 % sur les prix publics | 70 % du tarif public applicable |
| AWS Bedrock | 80 000 de consommation offerte | Credit temporaire ; devise, expiration et eligibilite a confirmer |
| Google AI | Aucune reduction actuellement ; possibilite evoquee de 30 % | 100 % du tarif public tant que la remise n'est pas acquise |

## Abonnements Navin annonces

- 5 dollars.
- 20 dollars.
- 69 dollars.
- 200 dollars.
- Teams : 40 dollars par utilisateur.

La periodicite, les taxes, les credits inclus et les quotas ne sont pas encore confirmes.

## Rentabilite : montants a distinguer

1. Tarif public du fournisseur.
2. Cout negocie reel, avec suivi separe des credits promotionnels.
3. Prix ou credits debites au client.
4. Autres couts : infrastructure, paiement et services inclus.

Calculer la rentabilite Bedrock avec et sans credits offerts pour eviter une tarification viable seulement pendant la promotion.

Clarification utilisateur : pour Z.AI et Alibaba, la consommation client sera valorisee aux prix publics. Les remises negociees restent cote Navin ; aucune majoration supplementaire n'est decidee. La tarification client Google et Bedrock reste a preciser.

Pour 20 dollars de consommation aux prix publics via les API eligibles :

| Fournisseur | Cout negocie | Difference brute avant autres frais |
| --- | --- | --- |
| Z.AI API | 16 dollars | 4 dollars |
| Alibaba API | 14 dollars | 6 dollars |

Ces exemples ne signifient pas qu'un abonnement Navin a 20 dollars inclut 20 dollars de credits LLM.

## Coding Plans Z.AI : capacite et distribution

Selon les informations fournies par l'utilisateur, non verifiees independamment :

| Plan | Credits sur 5 heures | Credits par semaine |
| --- | --- | --- |
| Lite | 2 000 | 10 000 |
| Pro | 12 000 | 60 000 |
| Max | 28 000 | 140 000 |

La consommation fournisseur depend des tokens d'entree, des entrees en cache et des sorties, avec des multiplicateurs propres au modele. Les reductions horaires et campagnes promotionnelles doivent etre confirmees pour le canal utilise ; l'illimite annonce pour ZCode ne doit pas etre suppose applicable a Navin.

Acheter un plan a -50 % ne garantit pas une marge de 50 % sur les abonnements Navin. Son cout est celui d'une capacite a quotas ; la rentabilite depend de son utilisation reelle.

Principe propose pour la discussion, non implemente : suivre separement le solde client en dollars aux prix publics et les quotas fournisseur sur 5 heures et par semaine. Les tokens en cache doivent etre distingues sans double comptage avec l'entree non cachee.

Un client peut conserver du credit Navin alors que le quota du plan fournisseur est epuise. La politique reste a choisir : attente du renouvellement ou bascule autorisee vers l'API payante, avec controle du cout et sans contourner les quotas par rotation de plans.

L'attribution individuelle ou la mutualisation des plans reste non decidee et depend des droits contractuels. Aucun mecanisme de distribution n'est encore accepte.

## Points ouverts avant implementation

- Que contient chaque abonnement : credits monetaires, requetes, tokens ou autre quota ?
- Quelle place donner a LiteLLM a cote d'OpenRouter ? Aucun routage ou fallback n'est encore decide.
- Les accords Z.AI Coding Plans autorisent-ils l'usage dans Navin, l'attribution aux clients et une passerelle multi-utilisateur ? Ne pas assimiler ces plans a des API mutualisables sans confirmation.
- Quels modeles, regions et usages beneficient des remises annoncees ?
- Quelles fonctions de la version et de l'edition LiteLLM retenues couvrent les budgets, tarifs commerciaux et couts fournisseurs distincts ?
- Comment isoler les donnees LiteLLM dans PostgreSQL/Supabase et proteger les credentials cote serveur ?

## Prochaine question de discussion

Quels credits ou quotas sont actuellement inclus dans les abonnements Navin a 5, 20, 69, 200 dollars et Teams a 40 dollars par utilisateur ?
