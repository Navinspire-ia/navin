# Transcription Meeting

Comment le bureau Meeting (`#/meeting`) transforme l'audio en texte avec le provider de reconnaissance vocale configure dans Reglages -> Voix. Rien ne part vers un service de transcription maison : le bureau emprunte la meme route STT que le reste du produit.

## Etat du provider

La pastille de la barre d'outils et le pied de page sous le transcript affichent les trois valeurs qui comptent : provider, modele, et duree maximale de segment envoyee.

| Pastille | Signification | Que faire |
| --- | --- | --- |
| Verte avec un nom de provider | Transcription activee et identifiants presents | Enregistrer ou importer |
| Orange, cliquable | Desactivee, ou provider sans identifiants | Cliquer pour ouvrir Reglages -> Voix |

Enregistrer et Importer sont desactives tant que la pastille est orange. C'est volontaire : une reunion qui echoue en silence au bout de vingt minutes est pire qu'un bouton qui refuse de demarrer.

## Enregistrement live

L'enregistrement diffuse le micro par tranches, pour que le transcript se remplisse pendant l'appel au lieu d'arriver a la fin.

- La tranche dure 25 secondes, reduite automatiquement si votre provider plafonne plus bas.
- Chaque tranche est transcrite separement puis ajoutee au transcript.
- Le minuteur de la barre d'outils affiche le temps ecoule. L'arret libere le micro immediatement.
- Le conteneur navigateur est choisi selon la plateforme, dans cet ordre : `audio/webm;codecs=opus`, `audio/webm`, `audio/mp4`, `audio/ogg;codecs=opus`.

## Import d'un fichier

Un enregistrement d'une heure depasse a la fois la limite de duree et la limite d'upload de la plupart des providers. Le bureau prepare donc le fichier localement avant tout envoi :

1. Decodage du fichier dans le navigateur.
2. Passage en mono et reechantillonnage a 16 kHz, ce qu'attendent les modeles de parole et ce qui reduit la taille utile.
3. Decoupage en segments sous la limite de duree configuree.
4. Envoi des segments un par un, avec l'indicateur `Transcription 4/24`.
5. Ajout de chaque segment renvoye au transcript, dans l'ordre.

Si le decodage echoue, par exemple sur un conteneur exotique, le fichier part tel quel et le provider tranche.

## Formats et conversion

Certains providers refusent le conteneur `webm/opus` du navigateur. Pour ceux-la, l'audio est converti en WAV cote navigateur avant l'envoi. Xiaomi MiMo est l'exemple actuel. Aucun reglage a faire : la conversion suit le provider actif.

## Precision

| Levier | Effet |
| --- | --- |
| Un modele STT plus fort dans Reglages -> Voix | Le meilleur gain unitaire, surtout sur les accents et le jargon |
| Un micro proche, un locuteur par appareil | Supprime la majorite des erreurs que la diarisation ne peut pas rattraper |
| L'action **High-accuracy pass** | Relit le transcript, repare les erreurs ASR evidentes et la ponctuation, conserve le sens |
| L'action **Identify speakers** | Etiquette les tours de parole en Speaker 1, Speaker 2, ou avec les noms reellement prononces |

## Locuteurs

La transcription rend des mots, pas leur auteur. **Identify speakers**, dans les onglets `Transcript` et `Notes`, passe sur tout le transcript et le reecrit en un tour de parole par ligne :

```text
Speaker 1: On se cale sur vendredi pour la livraison.
Aymen Ghadghadi: D'accord, je prepare la recette d'ici jeudi.
```

Le comportement :

- Un long transcript est decoupe en blocs, etiquetes l'un apres l'autre. Chaque bloc recoit les etiquettes deja utilisees, donc une meme personne garde la meme etiquette de la premiere a la derniere minute.
- Un vrai nom n'est retenu que si le transcript le dit : quelqu'un se presente, se signe, ou est appele par son nom. Sinon l'etiquette reste `Speaker 1`, `Speaker 2`. La passe ne devine jamais un nom a partir du sujet ou du role.
- Les mots prononces sont conserves. La passe ajoute des etiquettes et des sauts de ligne, rien d'autre.
- Les transcripts tres longs sont etiquetes jusqu'a une limite de blocs ; au-dela, la fin reste telle quelle et le bureau vous le signale.

Renommez un locuteur dans l'onglet `Notes` et tous ses tours sont reecrits : la vue lecture, le rapport et les exports restent coherents. Le transcript reste modifiable : passez en **Edit raw text** pour corriger une etiquette a la main.

## Lecture audio d'une synthese

Le bouton **Ecouter** (quand il est propose sur le rapport) lit la synthese, sinon les notes, sinon le transcript. Il ouvre une session vocale en lecture seule et ne touche jamais au micro. Il demande un plan Pro, Ultra ou Team et un provider TTS configure ; sans cela le bouton n'apparait pas.

## Documentation liee

- [Vue d'ensemble](./README.md)
- [Demarrage rapide](./quickstart.md)
- [Depannage](./troubleshooting.md)
