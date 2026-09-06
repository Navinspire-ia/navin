# Module Meeting - Vue d'ensemble

Le module **Meeting** (sidebar -> **Reunion**, route `#/meeting`) est un bureau local qui vise au-dela des checklists "PRO meeting notes" : capture, STT haute precision via **vos** providers Navin, **templates de synthese**, speakers, calendrier ICS + alertes locales, chat-with-meeting, exports MD/DOCX/PDF, et **audit trail** local.

## Fonctionnement

1. Ouvrir **Meeting**.
2. Creer une reunion, choisir un template, puis enregistrer / importer / coller.
3. Optionnel : importer un `.ics`, lier un evenement, etiqueter les speakers.
4. Lancer une action (CR, Speakers, Ask…) - le chat s'ouvre avec `/meeting`.
5. Exporter Markdown tout de suite, ou generer DOCX/PDF via les outils document/rapport.
6. Consulter l'audit trail local.

## Organisation du bureau

Une barre d'outils, une liste de reunions, un espace a onglets : la capture, les
actions, les exports et le calendrier ne se disputent plus le meme ecran.

- **Barre d'outils** : titre de la reunion, pastille d'etat STT (orange = cliquable pour ouvrir Reglages -> Voix), **Enregistrer** avec minuteur, **Importer un audio**, bascule pleine largeur, **Nouvelle reunion**.
- **Pleine largeur** : le bouton fleches etend le bureau sur toute la fenetre et masque le chat. Lancer une action ou poser une question reaffiche le chat automatiquement, puisque la reponse y arrive.
- **Liste des reunions** (a gauche) : recherche, puis une ligne par reunion. Le chevron ouvre un **apercu** : extrait, template, nombre de speakers et suppression.
- **Onglets** : `Transcript` (capture + exports), `Rapport` (generer et lire le compte rendu sans le chat), `Notes` (notes + speakers + audit), `Actions` (question + Run with your models), `Calendrier`, `Templates`.

## Chaine audio

Le bureau lit les reglages de transcription (Reglages -> Voix) et affiche le
provider, le modele et la limite de segment au-dessus du transcript. Les boutons
Enregistrer et Importer restent desactives tant que la transcription est coupee
ou que le provider n'a pas d'identifiants.

- **Enregistrement live** : envoi par tranches (25 s, ou moins si votre limite de duree est plus basse), le transcript se remplit pendant la reunion.
- **Import** : le fichier est decode localement, converti en mono 16 kHz et redecoupe en segments sous la limite de duree, donc un enregistrement d'une heure ne se heurte plus aux erreurs `duration` / `size`. La progression s'affiche (`Transcription 4/24`).
- Les providers qui refusent le conteneur `webm/opus` du navigateur (ex. Xiaomi MiMo) recoivent du WAV, converti cote navigateur.
- **Ecouter** lit la synthese (ou les notes / le transcript) avec votre provider TTS. Le bouton ouvre une session vocale en mode lecture seule, sans micro : il demande un plan Pro, Ultra ou Team et un provider TTS configure, sinon il n'apparait pas.

## Rejoindre les reunions

Les evenements ICS importes exposent leur lien de visio (`URL`,
`X-GOOGLE-CONFERENCE`, ou le premier lien Zoom / Meet / Teams / Webex trouve
dans le lieu ou la description).

- Chaque evenement a venir affiche un lien **Rejoindre**.
- Un clic sur la notification de debut ouvre le lien de visio.
- L'option **Ouvrir automatiquement le lien de visio a l'heure de debut** rejoint sans clic. Elle reste desactivee par defaut car les navigateurs bloquent l'ouverture d'onglet quand l'app n'a pas le focus.

## Documentation liee

- [Demarrage rapide](./quickstart.md)
- [Transcription](./transcription.md)
- [Templates et exports](./exports.md)
- [Calendrier](./calendar.md)
- [Confidentialite et audit trail](./privacy.md)
- [Depannage](./troubleshooting.md)
- [Actions](./actions.md)
- [Skills](./skills.md)
