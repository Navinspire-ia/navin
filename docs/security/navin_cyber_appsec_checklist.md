# Navin Cyber - Catalogue de vérification Application Security

**Version :** 1.0.0 - **Date :** 2026-11-14 - **Contrôles :** 302 - **Catégories :** 24

## Conditions d'exécution

- Détecter automatiquement types de projets, langages, environnements et surfaces.
- Appliquer les contrôles `all` dans chaque audit; appliquer les autres lorsqu'une surface correspondante existe.
- Statuts autorisés : `not_tested`, `passed`, `failed`, `inconclusive`, `blocked`, `not_applicable`.
- Un contrôle `passed` exige une preuve d'exécution horodatée, le périmètre et la version des outils.
- `not_applicable` exige une justification. `blocked` et `not_tested` ne sont pas des réussites.
- Pas de scan actif sur production sans mandat et garde-fous; jamais de tests destructifs, de modification directe de base, ni de vraies données clients pour les tests d'isolement.

## 01. Découverte et cartographie - 13 contrôles

**Applicabilité :** `all` - **Verification :** `read_only` - **Moteurs possibles :** inventaire statique, parsers, SBOM, manifests

- [ ] **NAV-CYBER-DISC-001** - Recenser tous les dépôts, sous-modules, branches et monorepos
- [ ] **NAV-CYBER-DISC-002** - Détecter chaque langage, framework, version et gestionnaire de dépendances
- [ ] **NAV-CYBER-DISC-003** - Identifier les applications web, API, mobiles, desktop, CLI, bibliothèques et services
- [ ] **NAV-CYBER-DISC-004** - Inventorier les points d'entrée HTTP, RPC, WebSocket, GraphQL, workers et tâches planifiées
- [ ] **NAV-CYBER-DISC-005** - Recenser les domaines, sous-domaines, URL, environnements et points de déploiement autorisés
- [ ] **NAV-CYBER-DISC-006** - Découvrir les bases, schémas, files, caches, stockages objets et systèmes tiers
- [ ] **NAV-CYBER-DISC-007** - Cartographier les flux de données, frontières de confiance et échanges interservices
- [ ] **NAV-CYBER-DISC-008** - Identifier les rôles humains, de service, administrateurs et leurs parcours
- [ ] **NAV-CYBER-DISC-009** - Localiser les fichiers de configuration, secrets, scripts, migrations et manifestes
- [ ] **NAV-CYBER-DISC-010** - Associer artefacts compilés et versions déployées aux commits et environnements
- [ ] **NAV-CYBER-DISC-011** - Distinguer code applicatif, code généré, exemples, tests et dépendances vendorisées
- [ ] **NAV-CYBER-DISC-012** - Établir pour chaque contrôle le périmètre autorisé, les exclusions et les preuves disponibles
- [ ] **NAV-CYBER-DISC-013** - Détecter les surfaces non documentées et composants orphelins

## 02. Architecture et conception sécurisée - 12 contrôles

**Applicabilité :** `all` - **Verification :** `hybrid` - **Moteurs possibles :** graphe architecture, revue guidee, threat modeling

- [ ] **NAV-CYBER-ARCH-001** - Reconstituer l'architecture logique et les dépendances critiques
- [ ] **NAV-CYBER-ARCH-002** - Identifier frontières de confiance entre clients, serveurs, données et services tiers
- [ ] **NAV-CYBER-ARCH-003** - Construire des scénarios de menace par fonctionnalité sensible et par rôle
- [ ] **NAV-CYBER-ARCH-004** - Vérifier la séparation frontend/backend et l'absence de confiance dans le client
- [ ] **NAV-CYBER-ARCH-005** - Vérifier les principes de moindre privilège et de refus par défaut
- [ ] **NAV-CYBER-ARCH-006** - Vérifier la segmentation des plans de contrôle, de données et d'administration
- [ ] **NAV-CYBER-ARCH-007** - Contrôler la séparation des tenants et des environnements de développement/production
- [ ] **NAV-CYBER-ARCH-008** - Examiner les chemins de contournement des contrôles de sécurité
- [ ] **NAV-CYBER-ARCH-009** - Vérifier le modèle de confiance des services tiers et webhooks
- [ ] **NAV-CYBER-ARCH-010** - Identifier les points uniques de défaillance ayant un impact de sécurité
- [ ] **NAV-CYBER-ARCH-011** - Vérifier que les opérations sensibles sont journalisées et approuvées selon le risque
- [ ] **NAV-CYBER-ARCH-012** - Vérifier la traçabilité des exigences de sécurité vers des tests exécutables

## 03. Analyse statique du code et défauts techniques - 18 contrôles

**Applicabilité :** `source` - **Verification :** `read_only` - **Moteurs possibles :** Semgrep/CodeQL, linters sécurité, analyse de flux

- [ ] **NAV-CYBER-SAST-001** - Détecter injections SQL, NoSQL et requêtes construites par concaténation
- [ ] **NAV-CYBER-SAST-002** - Détecter injections de commandes système et paramètres shell non maîtrisés
- [ ] **NAV-CYBER-SAST-003** - Détecter injections LDAP, XPath, XQuery et moteurs de recherche
- [ ] **NAV-CYBER-SAST-004** - Détecter injections de templates, expressions et interpréteurs
- [ ] **NAV-CYBER-SAST-005** - Détecter XSS stockées, réfléchies et DOM liées aux flux de données
- [ ] **NAV-CYBER-SAST-006** - Détecter désérialisation non sûre et chargement de types arbitraires
- [ ] **NAV-CYBER-SAST-007** - Détecter traversée de chemins, lecture/écriture arbitraire et inclusion de fichiers
- [ ] **NAV-CYBER-SAST-008** - Détecter SSRF et accès non maîtrisé aux URL, notamment aux métadonnées cloud
- [ ] **NAV-CYBER-SAST-009** - Détecter XXE et parsers XML non sécurisés
- [ ] **NAV-CYBER-SAST-010** - Détecter exécution de code dynamique, eval et chargement de modules non maîtrisé
- [ ] **NAV-CYBER-SAST-011** - Détecter prototype pollution et manipulation dangereuse d'objets dynamiques
- [ ] **NAV-CYBER-SAST-012** - Détecter redirections ouvertes et validation insuffisante des destinations
- [ ] **NAV-CYBER-SAST-013** - Détecter ReDoS et expressions régulières à complexité dangereuse
- [ ] **NAV-CYBER-SAST-014** - Détecter débordements mémoire, use-after-free, integer overflow et accès non bornés si langage concerné
- [ ] **NAV-CYBER-SAST-015** - Détecter chemins de concurrence, data races, TOCTOU et défauts de synchronisation
- [ ] **NAV-CYBER-SAST-016** - Détecter manipulations de formats d'archives, Zip Slip et bombes de décompression
- [ ] **NAV-CYBER-SAST-017** - Détecter désactivation des contrôles de sécurité dans les branches de code
- [ ] **NAV-CYBER-SAST-018** - Vérifier validation des entrées, encodage contextuel des sorties et typage des données

## 04. Authentification et gestion des sessions - 14 contrôles

**Applicabilité :** `auth` - **Verification :** `hybrid` - **Moteurs possibles :** tests de comptes et sessions isolés, revue config

- [ ] **NAV-CYBER-AUTH-001** - Contrôler création de compte, validation d'email et invitation
- [ ] **NAV-CYBER-AUTH-002** - Contrôler politique de mots de passe et stockage par hachage adapté
- [ ] **NAV-CYBER-AUTH-003** - Contrôler récupération de compte, réinitialisation et changement de mot de passe
- [ ] **NAV-CYBER-AUTH-004** - Vérifier activation, révocation et contournement éventuel de la MFA
- [ ] **NAV-CYBER-AUTH-005** - Vérifier limites de tentatives, anti-énumération et protection contre credential stuffing
- [ ] **NAV-CYBER-AUTH-006** - Vérifier durée de vie, rotation et révocation des sessions et refresh tokens
- [ ] **NAV-CYBER-AUTH-007** - Contrôler cookies Secure, HttpOnly, SameSite et fixation de session
- [ ] **NAV-CYBER-AUTH-008** - Valider JWT: algorithmes autorisés, signatures, audiences, issuers, expiration et clés
- [ ] **NAV-CYBER-AUTH-009** - Contrôler OAuth2/OIDC: redirect URI, PKCE, state, nonce et échange de code
- [ ] **NAV-CYBER-AUTH-010** - Vérifier le lien entre identité externe et compte local (account linking)
- [ ] **NAV-CYBER-AUTH-011** - Contrôler déconnexion, invalidation de session et changement de privilèges
- [ ] **NAV-CYBER-AUTH-012** - Vérifier authentification des services, workloads et tâches automatisées
- [ ] **NAV-CYBER-AUTH-013** - Vérifier absence d'identifiants par défaut, comptes de test et chemins de contournement
- [ ] **NAV-CYBER-AUTH-014** - Contrôler authentification et validation des signatures des callbacks/webhooks

## 05. Autorisations et isolation des accès - 14 contrôles

**Applicabilité :** `auth` - **Verification :** `hybrid` - **Moteurs possibles :** matrice rôles-ressources, tests multi-comptes isolés

- [ ] **NAV-CYBER-AUTHZ-001** - Tester BOLA/IDOR sur chaque objet accessible par identifiant
- [ ] **NAV-CYBER-AUTHZ-002** - Tester contrôle d'autorisation au niveau de chaque fonction et endpoint
- [ ] **NAV-CYBER-AUTHZ-003** - Tester autorisation par propriété/champ (lecture et écriture)
- [ ] **NAV-CYBER-AUTHZ-004** - Tester élévation horizontale entre utilisateurs de même niveau
- [ ] **NAV-CYBER-AUTHZ-005** - Tester élévation verticale vers fonctions opérateur, support et administrateur
- [ ] **NAV-CYBER-AUTHZ-006** - Tester isolation entre tenants, organisations, projets, entités et environnements
- [ ] **NAV-CYBER-AUTHZ-007** - Vérifier les contrôles d'accès côté serveur sur chaque opération sensible
- [ ] **NAV-CYBER-AUTHZ-008** - Tester accès non autorisé via exports, recherche, listes et fonctionnalités batch
- [ ] **NAV-CYBER-AUTHZ-009** - Tester mass assignment et champs protégés acceptés à la création/modification
- [ ] **NAV-CYBER-AUTHZ-010** - Contrôler l'héritage, la propagation et la révocation des rôles
- [ ] **NAV-CYBER-AUTHZ-011** - Contrôler délégation, impersonation et accès support avec audit et expiration
- [ ] **NAV-CYBER-AUTHZ-012** - Tester accès aux ressources liées, fichiers, commentaires et métadonnées
- [ ] **NAV-CYBER-AUTHZ-013** - Vérifier restrictions des clés API, scopes OAuth et permissions des comptes techniques
- [ ] **NAV-CYBER-AUTHZ-014** - Vérifier qu'un cache ou index de recherche ne contourne pas les règles d'accès

## 06. Logique métier, états et transactions - 12 contrôles

**Applicabilité :** `all` - **Verification :** `hybrid` - **Moteurs possibles :** tests métiers, scénarios de transitions et concurrence contrôles

- [ ] **NAV-CYBER-BUS-001** - Modéliser les états autorisés des processus métiers sensibles
- [ ] **NAV-CYBER-BUS-002** - Tester sauts d'étapes, validation prématurée et bypass d'approbation
- [ ] **NAV-CYBER-BUS-003** - Tester accès ou modification après clôture, annulation ou expiration
- [ ] **NAV-CYBER-BUS-004** - Tester doubles soumissions, replays et idempotence des opérations
- [ ] **NAV-CYBER-BUS-005** - Tester concurrence, courses critiques et double traitement
- [ ] **NAV-CYBER-BUS-006** - Contrôler montants, unités, soldes, arrondis, bornes et valeurs négatives
- [ ] **NAV-CYBER-BUS-007** - Vérifier unicité, intégrité et cohérence des références métier
- [ ] **NAV-CYBER-BUS-008** - Tester séparation des tâches, double validation et privilèges incompatibles
- [ ] **NAV-CYBER-BUS-009** - Tester falsification de prix, quotas, limites et paramètres métier venant du client
- [ ] **NAV-CYBER-BUS-010** - Tester incohérence entre frontend, API, files et traitements différés
- [ ] **NAV-CYBER-BUS-011** - Vérifier protections contre abus de fonctionnalités sensibles et automatisation massive
- [ ] **NAV-CYBER-BUS-012** - Tester l'échec partiel des workflows et la restauration d'un état cohérent

## 07. Secrets, identifiants et exposition - 11 contrôles

**Applicabilité :** `source` - **Verification :** `read_only` - **Moteurs possibles :** Gitleaks/TruffleHog, gestion secrets

- [ ] **NAV-CYBER-SECRET-001** - Scanner secrets dans les fichiers source et de configuration
- [ ] **NAV-CYBER-SECRET-002** - Scanner secrets dans tout l'historique Git accessible et tags autorisés
- [ ] **NAV-CYBER-SECRET-003** - Scanner .env, fichiers de tests, exemples, notebooks et scripts
- [ ] **NAV-CYBER-SECRET-004** - Scanner secrets dans images, binaires, bundles JS et ressources embarquées
- [ ] **NAV-CYBER-SECRET-005** - Vérifier absence de secrets dans logs, traces, messages d'erreur et artefacts CI
- [ ] **NAV-CYBER-SECRET-006** - Identifier clés réutilisées entre environnements et scopes trop étendus
- [ ] **NAV-CYBER-SECRET-007** - Vérifier politique de rotation, révocation et durée de vie des secrets
- [ ] **NAV-CYBER-SECRET-008** - Vérifier usage de gestionnaires de secrets et absence de clés dans le frontend
- [ ] **NAV-CYBER-SECRET-009** - Vérifier permissions d'accès aux secrets par service et environnement
- [ ] **NAV-CYBER-SECRET-010** - Détecter certificats et clés privées divulgués, expirés ou mal protégés
- [ ] **NAV-CYBER-SECRET-011** - Détecter données de connexion de production dans fixtures et sauvegardes

## 08. Cryptographie et protection des échanges - 11 contrôles

**Applicabilité :** `all` - **Verification :** `hybrid` - **Moteurs possibles :** analyse statique, revue bibliothèques crypto et configs TLS

- [ ] **NAV-CYBER-CRYP-001** - Vérifier TLS et certificats sur les flux applicatifs concernés
- [ ] **NAV-CYBER-CRYP-002** - Rechercher algorithmes obsolètes et paramètres cryptographiques faibles
- [ ] **NAV-CYBER-CRYP-003** - Vérifier hachage des mots de passe avec sel et coût adaptés
- [ ] **NAV-CYBER-CRYP-004** - Vérifier usage d'aléa cryptographique approprié pour jetons et clés
- [ ] **NAV-CYBER-CRYP-005** - Vérifier chiffrement authentifié et gestion correcte nonce/IV
- [ ] **NAV-CYBER-CRYP-006** - Vérifier signatures, validation et intégrité des objets critiques
- [ ] **NAV-CYBER-CRYP-007** - Vérifier dérivation, stockage, rotation et séparation des clés cryptographiques
- [ ] **NAV-CYBER-CRYP-008** - Détecter cryptographie maison ou contournement de validation TLS
- [ ] **NAV-CYBER-CRYP-009** - Vérifier chiffrement des sauvegardes et données sensibles au repos
- [ ] **NAV-CYBER-CRYP-010** - Vérifier non-divulgation de clés dans frontend, mobile, desktop et logs
- [ ] **NAV-CYBER-CRYP-011** - Vérifier résistance aux attaques de rejeu et anti-replay lorsque nécessaire

## 09. API REST, GraphQL, gRPC et communications - 14 contrôles

**Applicabilité :** `api` - **Verification :** `hybrid` - **Moteurs possibles :** schémas OpenAPI, tests API en préproduction

- [ ] **NAV-CYBER-API-001** - Inventorier endpoints et méthodes à partir du code, schémas et runtime
- [ ] **NAV-CYBER-API-002** - Comparer routes exposées aux spécifications OpenAPI/GraphQL/protobuf
- [ ] **NAV-CYBER-API-003** - Tester authentification et autorisation de chaque méthode ou resolver
- [ ] **NAV-CYBER-API-004** - Tester BOLA, BFLA, BOPLA et mass assignment des requêtes
- [ ] **NAV-CYBER-API-005** - Contrôler validation stricte des paramètres, types, tailles et formats
- [ ] **NAV-CYBER-API-006** - Contrôler pagination, limites de ressources, complexité GraphQL et profondeur
- [ ] **NAV-CYBER-API-007** - Contrôler rate limits, quotas et protections contre abus métier
- [ ] **NAV-CYBER-API-008** - Vérifier CORS, origines autorisées et requêtes cross-origin authentifiées
- [ ] **NAV-CYBER-API-009** - Vérifier gestion des erreurs sans divulgation d'informations internes
- [ ] **NAV-CYBER-API-010** - Vérifier sécurité des subscriptions, WebSocket et canaux temps réel
- [ ] **NAV-CYBER-API-011** - Vérifier signatures, idempotence et protection replay des webhooks
- [ ] **NAV-CYBER-API-012** - Vérifier consommation sûre des API tierces, schémas et erreurs
- [ ] **NAV-CYBER-API-013** - Vérifier impossibilité d'accès aux API internes/admin depuis canaux non prévus
- [ ] **NAV-CYBER-API-014** - Vérifier protection SSRF des endpoints prenant des URLs en entrée

## 10. Applications web et tests dynamiques - 14 contrôles

**Applicabilité :** `web` - **Verification :** `staging_active` - **Moteurs possibles :** OWASP ZAP baseline; DAST actif seulement en préproduction autorisée

- [ ] **NAV-CYBER-WEB-001** - Explorer les routes et ressources publiques avec un crawl borné
- [ ] **NAV-CYBER-WEB-002** - Contrôler TLS, HSTS et redirections HTTP vers HTTPS
- [ ] **NAV-CYBER-WEB-003** - Contrôler en-têtes CSP, X-Content-Type-Options et politique de framing
- [ ] **NAV-CYBER-WEB-004** - Contrôler cookies, sessions et stockage navigateur sensibles
- [ ] **NAV-CYBER-WEB-005** - Tester XSS réfléchies, stockées et DOM avec charges non destructrices
- [ ] **NAV-CYBER-WEB-006** - Tester CSRF sur actions qui modifient l'état
- [ ] **NAV-CYBER-WEB-007** - Tester injections et path traversal avec corpus sûr et limites strictes
- [ ] **NAV-CYBER-WEB-008** - Tester upload de fichiers: types, taille, stockage, isolation et inspection
- [ ] **NAV-CYBER-WEB-009** - Tester contrôle des téléchargements et accès aux fichiers privés
- [ ] **NAV-CYBER-WEB-010** - Tester sécurité CORS, postMessage et intégrations iframe
- [ ] **NAV-CYBER-WEB-011** - Vérifier protections clickjacking et open redirect
- [ ] **NAV-CYBER-WEB-012** - Vérifier cache/CDN: absence de réponse privée partagée
- [ ] **NAV-CYBER-WEB-013** - Vérifier source maps, mode debug et ressources internes exposées
- [ ] **NAV-CYBER-WEB-014** - Vérifier séparation des routes d'administration et endpoints techniques

## 11. Bases SQL/NoSQL, RLS et sécurité des données - 18 contrôles

**Applicabilité :** `db` - **Verification :** `hybrid` - **Moteurs possibles :** introspection read-only, pgTAP / tests rôles en base ephemere

- [ ] **NAV-CYBER-DB-001** - Inventorier bases, schémas, tables, collections, vues, fonctions et triggers
- [ ] **NAV-CYBER-DB-002** - Inventorier rôles, utilisateurs, GRANT/REVOKE et privilèges par objet
- [ ] **NAV-CYBER-DB-003** - Vérifier accès par moindre privilège et comptes séparés lecture/écriture/admin
- [ ] **NAV-CYBER-DB-004** - Tester injection des requêtes générées par ORM, SQL brut et procédures
- [ ] **NAV-CYBER-DB-005** - Détecter bases, consoles, ports et endpoints de données exposés
- [ ] **NAV-CYBER-DB-006** - Vérifier chiffrement des connexions et règles de connexion depuis l'application
- [ ] **NAV-CYBER-DB-007** - Vérifier activation de RLS sur tables exposées lorsqu'applicable
- [ ] **NAV-CYBER-DB-008** - Tester RLS SELECT en utilisateur normal, anonyme et utilisateurs de tenants différents
- [ ] **NAV-CYBER-DB-009** - Tester RLS INSERT et WITH CHECK pour empêcher création dans un autre tenant
- [ ] **NAV-CYBER-DB-010** - Tester RLS UPDATE avec USING et WITH CHECK, y compris changement de tenant_id
- [ ] **NAV-CYBER-DB-011** - Tester RLS DELETE et permissions de suppression des rôles ordinaires
- [ ] **NAV-CYBER-DB-012** - Vérifier politiques permissive/restrictive, combinaisons OR/AND et deny par défaut
- [ ] **NAV-CYBER-DB-013** - Vérifier contournements RLS via propriétaire, superuser et BYPASSRLS
- [ ] **NAV-CYBER-DB-014** - Contrôler fonctions SECURITY DEFINER, search_path et droits EXECUTE
- [ ] **NAV-CYBER-DB-015** - Contrôler vues security_invoker et droits des vues matérialisées/exposées
- [ ] **NAV-CYBER-DB-016** - Tester routes RPC/ORM/jobs utilisant service_role ou connexion privilégiée
- [ ] **NAV-CYBER-DB-017** - Vérifier migrations SQL, déclencheurs, contraintes et défauts de privilèges
- [ ] **NAV-CYBER-DB-018** - Vérifier audits de lecture/écriture sensibles et protections des dumps/backups

## 12. Protection des données et vie privée - 10 contrôles

**Applicabilité :** `data` - **Verification :** `hybrid` - **Moteurs possibles :** classification, inspection des flux autorisés

- [ ] **NAV-CYBER-DATA-001** - Identifier données sensibles, personnelles, financières et secrets métier
- [ ] **NAV-CYBER-DATA-002** - Cartographier parcours, réplications, exports et stockage des données
- [ ] **NAV-CYBER-DATA-003** - Contrôler minimisation des données renvoyées par API et UI
- [ ] **NAV-CYBER-DATA-004** - Contrôler anonymisation et masquage des jeux de test et de démonstration
- [ ] **NAV-CYBER-DATA-005** - Vérifier politiques de rétention, purge et suppression autorisée
- [ ] **NAV-CYBER-DATA-006** - Contrôler partages de fichiers, liens signés, expiration et autorisations
- [ ] **NAV-CYBER-DATA-007** - Vérifier segmentation des stockages et isolation par tenant
- [ ] **NAV-CYBER-DATA-008** - Vérifier protections contre exfiltration via logs, analytics et télémétrie
- [ ] **NAV-CYBER-DATA-009** - Vérifier recherche/index/vector store sans accès transversal non autorisé
- [ ] **NAV-CYBER-DATA-010** - Tester données personnelles présentes dans caches, exports et artefacts

## 13. Dépendances et chaîne d'approvisionnement - 13 contrôles

**Applicabilité :** `source` - **Verification :** `read_only` - **Moteurs possibles :** OSV-Scanner, Grype, Trivy, Syft/SBOM

- [ ] **NAV-CYBER-SCA-001** - Recenser dépendances directes, transitives et dépendances de build
- [ ] **NAV-CYBER-SCA-002** - Produire un SBOM avec versions et provenance quand disponible
- [ ] **NAV-CYBER-SCA-003** - Faire la correspondance avec CVE, avis fournisseurs et bases de vulnérabilités
- [ ] **NAV-CYBER-SCA-004** - Croiser vulnérabilités exploitables connues CISA KEV, EPSS et contexte du projet
- [ ] **NAV-CYBER-SCA-005** - Identifier packages abandonnés ou non maintenus et versions obsolètes
- [ ] **NAV-CYBER-SCA-006** - Identifier typosquatting, dependency confusion et paquets non attendus
- [ ] **NAV-CYBER-SCA-007** - Vérifier intégrité et cohérence des lockfiles et résolutions de versions
- [ ] **NAV-CYBER-SCA-008** - Vérifier absence de dépendances flottantes sur chemins critiques
- [ ] **NAV-CYBER-SCA-009** - Vérifier scripts d'installation et hooks potentiellement dangereux
- [ ] **NAV-CYBER-SCA-010** - Vérifier provenance des packages, signatures et attestations disponibles
- [ ] **NAV-CYBER-SCA-011** - Scanner licences et obligations contractuelles selon la politique de l'organisation
- [ ] **NAV-CYBER-SCA-012** - Analyser vendored code, sous-modules et binaires tiers embarqués
- [ ] **NAV-CYBER-SCA-013** - Suivre nouvelles vulnérabilités affectant les versions déjà déployées

## 14. Git, dépôt, build et CI/CD - 12 contrôles

**Applicabilité :** `cicd` - **Verification :** `hybrid` - **Moteurs possibles :** analyse Git et pipelines, OpenSSF Scorecard

- [ ] **NAV-CYBER-CICD-001** - Vérifier protections des branches critiques et règles de revue
- [ ] **NAV-CYBER-CICD-002** - Vérifier CODEOWNERS, approbations et séparation des rôles
- [ ] **NAV-CYBER-CICD-003** - Vérifier protections contre injection dans scripts de pipeline
- [ ] **NAV-CYBER-CICD-004** - Vérifier permissions minimales des tokens et intégrations CI
- [ ] **NAV-CYBER-CICD-005** - Vérifier isolation des runners, caches et artefacts entre projets
- [ ] **NAV-CYBER-CICD-006** - Vérifier absence de secrets dans les variables accessibles aux contributions externes
- [ ] **NAV-CYBER-CICD-007** - Vérifier signatures de commits/releases si exigées par la politique
- [ ] **NAV-CYBER-CICD-008** - Vérifier provenance, signature et traçabilité des artefacts de build
- [ ] **NAV-CYBER-CICD-009** - Vérifier contrôles de sécurité bloquants avant publication et déploiement
- [ ] **NAV-CYBER-CICD-010** - Vérifier séparation dev/test/preprod/prod et validations de production
- [ ] **NAV-CYBER-CICD-011** - Vérifier reproductibilité ou traçabilité du build et intégrité des dépendances
- [ ] **NAV-CYBER-CICD-012** - Vérifier qu'une version déployée correspond au commit et à l'artefact audités

## 15. Cloud, containers, IaC et Kubernetes applicatif - 15 contrôles

**Applicabilité :** `iac` - **Verification :** `hybrid` - **Moteurs possibles :** Checkov, Trivy config, Kubernetes policy

- [ ] **NAV-CYBER-IAC-001** - Scanner Dockerfiles et images de base pour versions vulnérables
- [ ] **NAV-CYBER-IAC-002** - Vérifier exécution non-root et capacités Linux minimales des conteneurs
- [ ] **NAV-CYBER-IAC-003** - Contrôler systèmes de fichiers read-only et restrictions de privilèges
- [ ] **NAV-CYBER-IAC-004** - Scanner configurations Terraform, OpenTofu, CloudFormation et équivalents
- [ ] **NAV-CYBER-IAC-005** - Scanner Helm, Kustomize et manifests Kubernetes du projet
- [ ] **NAV-CYBER-IAC-006** - Vérifier RBAC Kubernetes et permissions des service accounts
- [ ] **NAV-CYBER-IAC-007** - Vérifier NetworkPolicies et exposition des services/ingress
- [ ] **NAV-CYBER-IAC-008** - Vérifier secrets Kubernetes et gestion via coffre externe si nécessaire
- [ ] **NAV-CYBER-IAC-009** - Vérifier admission policies, quotas et Pod Security Standards
- [ ] **NAV-CYBER-IAC-010** - Vérifier limites de ressources pour réduire risques d'épuisement
- [ ] **NAV-CYBER-IAC-011** - Vérifier buckets, volumes, DB cloud et stockages exposés publiquement
- [ ] **NAV-CYBER-IAC-012** - Vérifier IAM cloud et absence de wildcard excessifs
- [ ] **NAV-CYBER-IAC-013** - Vérifier accès aux métadonnées cloud et protections contre SSRF
- [ ] **NAV-CYBER-IAC-014** - Vérifier séparation des réseaux et des identités par environnement
- [ ] **NAV-CYBER-IAC-015** - Vérifier cohérence des configurations déployées et déclarées (drift)

## 16. Configuration runtime et qualité du déploiement - 11 contrôles

**Applicabilité :** `runtime` - **Verification :** `hybrid` - **Moteurs possibles :** revue configuration et analyse runtime non intrusive

- [ ] **NAV-CYBER-RUN-001** - Vérifier mode debug désactivé et stack traces protégées en production
- [ ] **NAV-CYBER-RUN-002** - Contrôler configuration effective versus configuration attendue
- [ ] **NAV-CYBER-RUN-003** - Vérifier validation des variables et absence de valeurs sûres remplacées en prod
- [ ] **NAV-CYBER-RUN-004** - Vérifier absence de routes cachées, endpoints test et consoles exposées
- [ ] **NAV-CYBER-RUN-005** - Contrôler permissions système et chemins de fichiers de l'application
- [ ] **NAV-CYBER-RUN-006** - Vérifier chargement dynamique et exécution de plugins non fiables
- [ ] **NAV-CYBER-RUN-007** - Vérifier mises à jour signées et mécanismes de rollback
- [ ] **NAV-CYBER-RUN-008** - Vérifier limitation de consommation mémoire, CPU et connexions
- [ ] **NAV-CYBER-RUN-009** - Vérifier échecs de dépendances, timeouts, retries et circuit breakers
- [ ] **NAV-CYBER-RUN-010** - Vérifier comportement fail-closed des opérations de sécurité critiques
- [ ] **NAV-CYBER-RUN-011** - Vérifier sécurité des tâches planifiées, workers, files et consumers

## 17. Applications mobiles Android et iOS - 12 contrôles

**Applicabilité :** `mobile` - **Verification :** `hybrid` - **Moteurs possibles :** MobSF, MASVS/MASTG, simulateur de test

- [ ] **NAV-CYBER-MOBILE-001** - Analyser manifests, permissions et composants exportés Android
- [ ] **NAV-CYBER-MOBILE-002** - Analyser entitlements, ATS, URL schemes et universal links iOS
- [ ] **NAV-CYBER-MOBILE-003** - Vérifier stockage local de secrets et données sensibles
- [ ] **NAV-CYBER-MOBILE-004** - Vérifier usage Android Keystore/iOS Keychain selon le contexte
- [ ] **NAV-CYBER-MOBILE-005** - Vérifier chiffrement et intégrité des données locales sensibles
- [ ] **NAV-CYBER-MOBILE-006** - Contrôler TLS, validation des certificats et politiques de transport
- [ ] **NAV-CYBER-MOBILE-007** - Tester deep links, intents, webviews et ponts JavaScript natifs
- [ ] **NAV-CYBER-MOBILE-008** - Vérifier authentification biométrique et protections de session
- [ ] **NAV-CYBER-MOBILE-009** - Vérifier absence de secrets et endpoints administratifs dans APK/IPA
- [ ] **NAV-CYBER-MOBILE-010** - Tester résilience à la modification des packages et instrumentation selon risque
- [ ] **NAV-CYBER-MOBILE-011** - Vérifier mécanismes de mise à jour, signature et publication
- [ ] **NAV-CYBER-MOBILE-012** - Vérifier confidentialité des SDK analytics/publicitaires et permissions

## 18. Applications desktop, CLI, extensions et installateurs - 11 contrôles

**Applicabilité :** `desktop` - **Verification :** `hybrid` - **Moteurs possibles :** analyse binaires et packaging isolée

- [ ] **NAV-CYBER-DESK-001** - Scanner installateurs, binaires et bibliothèques embarquées
- [ ] **NAV-CYBER-DESK-002** - Vérifier signature de code et vérification de signature au lancement/mise à jour
- [ ] **NAV-CYBER-DESK-003** - Vérifier mise à jour sécurisée contre substitution et rétrogradation
- [ ] **NAV-CYBER-DESK-004** - Vérifier permissions de fichiers, dossiers, journaux et données utilisateur
- [ ] **NAV-CYBER-DESK-005** - Vérifier stockage des identifiants dans coffre-fort système quand disponible
- [ ] **NAV-CYBER-DESK-006** - Vérifier sécurité IPC, sockets locaux, protocol handlers et localhost API
- [ ] **NAV-CYBER-DESK-007** - Vérifier sécurité Electron/Tauri/WebView, navigation et isolation contexte
- [ ] **NAV-CYBER-DESK-008** - Vérifier protection des commandes CLI contre injections et arguments non fiables
- [ ] **NAV-CYBER-DESK-009** - Vérifier sûreté des plugins, extensions et modules téléchargés
- [ ] **NAV-CYBER-DESK-010** - Vérifier élévation de privilèges et séparation des processus
- [ ] **NAV-CYBER-DESK-011** - Vérifier absence d'informations sensibles dans crash dumps et logs

## 19. Applications IA, LLM, agents, RAG et MCP - 17 contrôles

**Applicabilité :** `ai` - **Verification :** `hybrid` - **Moteurs possibles :** OWASP LLM/MCP, tests de sécurité agentique en bac à sable

- [ ] **NAV-CYBER-AI-001** - Identifier modèles, providers, prompts, tools, agents, mémoires et RAG utilisés
- [ ] **NAV-CYBER-AI-002** - Tester injections de prompt directes et indirectes sur contenus non fiables
- [ ] **NAV-CYBER-AI-003** - Tester franchissement des frontières entre instructions système et données externes
- [ ] **NAV-CYBER-AI-004** - Vérifier que sorties du modèle sont validées avant commandes, SQL et API
- [ ] **NAV-CYBER-AI-005** - Contrôler autorisations par outil avec identité et permissions du demandeur
- [ ] **NAV-CYBER-AI-006** - Détecter excessive agency, scopes trop larges et autonomie non bornée
- [ ] **NAV-CYBER-AI-007** - Vérifier sandbox des outils d'exécution de code et restrictions d'accès réseau
- [ ] **NAV-CYBER-AI-008** - Vérifier absence de secrets et PII dans prompts, traces et appels providers
- [ ] **NAV-CYBER-AI-009** - Contrôler isolation multi-tenant de la mémoire, des embeddings et du RAG
- [ ] **NAV-CYBER-AI-010** - Tester empoisonnement de documents, index, métadonnées et retrieval
- [ ] **NAV-CYBER-AI-011** - Vérifier provenance et inspection de documents ou fichiers ingérés
- [ ] **NAV-CYBER-AI-012** - Contrôler MCP: authentification, scopes, descriptions d'outils et trust boundaries
- [ ] **NAV-CYBER-AI-013** - Tester tool poisoning, shadowing et substitutions de serveurs MCP
- [ ] **NAV-CYBER-AI-014** - Vérifier prévention d'exfiltration via tools, URL, messages et fichiers
- [ ] **NAV-CYBER-AI-015** - Tester boucles autonomes, budgets, quotas, limites temporelles et kill switch
- [ ] **NAV-CYBER-AI-016** - Vérifier journalisation et attribution utilisateur de chaque action agentique
- [ ] **NAV-CYBER-AI-017** - Vérifier que les actions destructrices exigent une approbation selon la politique

## 20. Journalisation, surveillance, erreurs et résilience - 10 contrôles

**Applicabilité :** `all` - **Verification :** `hybrid` - **Moteurs possibles :** revue logs, instrumentation et exercices isolés

- [ ] **NAV-CYBER-OBS-001** - Vérifier journaux d'authentification et d'accès aux ressources sensibles
- [ ] **NAV-CYBER-OBS-002** - Vérifier journaux d'administration et modifications d'autorisations
- [ ] **NAV-CYBER-OBS-003** - Vérifier corrélation trace-id, user-id, tenant-id et transaction-id sans secrets
- [ ] **NAV-CYBER-OBS-004** - Vérifier protections contre altération et suppression des journaux d'audit
- [ ] **NAV-CYBER-OBS-005** - Vérifier absence de secrets et données personnelles non nécessaires dans les logs
- [ ] **NAV-CYBER-OBS-006** - Vérifier alertes sur tentatives d'accès interdites et anomalies critiques
- [ ] **NAV-CYBER-OBS-007** - Vérifier gestion des exceptions sans fuite d'informations sensibles
- [ ] **NAV-CYBER-OBS-008** - Vérifier sauvegardes, restauration testée et intégrité des données
- [ ] **NAV-CYBER-OBS-009** - Vérifier dégradation contrôlée et continuité des fonctions de sécurité
- [ ] **NAV-CYBER-OBS-010** - Vérifier cohérence horodatage, rétention et capacité d'investigation

## 21. Fonctions serverless, edge, workers et jobs - 9 contrôles

**Applicabilité :** `serverless` - **Verification :** `hybrid` - **Moteurs possibles :** analyse IaC et fonctions serverless

- [ ] **NAV-CYBER-SRV-001** - Vérifier authentification des fonctions HTTP et déclencheurs événementiels
- [ ] **NAV-CYBER-SRV-002** - Vérifier IAM minimal des fonctions et comptes d'exécution
- [ ] **NAV-CYBER-SRV-003** - Vérifier validation des payloads de files, événements et objets
- [ ] **NAV-CYBER-SRV-004** - Vérifier idempotence, déduplication et reprise après échec
- [ ] **NAV-CYBER-SRV-005** - Vérifier sécurité des variables d'environnement et secrets runtime
- [ ] **NAV-CYBER-SRV-006** - Vérifier restrictions réseau, VPC et endpoints internes
- [ ] **NAV-CYBER-SRV-007** - Vérifier protections contre déclenchement massif et dépassement de coûts
- [ ] **NAV-CYBER-SRV-008** - Vérifier permissions et intégrité des packages et layers
- [ ] **NAV-CYBER-SRV-009** - Vérifier conformité des politiques de timeout, retry et dead-letter queue

## 22. Bibliothèques natives, firmware, WebAssembly et embarqué - 9 contrôles

**Applicabilité :** `native` - **Verification :** `hybrid` - **Moteurs possibles :** fuzzing securise, analyse binaire et compilation

- [ ] **NAV-CYBER-BIN-001** - Analyser code natif pour erreurs mémoire et arithmétiques
- [ ] **NAV-CYBER-BIN-002** - Vérifier compilation durcie et protections de la plateforme
- [ ] **NAV-CYBER-BIN-003** - Vérifier provenance et signatures des firmwares et binaires
- [ ] **NAV-CYBER-BIN-004** - Vérifier intégrité et authenticité du mécanisme de mise à jour
- [ ] **NAV-CYBER-BIN-005** - Vérifier parser de formats binaires, paquets et protocole custom
- [ ] **NAV-CYBER-BIN-006** - Vérifier limitations et permissions des modules WebAssembly
- [ ] **NAV-CYBER-BIN-007** - Vérifier l'isolation des bibliothèques FFI et appels natifs
- [ ] **NAV-CYBER-BIN-008** - Vérifier absence de clés embarquées dans firmware ou ressources
- [ ] **NAV-CYBER-BIN-009** - Vérifier fuzzing des parsers sur cible de test isolée

## 23. Intégrations et services tiers - 10 contrôles

**Applicabilité :** `intégrations` - **Verification :** `hybrid` - **Moteurs possibles :** analyse des flux API et permissions fournisseurs

- [ ] **NAV-CYBER-INT-001** - Inventorier fournisseurs externes, SDK et flux de données
- [ ] **NAV-CYBER-INT-002** - Vérifier authentification et scopes des connecteurs
- [ ] **NAV-CYBER-INT-003** - Vérifier validation et authentification des événements entrants
- [ ] **NAV-CYBER-INT-004** - Vérifier sorties vers domaines externes autorisés et contrôles SSRF
- [ ] **NAV-CYBER-INT-005** - Vérifier rotation et révocation des clés d'intégration
- [ ] **NAV-CYBER-INT-006** - Vérifier traitement des erreurs et réponses malformées de tiers
- [ ] **NAV-CYBER-INT-007** - Vérifier la séparation des tenants chez les intégrations et mappings d'identité
- [ ] **NAV-CYBER-INT-008** - Vérifier gestion des limites API, retries et déduplication
- [ ] **NAV-CYBER-INT-009** - Vérifier implications de la désactivation d'un fournisseur ou SDK
- [ ] **NAV-CYBER-INT-010** - Vérifier sécurité des partages de fichiers et URLs pré-signées

## 24. Qualité des scans, preuve et couverture - 12 contrôles

**Applicabilité :** `all` - **Verification :** `hybrid` - **Moteurs possibles :** matrice de preuves, validation des résultats et suivi de couverture

- [ ] **NAV-CYBER-QA-001** - Exécuter les moteurs SAST/SCA/secret/IaC pertinents et enregistrer leurs versions
- [ ] **NAV-CYBER-QA-002** - Tester que les moteurs s'exécutent réellement et produisent des résultats exploitables
- [ ] **NAV-CYBER-QA-003** - Comparer surfaces détectées et éléments réellement présents dans le dépôt
- [ ] **NAV-CYBER-QA-004** - Cartographier contrôles couverts, non couverts, non applicables et bloqués
- [ ] **NAV-CYBER-QA-005** - Vérifier que chaque résultat est lié à un fichier, ligne, endpoint ou actif précis
- [ ] **NAV-CYBER-QA-006** - Valider les findings critiques par reproduction sûre lorsque possible
- [ ] **NAV-CYBER-QA-007** - Évaluer faux positifs, faux négatifs et limites des outils utilisés
- [ ] **NAV-CYBER-QA-008** - Conserver configurations, empreintes et date des scans pour reproductibilité
- [ ] **NAV-CYBER-QA-009** - Tester que les correctifs génèrent des tests de régression et un nouveau scan
- [ ] **NAV-CYBER-QA-010** - Vérifier non-régression sur pull requests, releases et nouveaux actifs
- [ ] **NAV-CYBER-QA-011** - Bloquer l'étiquette "audit complet" si un contrôle requis n'a pas été exécuté
- [ ] **NAV-CYBER-QA-012** - Produire un rapport machine-readable des lacunes et preuves avec responsables

## References

- [OWASP ASVS 5.0](https://asvs.owasp.org/)
- [OWASP Top 10 2025](https://top10.owasp.org/)
- [OWASP WSTG](https://wstg.owasp.org/)
- [OWASP API Security](https://api-security.owasp.org/)
- [OWASP MASVS](https://mas.owasp.org/MASVS/)
- [OWASP LLM Top 10](https://genai.owasp.org/llm-top-10/)
- [OWASP MCP Top 10](https://owasp.org/projects/mcp-top-10)
- [NIST SSDF](https://csrc.nist.gov/pubs/sp/800/218/final)
- [PostgreSQL RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [OWASP SAMM](https://owaspsamm.org/model/)
