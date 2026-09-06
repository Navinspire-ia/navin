# Depannage Meeting

Les symptomes rencontres sur le bureau Meeting (`#/meeting`), leur cause habituelle et la correction.

## Capture

| Symptome | Cause probable | Correction |
| --- | --- | --- |
| Enregistrer et Importer sont grises | Transcription desactivee, ou provider sans identifiants | Cliquez sur la pastille orange, ou ouvrez Reglages -> Voix |
| « Permission micro refusee ou indisponible » | L'OS ou l'app n'a jamais eu l'acces micro | Accordez-le dans les reglages de confidentialite systeme, puis rouvrez le bureau |
| « Ce navigateur ne peut pas enregistrer le micro » | Surface sans `MediaRecorder` | Importez plutot un fichier enregistre ailleurs |
| L'enregistrement tourne mais le transcript reste vide | Les tranches sont refusees par le provider | Lisez le bandeau d'erreur, puis voyez les lignes Transcription ci-dessous |
| Le minuteur tourne apres la fin de la reunion | L'enregistrement n'a jamais ete arrete | Appuyez sur stop ; quitter la vue libere aussi le micro |

## Transcription

| Symptome | Cause probable | Correction |
| --- | --- | --- |
| « Segment audio plus long que la limite configuree » | Un segment depasse la limite de duree du provider | Baissez la limite dans Reglages -> Voix pour que le bureau coupe plus court, ou importez au lieu d'enregistrer |
| « Charge audio superieure a la limite d'upload » | Fichier long, ou decodage echoue donc envoi entier | Reencodez le fichier dans un format courant, puis reimportez |
| « Ce format audio n'est pas accepte par le provider » | Conteneur refuse par le provider | Convertissez en WAV ou MP3, ou choisissez un provider qui accepte `webm/opus` |
| « La transcription est desactivee » | Fonction coupee | Reglages -> Voix, activez la transcription |
| « Le provider de transcription n'a pas encore d'identifiants » | Provider selectionne mais non configure | Reglages -> Voix, ajoutez la cle ou connectez le provider |
| L'import s'arrete en cours avec un transcript partiel | Un segment a echoue et la boucle s'est arretee | Le transcript garde ce qui a reussi ; relancez l'import pour la suite |
| Les mots sont bons mais pas les locuteurs | Pas de diarisation a la capture | Lancez **Identify speakers**, puis corrigez la liste dans l'onglet `Notes` |

Les labels AssemblyAI viennent d'une diarisation acoustique native au provider. Avec les autres providers, **Identify speakers** est un fallback LLM explicite sur le texte et ne constitue pas une preuve d'identite acoustique.

## Bot de reunion et desktop

Le bot prend en charge les parcours invites Zoom, Google Meet et Microsoft Teams. Un hote peut devoir l'admettre. Les reunions reservees aux comptes ne sont pas accessibles en invite. La gateway persiste le statut et les segments audio. Apres un redemarrage, une session navigateur auparavant active est indiquee comme interrompue, jamais faussement active. Relancez le bot pour reconnecter.

Les apps Windows, macOS et Linux packagees ont besoin d'un Chromium utilisable et des permissions micro du systeme. Les liens d'invitation ne sont pas ecrits dans les fichiers d'etat du bot. Ne mettez jamais de mot de passe de compte ni de cle provider dans une URL de reunion.

## Actions et exports

| Symptome | Cause probable | Correction |
| --- | --- | --- |
| « Ajoutez un transcript ou des notes avant de lancer une action » | Reunion sans transcript ni notes | Capturez, importez ou saisissez des notes ; les actions n'inventent rien |
| Une action semble sans effet | Le mode pleine largeur masquait le chat | Le bureau quitte la pleine largeur tout seul ; regardez le champ de chat a droite |
| Pas de bouton Ecouter | Aucun provider TTS, ou plan inferieur a Pro | Reglages -> Voix, plus un plan Pro, Ultra ou Team |
| « La sortie vocale demande un plan Pro, Ultra ou Team » | TTS refuse par le plan | Voir ci-dessus |
| L'export Markdown est presque vide | Tout a ete capture dans une autre reunion | Verifiez la reunion selectionnee dans la liste de gauche |

## Calendrier

| Symptome | Cause probable | Correction |
| --- | --- | --- |
| « Aucun evenement trouve dans ce fichier ICS » | Export vide, ou fichier qui n'est pas un ICS | Reexportez depuis l'agenda, dezippez si besoin |
| Aucun evenement apres l'import | Ils commencent tous au-dela de 72 heures | Normal : la liste n'affiche que les 72 prochaines heures |
| Pas de bouton Rejoindre sur un evenement | Aucun lien de visio dans l'ICS | Ajoutez le lien dans le lieu ou la description, puis reimportez |
| Aucune notification de debut | Notifications refusees pour l'application | Autorisez les notifications, puis reimportez pour reannoncer l'evenement |
| L'auto-join ne fait rien | Ouverture d'onglet bloquee car l'app n'avait pas le focus | Gardez Navin au premier plan, ou cliquez Rejoindre vous-meme |
| « Impossible d'ouvrir le lien » | Ni le navigateur systeme ni un onglet n'ont pu s'ouvrir | Copiez l'URL affichee dans le message |

## Documentation liee

- [Vue d'ensemble](./README.md)
- [Transcription](./transcription.md)
- [Calendrier](./calendar.md)
