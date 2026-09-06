# Calendrier Meeting

L'onglet `Calendrier` du bureau Meeting (`#/meeting`) lit un export ICS, expose le lien de visio de chaque evenement a venir, et vous alerte localement quand une reunion va commencer. Aucune connexion OAuth, aucun compte agenda lie : Navin lit un fichier que vous fournissez.

## Importer un fichier ICS

| Source | Ou exporter |
| --- | --- |
| Google Agenda | Parametres, Importer et exporter, Exporter, puis dezipper le `.ics` |
| Outlook / Microsoft 365 | Calendrier, Partager, Enregistrer dans un fichier, `.ics` |
| Calendrier Apple | Fichier, Exporter, Exporter au format `.ics` |

Cliquez sur **Importer .ics** et choisissez le fichier. Les evenements sont stockes localement et le badge de l'onglet `Calendrier` indique combien arrivent dans les 72 prochaines heures. Un nouvel import remplace le lot precedent : c'est ainsi qu'on rafraichit la semaine.

## Liens de visio

Navin cherche un lien de visio dans l'evenement, dans cet ordre : la propriete `URL`, la propriete `X-GOOGLE-CONFERENCE`, puis le premier lien de visio connu trouve dans le lieu ou la description. Les hebergeurs reconnus incluent Zoom, Google Meet, Microsoft Teams, Webex, Whereby, Jitsi, GoToMeeting, BlueJeans, Livestorm et RingCentral.

Chaque evenement avec un lien affiche un bouton **Rejoindre**. Dans l'application packagee, le lien est ouvert par la gateway dans votre navigateur systeme, car le WebView embarque bloque les nouveaux onglets. Si aucun chemin ne fonctionne, l'URL est affichee pour que vous puissiez la copier.

## Lier un evenement a une reunion

**Lier a la reunion** recopie l'evenement dans la reunion active : le titre s'il est encore vide, le lieu et la description en notes, et l'identifiant de l'evenement pour que la synthese puisse s'y referer. C'est ce qui rend un export comprehensible trois mois plus tard.

## Alertes de debut et auto-join

Toutes les 30 secondes, le bureau cherche les evenements qui commencent dans les 5 minutes et declenche une notification navigateur par evenement, une seule fois. Un clic sur la notification ouvre le lien de visio.

L'option **Ouvrir automatiquement le lien de visio a l'heure de debut** rejoint sans clic. Elle est desactivee par defaut et doit le rester si vous ne gardez pas Navin au premier plan : les navigateurs et les WebView packages bloquent l'ouverture d'onglet quand l'application n'a pas le focus, donc un auto-join sans surveillance peut ne rien faire du tout.

Navin ne rejoint jamais une reunion a votre place sous forme de bot, n'appelle jamais une salle, et n'enregistre jamais un appel auquel vous ne participez pas.

## Documentation liee

- [Vue d'ensemble](./README.md)
- [Confidentialite et audit trail](./privacy.md)
- [Depannage](./troubleshooting.md)
