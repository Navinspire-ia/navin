# Templates et exports Meeting

Les templates decident de ce que contient une synthese. Les exports decident de la maniere dont elle quitte le bureau (`#/meeting`).

## Templates fournis

Choisissez un template a cote du titre de la reunion, avant de lancer une action de synthese. Toutes les actions de synthese lisent les instructions du template selectionne.

| Template | Ecrit pour | Accent mis sur |
| --- | --- | --- |
| Standard minutes | Reunions de projet recurrentes | Decisions, responsables, echeances, points ouverts |
| Executive brief | Les decideurs absents | Dix lignes, resultat d'abord, pas de citations |
| Sales discovery | Appels prospects | Douleur, budget, calendrier, circuit de decision, objections |
| Stand-up / sync | Points d'equipe quotidiens | Fait, a venir, blocages, par personne |
| Interview notes | Recrutement et entretiens utilisateurs | Signaux, verbatims, preuves, pas de jugement |

## Templates personnels

L'onglet `Templates` propose un editeur a deux champs : un nom et les instructions. Redigez les instructions comme un brief a un assistant meticuleux : les sections voulues, dans quel ordre, le ton, la langue de sortie, et les champs qui ne doivent jamais manquer.

```text
Nom : Comite de pilotage
Instructions : Rapport en francais. Sections : 1) Decisions avec responsable et
date, 2) Impact budget, 3) Risques avec severite, 4) Prochaines etapes. Citer le
transcript pour chaque decision. Ecrire "non aborde" plutot que de supposer.
```

Les templates personnels sont stockes localement dans le profil du navigateur, apparaissent dans le selecteur a cote des modeles fournis, et portent la mention `Perso`. Enregistrer un template le selectionne pour la reunion active.

## Ou se trouvent les boutons d'export

La rangee d'export (Markdown, HTML, PDF, DOCX) est en bas de l'onglet `Transcript` et de l'onglet `Notes`, sous le modele qui a produit le rapport. La piste d'audit locale est dans `Notes`.

## Formats d'export

| Format | Comment il est produit | Ideal pour |
| --- | --- | --- |
| Markdown | Construit par le bureau depuis le rapport, sans appel modele | Archivage, wiki, git |
| HTML | Construit par le bureau, autonome et stylise pour l'impression | Envoyer un fichier unique qui s'ouvre partout |
| PDF | Le meme HTML envoye a la boite d'impression du navigateur | Partage final, propre, en lecture seule |
| DOCX | Ecrit par l'agent a partir du contenu de la reunion | Documents a envoyer et a faire signer |
| Audit trail | Telecharge immediatement en Markdown | Conformite et revues internes |
| Ecouter | Lecture TTS, aucun fichier | Relire une synthese en faisant autre chose |

Les trois formats locaux rendent le meme document que l'onglet `Rapport`, dans cet ordre : titre, table de metadonnees, synthese, table des decisions et actions, questions ouvertes, notes, puis le transcript decoupe en tours de parole lisibles. Les sections vides sont supprimees plutot que laissees en placeholder, et les elements detectes citent le transcript mot pour mot.

Si la boite d'impression n'est pas disponible dans votre build, telechargez le HTML et imprimez-le depuis votre navigateur : le fichier embarque sa feuille de style d'impression, avec des sauts de page tenus hors des tables et des tours de parole.

## Le rapport HTML

La plupart des actions rafraichissent aussi `meetings/<slug>/meeting-report-*.html`. C'est l'artefact a partager quand vous voulez un lien plutot que cinq fichiers : il embarque la synthese, les decisions, la table d'actions, et, avec l'action **Visual recap**, une image de recapitulatif plus un diagramme du flux de decision.

## Documentation liee

- [Vue d'ensemble](./README.md)
- [Actions](./actions.md)
- [Demarrage rapide](./quickstart.md)
