# Demarrage rapide Meeting

D'un bureau vide a un compte rendu partageable, en cinq minutes. Route `#/meeting`, commande `/meeting`.

## Prerequis

| Prerequis | Ou | Necessaire pour |
| --- | --- | --- |
| Transcription activee avec un provider configure | Reglages -> Voix | Enregistrer et Importer un audio |
| Un modele de chat | Reglages -> Modeles | Comptes rendus, syntheses, listes d'actions |
| Provider TTS et plan Pro, Ultra ou Team | Reglages -> Voix | Le bouton Ecouter (optionnel) |

La pastille d'etat dans la barre d'outils indique la situation. En vert, la transcription est prete et le provider actif s'affiche. En orange, elle ne l'est pas, et un clic ouvre Reglages -> Voix. Tant qu'elle est orange, Enregistrer et Importer restent desactives volontairement, plutot que d'echouer au milieu d'une reunion.

## Cinq etapes

1. **Creer la reunion.** Cliquez sur le bouton plus dans la barre d'outils. Renommez-la dans le champ titre : le titre devient le nom du dossier d'export (`meetings/<slug>/`).
2. **Choisir un template** a cote du titre : Standard minutes, Executive brief, Sales discovery, Stand-up / sync ou Interview notes. Le template pilote toutes les actions de synthese, choisissez-le avant de les lancer.
3. **Capturer.** Appuyez sur **Enregistrer** pour diffuser le micro, ou sur **Importer un audio** pour un fichier existant. Le transcript se remplit dans l'onglet `Transcript` pendant la reunion.
4. **Generer le rapport.** Ouvrez l'onglet `Rapport` et cliquez sur **Generer le rapport**. Un seul appel modele transforme le transcript en compte rendu pilote par le template, affiche sur place a cote des metadonnees, des decisions et actions detectees, et du transcript mis en forme. Rien ne passe par le chat.
5. **Exporter.** En bas de `Transcript` (ou `Notes`), Markdown et HTML se telechargent immediatement et **PDF** ouvre la boite d'impression. Le DOCX reste le seul format ecrit par l'agent.

L'onglet `Actions` reste pour les taches qui ont vraiment besoin de l'agent : packs DOCX, emails de suivi ecrits sur disque, passes de nettoyage haute precision, recapitulatifs visuels.

## Pleine largeur et chat

Le bouton double fleche etend le bureau sur toute la fenetre et masque la colonne de chat. Utilisez-le pendant que vous prenez des notes en visio. Des que vous lancez une action ou posez une question, le chat revient tout seul, puisque c'est la que l'agent repond.

## Ou vont les fichiers

```text
meetings/<slug>/
  transcript.md
  minutes.md
  actions.md
  follow-up.md
  chat.md
  meeting-report-<timestamp>.html
```

## Etapes suivantes

- Notez qui a parle dans l'onglet `Notes`, puis lancez **Identify speakers** pour un transcript etiquete.
- Importez votre calendrier dans l'onglet `Calendrier` pour afficher un lien de visio sur les reunions a venir.
- Redigez votre propre template dans l'onglet `Templates` si les cinq modeles fournis ne collent pas a votre format de rapport.

## Documentation liee

- [Vue d'ensemble](./README.md)
- [Transcription](./transcription.md)
- [Templates et exports](./exports.md)
- [Depannage](./troubleshooting.md)
