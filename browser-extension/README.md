# Navin Import

Import de missions, emplois, profils candidats, prospects et appels d'offres depuis le navigateur de l'utilisateur. Versions locales Chrome, Microsoft Edge et Firefox.

## Installation

1. Dans Navin (web ou application Tauri Linux, macOS, Windows), ouvrir Leads > Extensions ou Carrière > Extensions et télécharger le paquet du navigateur. L'application desktop ouvre la boîte native d'enregistrement.
2. Pour Chrome ou Microsoft Edge, décompresser le ZIP du navigateur choisi. Dans l'explorateur de fichiers, vérifier que le dossier contient `manifest.json`, `app.js` et `index.html`. Ouvrir `chrome://extensions` ou `edge://extensions`, activer le mode développeur et choisir « Charger l'extension non empaquetée ». Sélectionner ce dossier et valider : le sélecteur peut masquer les fichiers et faire paraître le dossier vide. Ne pas sélectionner le ZIP ou le dossier parent.
3. Pour Firefox 140 ou plus récent, ouvrir `about:debugging#/runtime/this-firefox`, puis « Charger un module complémentaire temporaire » et sélectionner directement le ZIP Firefox. Si le ZIP a déjà été extrait, sélectionner `manifest.json` dans le dossier. Ne pas utiliser l'installation depuis `about:addons` : celle-ci nécessite une signature Mozilla. Cette installation est temporaire ; aucun paquet signé n'est fourni ici.
4. Dans Navin, générer un code d'appairage. Recharger la page de la plateforme après installation ou mise à jour. Cliquer sur la bulle Navin à droite pour ouvrir le panneau dans la page, puis saisir l'adresse de Navin et le code. L'icône de la barre du navigateur ouvre aussi le panneau. Les pages protégées du navigateur utilisent une fenêtre d'import séparée.
5. Cliquer sur « Analyser cette page avec mes critères Navin ». L'extension affiche les critères Carrière actifs, les correspondances, les informations à vérifier et les doublons. Les profils détectés vont au vivier ; les missions et emplois vont aux opportunités. Vérifier les fiches présélectionnées puis confirmer leur enregistrement. Une modification de fiche ou de critères demande une nouvelle analyse.

Sur LinkedIn, sélectionner soi-même le texte avant de cliquer sur Navin. Le parcours ne visite pas d'autres pages, ne lit pas les cookies et ne parcourt pas les résultats en arrière-plan. Une absence de restriction par la plateforme ne peut pas être garantie.

Pour la prospection commerciale, choisir « Prospection commerciale (Leads) » dans le panneau. Ce choix est conservé pour les prochaines captures. Vérifier le contact et renseigner son entreprise, sélectionner les fiches et confirmer l'envoi. Elles sont enregistrées dans Leads, sans appliquer les filtres Carrière ni contacter les prospects. Revenir dans Navin actualise la liste. Revenir au choix « Selon le type de fiche détecté » rétablit les destinations détectées.

Après génération du code, « Copier l'adresse et le code » permet de coller les deux dans le champ Code d'appairage de l'extension. Vérifier l'adresse affichée avant de confirmer. Le domaine HTTPS de Navin en ligne ou le port réel du moteur desktop est conservé ; les routes de discussion et secrets de session ne sont jamais copiés. Aucune adresse ni aucun port par défaut n'est deviné. Une adresse localhost, 127.0.0.1 ou ::1 nécessite le navigateur sur le même ordinateur que Navin. Un changement d'adresse du serveur nécessite de relier à nouveau l'extension.

## Fonctionnement vérifiable

- Missions et emplois : Carrière ; profils : vivier ; prospects : Leads ; appels d'offres : Tender. Pour une consultation de mission RFP/SoW, choisir Mission et renseigner le type de besoin pour la garder dans Carrière.
- Données structurées JobPosting lorsqu'elles existent ; sinon texte sélectionné ou contenu de la fiche à corriger. Les pages sans données complètes demandent une vérification manuelle.
- Pas de transfert de cookies ni de mots de passe des plateformes. Pas de permission cookies, historique ou interception des requêtes. Un script affiche la bulle sur les pages HTTP et HTTPS autorisées ; la capture démarre uniquement au clic.
- L'accès aux sites sert à afficher la bulle et lire la page à la demande. Les fiches sont envoyées uniquement à l'hôte Navin choisi. HTTPS requis, sauf boucle locale.
- Fermer le panneau conserve le brouillon. « Relire la page » remplace la capture après confirmation. Une réouverture après changement d'adresse capture la nouvelle page. Les restrictions du navigateur peuvent empêcher l'affichage sur certaines pages.
- LinkedIn interdit notamment les extensions qui modifient l'apparence de son site. Le mode sélection manuelle n'est ni une autorisation de LinkedIn ni une garantie contre les restrictions de compte : https://www.linkedin.com/help/linkedin/answer/a1341387/prohibited-software-and-extensions
- Jeton d'analyse et d'import révocable, stocké dans le navigateur. Navin conserve uniquement son empreinte. Seuls les critères de matching sont consultables ; le jeton n'autorise pas la lecture des dossiers, les recherches externes, les candidatures ou les messages.
- L'analyse ne sauvegarde aucune fiche. Les profils sont comparés aux critères du vivier (métiers, compétences, pays et TJM d'achat maximum), les missions aux filtres d'opportunités. Les données manquantes restent à vérifier. Un score ne confirme jamais la disponibilité.
- Sur les listes de profils, seules les cartes rendues sur la page ouverte et reliées à une adresse de profil distincte sont reprises, dans la limite de 20. Aucun profil lié n'est ouvert automatiquement. Les pages dont la structure ne permet pas de séparer les profils demandent une sélection de texte ou une fiche détaillée.
- Brouillons dans le stockage de session du navigateur. Aucun service de télémétrie ou d'enrichissement. La notice incluse décrit la conservation et la suppression.
- Un import de profil ne prouve ni sa disponibilité ni son consentement. Un email publié reste non vérifié.

## Construction et validation

Depuis la racine du dépôt, après installation des dépendances de `webui` :

```sh
node browser-extension/build.mjs
webui/node_modules/.bin/tsc -p browser-extension/tsconfig.json --noEmit
.venv/bin/python -m pytest tests/test_browser_bridge.py -q
```

Les dossiers installables sont `browser-extension/dist/chrome`, `browser-extension/dist/edge` et `browser-extension/dist/firefox`. Les ZIP sont créés dans `navin/browser_extension` et embarqués dans la distribution Python lorsqu'ils ont été construits.

Les paquets ne sont pas publiés sur Chrome Web Store, Microsoft Edge Add-ons ou Mozilla Add-ons. Le processus de publication, les comptes éditeurs et la signature ne sont pas simulés.

Les builds desktop exigent les trois ZIP avant de construire le moteur Python. Le workflow de livraison les construit une fois et les fournit aux builds Linux, macOS et Windows. En local, lancer la commande de construction ci-dessus avant le build desktop. `NAVIN_BUILD_PYTHON` permet de choisir l'exécutable Python utilisé pour créer les ZIP.
