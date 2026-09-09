# Vérification et amélioration des modules - 8 septembre 2026

Les cinq modules ont été corrigés et vérifiés dans le navigateur. Les réponses AO et les CV sont plus complets, les actions chargent leurs véritables skills, et les envois distinguent une demande en cours d'une confirmation du service. Les intégrations sociales et la messagerie sont implémentées. Leur activation avec les comptes de l'utilisateur reste à effectuer.

| Module | Résultat livré |
| --- | --- |
| Appels d'offres | Collecte, qualification, suivi quotidien configurable et alertes par canal. Rédaction exigence par exigence, méthode, livrables, critères de réception, risques, preuves et éléments manquants. Exports Word, PowerPoint et véritable diagramme Archify HTML/SVG. |
| Carrière | CV adapté à la mission en conservant les expériences et formations sources, lettre séparée, deux téléchargements Word. Courrier SMTP avec pièces jointes réelles, reçu et protection contre les doublons. Lecture IMAP, rattachement des réponses et notifications. Envoi automatique avec activation explicite, seuil de correspondance et limite quotidienne. |
| Montage | Skills de création d'images et vidéos, montage et traduction sélectionnés par action. Import de médias sans collision entre fichiers homonymes, timeline, aperçu et rendu MP4 vérifiés. Les longues transcriptions ne sont plus tronquées silencieusement. |
| Marketing | Connexion OAuth à Reddit, LinkedIn, Instagram, Facebook et TikTok. Choix et préparation des médias, adaptation JPEG, aperçu, approbation, planification, publication et suivi des traitements asynchrones. Les publications utilisent un identifiant retourné par le réseau pour confirmer leur succès. |
| Meeting | Skills de compte rendu, vérification, traduction et correction. Traitement des longues transcriptions par segments, contrôle des citations et limites explicites. Sauvegarde, restauration et export Word vérifiés. |

Le [schéma Archify des modules](architecture.html) montre les entrées de l'interface, les outils de l'agent et les états locaux. Le [reçu de routage](preuves/routage-skills.json) documente le chargement des skills. Leurs corps complets sont fournis au modèle, avec respect du workspace courant et des skills désactivés.

**Exemples consultables, intégralement fictifs**

- [CV adapté](exemples/cv-adapte.docx) et [lettre de motivation](exemples/lettre-motivation.docx).
- [Réponse AO complète](exemples/reponse-ao.docx), [présentation PowerPoint](exemples/soutenance-ao.pptx) et [diagramme Archify interactif](exemples/architecture-ao.html).

Le CV d'exemple provient de deux réponses réelles du modèle documentaire, rejouées après correction du validateur. Six groupes de champs sont acceptés, sans rejet ni avertissement dans ce cas. Aucun nouvel appel au modèle n'a été effectué pendant ce rejeu. Les trois documents CV/lettre/dossier passent le contrôle documentaire. [Provenance](preuves/cv-modele-validation.json).

L'exemple AO a fait l'objet de trois appels réels au modèle : deux lots et une correction bornée. Les 14 exigences sont couvertes, dont neuf enrichies par le modèle et cinq conservant la rédaction locale. Seize champs ont été réparés. Les justificatifs absents, notamment ISO 27001 dans ce dossier fictif, restent signalés : ce document n'est pas présenté comme prêt à soumettre. Archify passe ses neuf contrôles, sans erreur ni avertissement. Le diagramme représente le déroulement proposé de la prestation, avec liens aux exigences du cahier des charges. [Provenance et contrôles](preuves/ao-modele-validation.json).

**Validation effectuée**

| Contrôle | Résultat |
| --- | --- |
| Tests serveur sur les cinq modules et intégrations associées | 1 119 réussis, 354 sous-tests réussis. Un test réservé à un hôte Omarchy ignoré. Un test de collecte live exclu de cette suite isolée. |
| Tests d'interface ciblés | 189 réussis, aucun échec. |
| `verify` sur 101 fichiers du périmètre | `clean`, zéro erreur et zéro avertissement. Ruff, ESLint, JSON, contrôles produit et 15 tests OAuth réussis. |
| TypeScript et compilation Vite | Réussis. Avertissement existant de taille de certains bundles, supérieur à 500 kB. |
| Preview des cinq modules | Parcours fonctionnels réussis, aucune erreur JavaScript observée. Affichage vérifié à 375, 768, 1 024 et 1 440 pixels, sans débordement horizontal. |
| Exports et rendu | CV et lettre séparés, Word/PPT/HTML AO, Word Meeting téléchargés. Nouveau MP4 produit par le bouton de rendu, avec modification du fichier vérifiée. |
| Messagerie dans l'interface | Brouillon et pièces jointes, acceptation SMTP 250, absence de doublon, réponse IMAP liée et classement en entretien. Refus SMTP 550 visible, sans marquer la candidature envoyée. |
| OAuth et publication dans l'interface | Cinq cartes présentes. Aller-retour Reddit via le véritable serveur HTTP, cookie de liaison HttpOnly, restauration du bon écran, activation explicite du connecteur, approbation et reçu de publication. |

Les services SMTP, IMAP et sociaux ont été remplacés par des transports capturés pour ces essais. Aucun vrai mail, message ou post n'a été envoyé. Les tests des cinq API sociales couvrent aussi les médias, les traitements en attente, les erreurs, les expirations et les protections contre les doublons.

La collecte publique a été testée en réseau le 8 septembre 2026 à 11:40 UTC : trois avis BOAMP, trois TED et trois Find a Tender, tous publiés ce jour-là, avec acheteur, échéance et lien officiel. Il s'agit d'une preuve d'accès aux sources, pas d'une recherche personnalisée pour l'utilisateur. [Avis et reçus HTTP](preuves/collecte-ao-reelle.json).

Preuves détaillées : [tests serveur](preuves/tests-serveur.xml), [tests interface](preuves/tests-interface.json), [verify](preuves/verify.json), [parcours des modules](preuves/preview-parcours.json), [documents](preuves/preview-documents.json), [rendu et exports](preuves/preview-exports.json), [affichage responsive](preuves/preview-responsive.json), [messagerie](preuves/preview-messagerie.json) et [connexion sociale](preuves/preview-social.json).

**Configuration à réaliser avec les comptes réels**

1. Compléter le CV maître et le dossier société : expériences détaillées, références, équipe, certifications et pièces justificatives. Les dossiers existants contiennent peu de matière. Le générateur conserve les informations manquantes comme telles.
2. Dans Carrière, ouvrir la configuration du courrier professionnel, renseigner SMTP/IMAP et utiliser le test de connexion. Activer séparément la relève des réponses et, si souhaité, l'envoi automatique avec ses limites.
3. Renseigner les comptes et destinataires Telegram, WhatsApp, Teams ou email dans les réglages des canaux. Les alertes affichent leurs états et reçus ; seules les erreurs certaines peuvent être relancées automatiquement. Une mise en attente n'est pas une confirmation de réception.
4. Choisir la fréquence quotidienne et le fuseau dans le planning AO/Carrière, puis démarrer la boucle. Les boucles réelles de l'utilisateur n'ont pas été activées pendant cet audit.
5. Dans Marketing > Réglages, configurer les applications sociales et leur URL de retour `/api/marketing/oauth/callback`, ouvrir Navin à cette adresse publique HTTPS, puis autoriser chaque compte. Sélectionner la Page Facebook lorsque nécessaire et activer le connecteur voulu. Pour Reddit, configurer aussi la communauté par défaut. Les médias servis aux plateformes utilisent l'adresse publique renseignée dans les réglages.

Les comptes, applications, droits de publication et URL publique n'ont pas été fournis. La validation auprès des fournisseurs avec ces comptes reste donc à faire. Instagram requiert un compte professionnel. Les audiences et autorisations TikTok dépendent de l'application ; chaque publication exige les choix et confirmations de l'utilisateur. LinkedIn OAuth connecte ici un membre, l'auteur organisation reste configurable séparément. La déconnexion retire localement les jetons ; la révocation chez le fournisseur s'effectue dans ses propres réglages. [Instagram](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login/), [TikTok](https://developers.tiktok.com/docs/en/content-sharing-guidelines), [LinkedIn](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow).

**Limites conservées**

La couverture native concerne les formats implémentés : Reddit texte/liens, Instagram image/reel, TikTok vidéo/photos, LinkedIn texte/médias et Facebook texte/photos/vidéos. Elle ne constitue pas une prise en charge de tous les formats de tous les réseaux ; YouTube et Product Hunt restent manuels ou passent par un connecteur intermédiaire. Les fonctions de création d'images/vidéos utilisent les fournisseurs configurés, dont les modèles payants n'ont pas été sollicités pour les tests d'envoi.

Le mode questions-réponses de Meeting conserve sa limite de contexte ; le traitement par segments concerne les comptes rendus. L'intégration calendrier native Google/Microsoft reste incomplète, l'export ICS étant disponible. Dans Montage, conserver explicitement une piste sonore lors de l'assemblage reste nécessaire selon le parcours. Le compteur de collecte LinkedIn Career mérite encore une correction pour éviter une double collecte.

Les documents ont été contrôlés structurellement et leurs aperçus web examinés. La pagination finale dans Microsoft Word/LibreOffice n'a pas été vérifiée faute de moteur Office installé. Les exemples, leur [manifeste SHA-256](preuves/exemples-manifeste.json) et les reçus sont conservés avec ce rapport.
