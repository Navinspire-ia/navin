# Confidentialite et audit trail Meeting

Ce qui quitte votre machine quand vous utilisez le bureau Meeting (`#/meeting`), ce qui y reste, et comment le prouver.

## Ce qui quitte la machine

| Donnee | Sort ? | Vers qui |
| --- | --- | --- |
| Tranches audio et fichiers importes | Oui, quand vous enregistrez ou importez | Uniquement le provider STT configure dans Reglages -> Voix |
| Transcript, notes, synthese | Oui, quand vous lancez une action | Uniquement le modele de chat choisi pour cette action |
| Texte de la synthese pour la lecture audio | Oui, quand vous cliquez sur Ecouter | Uniquement le provider TTS configure |
| Liste des reunions, titres, speakers, templates perso | Non | Stockes dans le repertoire de donnees local Navin |
| Evenements de calendrier et liens de visio | Non | Analyses localement depuis le fichier importe |
| Audit trail | Non | Local tant que vous ne l'exportez pas vous-meme |

Il n'existe aucune archive de reunions hebergee par Navin et aucun envoi automatique. Les connexions calendrier Google ou Microsoft optionnelles transmettent des donnees uniquement si elles sont configurees et utilisees explicitement.

## Ou sont stockees les donnees locales

La gateway stocke les fiches, templates, donnees calendrier, audit JSONL, segments audio et etats du bot dans `meetings/`, sous le repertoire de donnees local Navin. Les ecritures sont atomiques et verrouillees entre processus. Une migration versionnee et idempotente importe les anciennes donnees `localStorage`. L'export ZIP d'urgence reste lisible sans Navin.

Supprimer une reunion retire sa fiche et ses segments audio. Sur desktop, sauvegardez le repertoire de donnees Navin, pas seulement le profil WebView.

## Audit trail

Chaque action significative est ajoutee a un journal local en append-only : reunion creee et supprimee, enregistrement demarre et arrete, audio importe et transcrit, transcript complete avec sa taille, template selectionne ou cree, action envoyee a l'agent, question posee, export produit, calendrier importe, evenement lie ou rejoint, auto-join bascule, synthese lue a voix haute.

Chaque entree porte un horodatage, l'identifiant de la reunion, l'action et un detail court. L'onglet `Notes` affiche le journal de la reunion active et le telecharge en Markdown : c'est la piece a joindre a une revue de conformite.

Ce journal repond a la question que pose reellement un auditeur : qu'a-t-on capture, quand, avec quel sous-traitant, et qui a demande l'export. Ce n'est pas une frontiere de securite, puisqu'il vit dans le meme profil que les donnees qu'il decrit.

## Pratiques en environnement regule

1. Choisissez les providers STT et chat de maniere deliberee. Dans le bureau Meeting, le sous-traitant est celui que vous avez configure : votre registre des traitements doit le nommer.
2. N'enregistrez que le necessaire. Une reunion avec des notes et sans audio produit quand meme un compte rendu, les actions acceptant les notes seules.
3. Annoncez l'enregistrement aux participants. Le bot apparait comme participant mais ne fournit ni consentement juridique ni annonce sonore.
4. Exportez et archivez a la fin, puis supprimez la reunion du bureau. Le profil navigateur est un plan de travail, pas une archive.
5. Conservez l'audit trail avec l'export quand la reunion a une portee juridique ou contractuelle.

## Documentation liee

- [Vue d'ensemble](./README.md)
- [Calendrier](./calendar.md)
- [Templates et exports](./exports.md)
