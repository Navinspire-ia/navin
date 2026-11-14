# Couverture Navin Cyber - 302 contrôles

Audit `20261114-154505-144939` du 2026-11-14T14:45:03Z sur `fixture-shop`.

**Couverture vérifiée : 29.7 %** (69 contrôles applicables testés avec preuves sur 232 applicables).

| Statut | Contrôles |
|---|---|
| réussi | 23 |
| échec | 46 |
| non concluant | 67 |
| bloqué | 2 |
| non testé | 94 |
| non applicable | 70 |

**Audit non complet** : 163 contrôle(s) applicable(s) non vérifié(s).

> Un contrôle « réussi » signifie qu'un moteur de sa liste a réellement tourné sur ce périmètre sans trouver de faiblesse : ce n'est pas une preuve de sécurité. La couverture vérifiée ne compte que les contrôles applicables exécutés avec preuves (réussis ou en échec).

## 01. Découverte et cartographie (13/13 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-DISC-001 Recenser tous les dépôts, sous-modules, branches et monorepos | réussi | navin_discovery 2.4.14 | navin_discovery 2.4.14 ok, 0 finding(s) in the run, 0.01 s; audit 20261114-154505-144939; discovery: 11 files read |
| NAV-CYBER-DISC-002 Détecter chaque langage, framework, version et gestionnaire de dépenda | non concluant | navin_discovery 2.4.14 | partial check: languages, frameworks and manifests are detected; framework versions are not resolved |
| NAV-CYBER-DISC-003 Identifier les applications web, API, mobiles, desktop, CLI, bibliothè | non concluant | navin_discovery 2.4.14 | partial check: module kinds come from each manifest's own signals |
| NAV-CYBER-DISC-004 Inventorier les points d'entrée HTTP, RPC, WebSocket, GraphQL, workers | réussi | navin_discovery 2.4.14 | navin_discovery 2.4.14 ok, 0 finding(s) in the run, 0.01 s; audit 20261114-154505-144939; discovery: 11 files read |
| NAV-CYBER-DISC-005 Recenser les domaines, sous-domaines, URL, environnements et points de | non concluant | navin_discovery 2.4.14 | partial check: deploy targets are read from deployment files; no DNS or subdomain enumeration |
| NAV-CYBER-DISC-006 Découvrir les bases, schémas, files, caches, stockages objets et systè | non concluant | navin_discovery 2.4.14 | partial check: databases, integrations and infrastructure files; queues and object stores only by signal |
| NAV-CYBER-DISC-007 Cartographier les flux de données, frontières de confiance et échanges | non concluant | navin_threat_model 2.4.14 | partial check: trust boundaries and scenarios are derived from the surfaces, not from a traced data flow |
| NAV-CYBER-DISC-008 Identifier les rôles humains, de service, administrateurs et leurs par | non concluant | navin_discovery 2.4.14 | partial check: role handling is only flagged (rbac signal); roles and journeys are not inventoried |
| NAV-CYBER-DISC-009 Localiser les fichiers de configuration, secrets, scripts, migrations  | réussi | navin_discovery 2.4.14 | navin_discovery 2.4.14 ok, 0 finding(s) in the run, 0.01 s; audit 20261114-154505-144939; discovery: 11 files read |
| NAV-CYBER-DISC-010 Associer artefacts compilés et versions déployées aux commits et envir | non testé | - | no Navin engine covers it yet: deployed artifacts are not tied to commits |
| NAV-CYBER-DISC-011 Distinguer code applicatif, code généré, exemples, tests et dépendance | réussi | navin_discovery 2.4.14 | navin_discovery 2.4.14 ok, 0 finding(s) in the run, 0.01 s; audit 20261114-154505-144939; discovery: 11 files read |
| NAV-CYBER-DISC-012 Établir pour chaque contrôle le périmètre autorisé, les exclusions et  | réussi | navin_matrix 1.0.0 | 302 controls with applicability, engines and evidence; 70 not applicable, each with its reason |
| NAV-CYBER-DISC-013 Détecter les surfaces non documentées et composants orphelins | non testé | - | no Navin engine covers it yet: undocumented surfaces and orphan components are not compared with a reference |

## 02. Architecture et conception sécurisée (12/12 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-ARCH-001 Reconstituer l'architecture logique et les dépendances critiques | non concluant | navin_discovery 2.4.14, navin_threat_model 2.4.14 | partial check: logical map from surfaces and modules; no architecture graph |
| NAV-CYBER-ARCH-002 Identifier frontières de confiance entre clients, serveurs, données et | non concluant | navin_threat_model 2.4.14 | partial check: trust boundaries from the threat model |
| NAV-CYBER-ARCH-003 Construire des scénarios de menace par fonctionnalité sensible et par  | non concluant | navin_threat_model 2.4.14 | partial check: scenarios per surface, not per feature and role |
| NAV-CYBER-ARCH-004 Vérifier la séparation frontend/backend et l'absence de confiance dans | non concluant | navin_business_logic 2.4.14, navin_database 2.4.14 | partial check: client-set prices and service keys in the client |
| NAV-CYBER-ARCH-005 Vérifier les principes de moindre privilège et de refus par défaut | échec | navin_database 2.4.14, navin_auth 2.4.14 | db/mysql.sql:2; app.py:17; supabase/migrations/001_init.sql:11 |
| NAV-CYBER-ARCH-006 Vérifier la segmentation des plans de contrôle, de données et d'admini | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-ARCH-007 Contrôler la séparation des tenants et des environnements de développe | échec | navin_tenant 2.4.14, navin_database 2.4.14, navin_db_ephemeral 16.2 | supabase/migrations/001_init.sql:4; supabase/migrations/001_init.sql:5; supabase/migrations/001_init.sql:1 |
| NAV-CYBER-ARCH-008 Examiner les chemins de contournement des contrôles de sécurité | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-ARCH-009 Vérifier le modèle de confiance des services tiers et webhooks | non concluant | navin_auth 2.4.14 | partial check: unsigned webhooks only |
| NAV-CYBER-ARCH-010 Identifier les points uniques de défaillance ayant un impact de sécuri | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-ARCH-011 Vérifier que les opérations sensibles sont journalisées et approuvées  | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-ARCH-012 Vérifier la traçabilité des exigences de sécurité vers des tests exécu | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |

## 03. Analyse statique du code et défauts techniques (17/18 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-SAST-001 Détecter injections SQL, NoSQL et requêtes construites par concaténati | échec | navin_dataflow 2.4.14, navin_rules 2.4.14 | app.py:13; app.py:13 |
| NAV-CYBER-SAST-002 Détecter injections de commandes système et paramètres shell non maîtr | réussi | navin_dataflow 2.4.14, navin_rules 2.4.14 | navin_dataflow 2.4.14 ok, 2 finding(s) in the run, 0.0 s; navin_rules 2.4.14 ok, 13 finding(s) in the run, 0.25 s; audit 20261114-154505-144939 |
| NAV-CYBER-SAST-003 Détecter injections LDAP, XPath, XQuery et moteurs de recherche | non concluant | navin_rules 2.4.14 | partial check: LDAP and XPath patterns; no XQuery or search-engine rule |
| NAV-CYBER-SAST-004 Détecter injections de templates, expressions et interpréteurs | réussi | navin_dataflow 2.4.14, navin_rules 2.4.14 | navin_dataflow 2.4.14 ok, 2 finding(s) in the run, 0.0 s; navin_rules 2.4.14 ok, 13 finding(s) in the run, 0.25 s; audit 20261114-154505-144939 |
| NAV-CYBER-SAST-005 Détecter XSS stockées, réfléchies et DOM liées aux flux de données | non concluant | navin_rules 2.4.14 | partial check: sink patterns without a JavaScript dataflow; not run: semgrep |
| NAV-CYBER-SAST-006 Détecter désérialisation non sûre et chargement de types arbitraires | échec | navin_dataflow 2.4.14, navin_rules 2.4.14 | app.py:18; app.py:18 |
| NAV-CYBER-SAST-007 Détecter traversée de chemins, lecture/écriture arbitraire et inclusio | réussi | navin_dataflow 2.4.14, navin_rules 2.4.14 | navin_dataflow 2.4.14 ok, 2 finding(s) in the run, 0.0 s; navin_rules 2.4.14 ok, 13 finding(s) in the run, 0.25 s; audit 20261114-154505-144939 |
| NAV-CYBER-SAST-008 Détecter SSRF et accès non maîtrisé aux URL, notamment aux métadonnées | non concluant | navin_dataflow 2.4.14, navin_rules 2.4.14 | partial check: Python dataflow plus patterns; cloud metadata only by literal address |
| NAV-CYBER-SAST-009 Détecter XXE et parsers XML non sécurisés | réussi | navin_rules 2.4.14 | navin_rules 2.4.14 ok, 13 finding(s) in the run, 0.25 s; audit 20261114-154505-144939 |
| NAV-CYBER-SAST-010 Détecter exécution de code dynamique, eval et chargement de modules no | réussi | navin_dataflow 2.4.14, navin_rules 2.4.14 | navin_dataflow 2.4.14 ok, 2 finding(s) in the run, 0.0 s; navin_rules 2.4.14 ok, 13 finding(s) in the run, 0.25 s; audit 20261114-154505-144939 |
| NAV-CYBER-SAST-011 Détecter prototype pollution et manipulation dangereuse d'objets dynam | non concluant | navin_rules 2.4.14 | partial check: one pattern rule |
| NAV-CYBER-SAST-012 Détecter redirections ouvertes et validation insuffisante des destinat | non concluant | navin_rules 2.4.14, navin_dataflow 2.4.14 | partial check: pattern rules |
| NAV-CYBER-SAST-013 Détecter ReDoS et expressions régulières à complexité dangereuse | non concluant | navin_rules 2.4.14 | partial check: regexes built from input; no complexity analysis |
| NAV-CYBER-SAST-014 Détecter débordements mémoire, use-after-free, integer overflow et acc | non applicable | navin_rules 2.4.14 | no native surface found by discovery in 11 files read |
| NAV-CYBER-SAST-015 Détecter chemins de concurrence, data races, TOCTOU et défauts de sync | non concluant | navin_rules 2.4.14 | partial check: one Python TOCTOU pattern |
| NAV-CYBER-SAST-016 Détecter manipulations de formats d'archives, Zip Slip et bombes de dé | non concluant | navin_rules 2.4.14 | partial check: archive extraction patterns |
| NAV-CYBER-SAST-017 Détecter désactivation des contrôles de sécurité dans les branches de  | non concluant | navin_rules 2.4.14 | partial check: security switches turned off in code |
| NAV-CYBER-SAST-018 Vérifier validation des entrées, encodage contextuel des sorties et ty | non concluant | navin_dataflow 2.4.14 | partial check: validation is only seen as input reaching a sink; not run: semgrep |

## 04. Authentification et gestion des sessions (14/14 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-AUTH-001 Contrôler création de compte, validation d'email et invitation | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-002 Contrôler politique de mots de passe et stockage par hachage adapté | non concluant | navin_rules 2.4.14 | partial check: hash algorithm and cost; no policy check |
| NAV-CYBER-AUTH-003 Contrôler récupération de compte, réinitialisation et changement de mo | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-004 Vérifier activation, révocation et contournement éventuel de la MFA | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-005 Vérifier limites de tentatives, anti-énumération et protection contre  | non concluant | navin_auth 2.4.14 | partial check: login routes without rate limiting |
| NAV-CYBER-AUTH-006 Vérifier durée de vie, rotation et révocation des sessions et refresh  | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-007 Contrôler cookies Secure, HttpOnly, SameSite et fixation de session | échec | navin_rules 2.4.14, navin_web_baseline 2.4.14 | / |
| NAV-CYBER-AUTH-008 Valider JWT: algorithmes autorisés, signatures, audiences, issuers, ex | échec | navin_auth 2.4.14, navin_rules 2.4.14 | app.py:23; app.py:23 |
| NAV-CYBER-AUTH-009 Contrôler OAuth2/OIDC: redirect URI, PKCE, state, nonce et échange de  | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-010 Vérifier le lien entre identité externe et compte local (account linki | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-011 Contrôler déconnexion, invalidation de session et changement de privil | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-012 Vérifier authentification des services, workloads et tâches automatisé | échec | navin_auth 2.4.14 | app.py:17 |
| NAV-CYBER-AUTH-013 Vérifier absence d'identifiants par défaut, comptes de test et chemins | échec | navin_rules 2.4.14, navin_datastores 2.4.14 | db/mysql.sql:1; docker-compose.yml:5 |
| NAV-CYBER-AUTH-014 Contrôler authentification et validation des signatures des callbacks/ | réussi | navin_auth 2.4.14 | navin_auth 2.4.14 ok, 1 finding(s) in the run, 0.0 s; audit 20261114-154505-144939 |

## 05. Autorisations et isolation des accès (14/14 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-AUTHZ-001 Tester BOLA/IDOR sur chaque objet accessible par identifiant | non testé | - | no Navin engine covers it yet: object-level access needs two synthetic accounts on an isolated target; needs two synthetic test accounts on an isolated environment |
| NAV-CYBER-AUTHZ-002 Tester contrôle d'autorisation au niveau de chaque fonction et endpoin | échec | navin_auth 2.4.14 | app.py:17 |
| NAV-CYBER-AUTHZ-003 Tester autorisation par propriété/champ (lecture et écriture) | non testé | - | no Navin engine covers it yet: field-level access needs a running target; needs two synthetic test accounts on an isolated environment |
| NAV-CYBER-AUTHZ-004 Tester élévation horizontale entre utilisateurs de même niveau | non testé | - | no Navin engine covers it yet: horizontal escalation needs two accounts of the same level; needs two synthetic test accounts on an isolated environment |
| NAV-CYBER-AUTHZ-005 Tester élévation verticale vers fonctions opérateur, support et admini | échec | navin_auth 2.4.14 | app.py:17 |
| NAV-CYBER-AUTHZ-006 Tester isolation entre tenants, organisations, projets, entités et env | échec | navin_tenant 2.4.14, navin_database 2.4.14, navin_db_ephemeral 16.2 | supabase/migrations/001_init.sql:4; supabase/migrations/001_init.sql:5; supabase/migrations/001_init.sql:1 |
| NAV-CYBER-AUTHZ-007 Vérifier les contrôles d'accès côté serveur sur chaque opération sensi | échec | navin_auth 2.4.14 | app.py:17 |
| NAV-CYBER-AUTHZ-008 Tester accès non autorisé via exports, recherche, listes et fonctionna | non testé | - | no Navin engine covers it yet: exports, search and batch endpoints are not tested; needs two synthetic test accounts on an isolated environment |
| NAV-CYBER-AUTHZ-009 Tester mass assignment et champs protégés acceptés à la création/modif | non concluant | navin_rules 2.4.14 | partial check: mass assignment pattern |
| NAV-CYBER-AUTHZ-010 Contrôler l'héritage, la propagation et la révocation des rôles | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTHZ-011 Contrôler délégation, impersonation et accès support avec audit et exp | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTHZ-012 Tester accès aux ressources liées, fichiers, commentaires et métadonné | non testé | - | no Navin engine covers it yet: related resources are not tested; needs two synthetic test accounts on an isolated environment |
| NAV-CYBER-AUTHZ-013 Vérifier restrictions des clés API, scopes OAuth et permissions des co | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTHZ-014 Vérifier qu'un cache ou index de recherche ne contourne pas les règles | non testé | - | no Navin engine covers it yet: caches and search indexes are not tested against access rules; needs two synthetic test accounts on an isolated environment |

## 06. Logique métier, états et transactions (12/12 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-BUS-001 Modéliser les états autorisés des processus métiers sensibles | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-002 Tester sauts d'étapes, validation prématurée et bypass d'approbation | non testé | - | no Navin engine covers it yet: workflow step skipping needs scenario tests |
| NAV-CYBER-BUS-003 Tester accès ou modification après clôture, annulation ou expiration | non testé | - | no Navin engine covers it yet: post-closure access needs scenario tests |
| NAV-CYBER-BUS-004 Tester doubles soumissions, replays et idempotence des opérations | non testé | - | no Navin engine covers it yet: replay and idempotence need scenario tests |
| NAV-CYBER-BUS-005 Tester concurrence, courses critiques et double traitement | non testé | - | no Navin engine covers it yet: concurrency needs an isolated target and concurrent requests |
| NAV-CYBER-BUS-006 Contrôler montants, unités, soldes, arrondis, bornes et valeurs négati | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-007 Vérifier unicité, intégrité et cohérence des références métier | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-008 Tester séparation des tâches, double validation et privilèges incompat | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-009 Tester falsification de prix, quotas, limites et paramètres métier ven | réussi | navin_business_logic 2.4.14 | navin_business_logic 2.4.14 ok, 0 finding(s) in the run, 0.0 s; audit 20261114-154505-144939 |
| NAV-CYBER-BUS-010 Tester incohérence entre frontend, API, files et traitements différés | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-011 Vérifier protections contre abus de fonctionnalités sensibles et autom | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-012 Tester l'échec partiel des workflows et la restauration d'un état cohé | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |

## 07. Secrets, identifiants et exposition (10/11 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-SECRET-001 Scanner secrets dans les fichiers source et de configuration | réussi | navin_rules 2.4.14 | navin_rules 2.4.14 ok, 13 finding(s) in the run, 0.25 s; audit 20261114-154505-144939 |
| NAV-CYBER-SECRET-002 Scanner secrets dans tout l'historique Git accessible et tags autorisé | non applicable | - | not a git repository: there is no history to scan |
| NAV-CYBER-SECRET-003 Scanner .env, fichiers de tests, exemples, notebooks et scripts | non concluant | navin_rules 2.4.14 | partial check: env files and scripts; notebooks are not parsed; not run: gitleaks |
| NAV-CYBER-SECRET-004 Scanner secrets dans images, binaires, bundles JS et ressources embarq | bloqué | - | no engine of this control ran: trivy not_installed (https://trivy.dev/latest/getting-started/installation/); gitleaks not_installed (https://github.com/gitleaks/gitleaks/releases) |
| NAV-CYBER-SECRET-005 Vérifier absence de secrets dans logs, traces, messages d'erreur et ar | non concluant | navin_rules 2.4.14 | partial check: log statements and CI echo of secrets |
| NAV-CYBER-SECRET-006 Identifier clés réutilisées entre environnements et scopes trop étendu | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SECRET-007 Vérifier politique de rotation, révocation et durée de vie des secrets | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SECRET-008 Vérifier usage de gestionnaires de secrets et absence de clés dans le  | non concluant | navin_rules 2.4.14, navin_database 2.4.14 | partial check: keys in code and service keys in client code |
| NAV-CYBER-SECRET-009 Vérifier permissions d'accès aux secrets par service et environnement | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SECRET-010 Détecter certificats et clés privées divulgués, expirés ou mal protégé | réussi | navin_rules 2.4.14 | navin_rules 2.4.14 ok, 13 finding(s) in the run, 0.25 s; audit 20261114-154505-144939 |
| NAV-CYBER-SECRET-011 Détecter données de connexion de production dans fixtures et sauvegard | non concluant | navin_rules 2.4.14 | partial check: connection strings in the tree |

## 08. Cryptographie et protection des échanges (11/11 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-CRYP-001 Vérifier TLS et certificats sur les flux applicatifs concernés | échec | navin_rules 2.4.14, navin_web_baseline 2.4.14, navin_datastores 2.4.14 | src/db.ts:1 |
| NAV-CYBER-CRYP-002 Rechercher algorithmes obsolètes et paramètres cryptographiques faible | réussi | navin_rules 2.4.14 | navin_rules 2.4.14 ok, 13 finding(s) in the run, 0.25 s; audit 20261114-154505-144939 |
| NAV-CYBER-CRYP-003 Vérifier hachage des mots de passe avec sel et coût adaptés | non concluant | navin_rules 2.4.14 | partial check: algorithm and cost |
| NAV-CYBER-CRYP-004 Vérifier usage d'aléa cryptographique approprié pour jetons et clés | réussi | navin_rules 2.4.14 | navin_rules 2.4.14 ok, 13 finding(s) in the run, 0.25 s; audit 20261114-154505-144939 |
| NAV-CYBER-CRYP-005 Vérifier chiffrement authentifié et gestion correcte nonce/IV | non concluant | navin_rules 2.4.14 | partial check: static IV and ECB; no nonce reuse analysis |
| NAV-CYBER-CRYP-006 Vérifier signatures, validation et intégrité des objets critiques | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CRYP-007 Vérifier dérivation, stockage, rotation et séparation des clés cryptog | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CRYP-008 Détecter cryptographie maison ou contournement de validation TLS | non concluant | navin_rules 2.4.14 | partial check: TLS validation turned off |
| NAV-CYBER-CRYP-009 Vérifier chiffrement des sauvegardes et données sensibles au repos | non concluant | navin_rules 2.4.14 | partial check: Terraform encryption flags only |
| NAV-CYBER-CRYP-010 Vérifier non-divulgation de clés dans frontend, mobile, desktop et log | non concluant | navin_rules 2.4.14 | partial check: keys in the tree; binaries are not inspected |
| NAV-CYBER-CRYP-011 Vérifier résistance aux attaques de rejeu et anti-replay lorsque néces | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |

## 09. API REST, GraphQL, gRPC et communications (14/14 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-API-001 Inventorier endpoints et méthodes à partir du code, schémas et runtime | non concluant | navin_discovery 2.4.14 | partial check: entry points from the code; no runtime inventory |
| NAV-CYBER-API-002 Comparer routes exposées aux spécifications OpenAPI/GraphQL/protobuf | non testé | - | no Navin engine covers it yet: routes are not compared with an OpenAPI, GraphQL or protobuf specification |
| NAV-CYBER-API-003 Tester authentification et autorisation de chaque méthode ou resolver | échec | navin_auth 2.4.14 | app.py:17 |
| NAV-CYBER-API-004 Tester BOLA, BFLA, BOPLA et mass assignment des requêtes | non concluant | navin_rules 2.4.14 | partial check: mass assignment only; no BOLA or BFLA test |
| NAV-CYBER-API-005 Contrôler validation stricte des paramètres, types, tailles et formats | non concluant | navin_dataflow 2.4.14 | partial check: input reaching a sink; no schema validation check |
| NAV-CYBER-API-006 Contrôler pagination, limites de ressources, complexité GraphQL et pro | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-API-007 Contrôler rate limits, quotas et protections contre abus métier | non concluant | navin_auth 2.4.14 | partial check: login rate limiting only |
| NAV-CYBER-API-008 Vérifier CORS, origines autorisées et requêtes cross-origin authentifi | échec | navin_rules 2.4.14, navin_web_baseline 2.4.14 | / |
| NAV-CYBER-API-009 Vérifier gestion des erreurs sans divulgation d'informations internes | non concluant | navin_web_baseline 2.4.14, navin_rules 2.4.14 | partial check: debug mode and error pages |
| NAV-CYBER-API-010 Vérifier sécurité des subscriptions, WebSocket et canaux temps réel | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-API-011 Vérifier signatures, idempotence et protection replay des webhooks | non concluant | navin_auth 2.4.14 | partial check: signature presence; replay protection is not checked |
| NAV-CYBER-API-012 Vérifier consommation sûre des API tierces, schémas et erreurs | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-API-013 Vérifier impossibilité d'accès aux API internes/admin depuis canaux no | échec | navin_web_baseline 2.4.14 | /actuator/env |
| NAV-CYBER-API-014 Vérifier protection SSRF des endpoints prenant des URLs en entrée | réussi | navin_dataflow 2.4.14, navin_rules 2.4.14 | navin_dataflow 2.4.14 ok, 2 finding(s) in the run, 0.0 s; navin_rules 2.4.14 ok, 13 finding(s) in the run, 0.25 s; audit 20261114-154505-144939 |

## 10. Applications web et tests dynamiques (14/14 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-WEB-001 Explorer les routes et ressources publiques avec un crawl borné | non concluant | navin_web_baseline 2.4.14 | partial check: a bounded set of paths, not a crawl |
| NAV-CYBER-WEB-002 Contrôler TLS, HSTS et redirections HTTP vers HTTPS | non testé | navin_web_baseline 2.4.14 | no engine of this control ran: navin_web_baseline ok (16 request(s) to http://127.0.0.1:33731) |
| NAV-CYBER-WEB-003 Contrôler en-têtes CSP, X-Content-Type-Options et politique de framing | échec | navin_web_baseline 2.4.14 | /; / |
| NAV-CYBER-WEB-004 Contrôler cookies, sessions et stockage navigateur sensibles | échec | navin_web_baseline 2.4.14 | / |
| NAV-CYBER-WEB-005 Tester XSS réfléchies, stockées et DOM avec charges non destructrices | non testé | - | no Navin engine covers it yet: active XSS payloads are not sent |
| NAV-CYBER-WEB-006 Tester CSRF sur actions qui modifient l'état | non testé | - | no Navin engine covers it yet: CSRF on state-changing actions is not tested |
| NAV-CYBER-WEB-007 Tester injections et path traversal avec corpus sûr et limites stricte | non testé | - | no Navin engine covers it yet: active injection and traversal payloads are not sent |
| NAV-CYBER-WEB-008 Tester upload de fichiers: types, taille, stockage, isolation et inspe | non testé | - | no Navin engine covers it yet: uploads are not tested |
| NAV-CYBER-WEB-009 Tester contrôle des téléchargements et accès aux fichiers privés | non testé | - | no Navin engine covers it yet: downloads and private files are not tested |
| NAV-CYBER-WEB-010 Tester sécurité CORS, postMessage et intégrations iframe | échec | navin_web_baseline 2.4.14 | / |
| NAV-CYBER-WEB-011 Vérifier protections clickjacking et open redirect | échec | navin_web_baseline 2.4.14 | / |
| NAV-CYBER-WEB-012 Vérifier cache/CDN: absence de réponse privée partagée | échec | navin_web_baseline 2.4.14 | / |
| NAV-CYBER-WEB-013 Vérifier source maps, mode debug et ressources internes exposées | échec | navin_web_baseline 2.4.14 | /actuator/env; /app.js.map; / |
| NAV-CYBER-WEB-014 Vérifier séparation des routes d'administration et endpoints technique | non concluant | navin_web_baseline 2.4.14 | partial check: a few well-known admin paths |

## 11. Bases SQL/NoSQL, RLS et sécurité des données (18/18 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-DB-001 Inventorier bases, schémas, tables, collections, vues, fonctions et tr | non concluant | navin_database 2.4.14, navin_db_ephemeral 16.2, navin_datastores 2.4.14, navin_discovery 2.4.14 | partial check: PostgreSQL objects declared in the migrations; MySQL/MariaDB, SQL Server, Oracle, MongoDB, Redis, Elasticsearch, Cassandra, Firebase, DynamoDB, Cosmos DB and others detected from drivers, configs and scrip |
| NAV-CYBER-DB-002 Inventorier rôles, utilisateurs, GRANT/REVOKE et privilèges par objet | échec | navin_database 2.4.14, navin_db_ephemeral 16.2, navin_datastores 2.4.14 | supabase/migrations/001_init.sql:10; db/mysql.sql:2; db/mysql.sql:1 |
| NAV-CYBER-DB-003 Vérifier accès par moindre privilège et comptes séparés lecture/écritu | échec | navin_datastores 2.4.14, navin_database 2.4.14 | db/mysql.sql:2 |
| NAV-CYBER-DB-004 Tester injection des requêtes générées par ORM, SQL brut et procédures | échec | navin_dataflow 2.4.14, navin_database 2.4.14, navin_rules 2.4.14 | app.py:13; app.py:13 |
| NAV-CYBER-DB-005 Détecter bases, consoles, ports et endpoints de données exposés | échec | navin_datastores 2.4.14, navin_rules 2.4.14 | docker-compose.yml:5; redis.conf:2; docker-compose.yml:7 |
| NAV-CYBER-DB-006 Vérifier chiffrement des connexions et règles de connexion depuis l'ap | échec | navin_datastores 2.4.14 | src/db.ts:1 |
| NAV-CYBER-DB-007 Vérifier activation de RLS sur tables exposées lorsqu'applicable | échec | navin_database 2.4.14, navin_db_ephemeral 16.2, navin_datastores 2.4.14 | supabase/migrations/001_init.sql:9 |
| NAV-CYBER-DB-008 Tester RLS SELECT en utilisateur normal, anonyme et utilisateurs de te | échec | navin_db_ephemeral 16.2, navin_database 2.4.14, navin_datastores 2.4.14 | supabase/migrations/001_init.sql:4; supabase/migrations/001_init.sql:1; supabase/migrations/001_init.sql:5 |
| NAV-CYBER-DB-009 Tester RLS INSERT et WITH CHECK pour empêcher création dans un autre t | échec | navin_database 2.4.14, navin_db_ephemeral 16.2, navin_datastores 2.4.14 | supabase/migrations/001_init.sql:4; supabase/migrations/001_init.sql:5; supabase/migrations/001_init.sql:5 |
| NAV-CYBER-DB-010 Tester RLS UPDATE avec USING et WITH CHECK, y compris changement de te | échec | navin_database 2.4.14, navin_db_ephemeral 16.2 | supabase/migrations/001_init.sql:5 |
| NAV-CYBER-DB-011 Tester RLS DELETE et permissions de suppression des rôles ordinaires | échec | navin_database 2.4.14, navin_db_ephemeral 16.2, navin_datastores 2.4.14 | supabase/migrations/001_init.sql:4; supabase/migrations/001_init.sql:5; supabase/migrations/001_init.sql:10 |
| NAV-CYBER-DB-012 Vérifier politiques permissive/restrictive, combinaisons OR/AND et den | non concluant | navin_database 2.4.14, navin_db_ephemeral 16.2 | partial check: OR and AND semantics of PostgreSQL policies |
| NAV-CYBER-DB-013 Vérifier contournements RLS via propriétaire, superuser et BYPASSRLS | non concluant | navin_database 2.4.14, navin_db_ephemeral 16.2, navin_datastores 2.4.14 | partial check: BYPASSRLS and superuser roles, disabled security policies; FORCE ROW LEVEL SECURITY is not checked |
| NAV-CYBER-DB-014 Contrôler fonctions SECURITY DEFINER, search_path et droits EXECUTE | échec | navin_database 2.4.14, navin_db_ephemeral 16.2, navin_datastores 2.4.14 | supabase/migrations/001_init.sql:11; supabase/migrations/001_init.sql:11; supabase/migrations/001_init.sql:11 |
| NAV-CYBER-DB-015 Contrôler vues security_invoker et droits des vues matérialisées/expos | échec | navin_database 2.4.14, navin_db_ephemeral 16.2 | supabase/migrations/001_init.sql:12 |
| NAV-CYBER-DB-016 Tester routes RPC/ORM/jobs utilisant service_role ou connexion privilé | non concluant | navin_database 2.4.14, navin_datastores 2.4.14 | partial check: service keys in client code and administrator logins in connection strings |
| NAV-CYBER-DB-017 Vérifier migrations SQL, déclencheurs, contraintes et défauts de privi | échec | navin_database 2.4.14, navin_db_ephemeral 16.2, navin_datastores 2.4.14 | supabase/migrations/001_init.sql:10; db/mysql.sql:1; supabase/migrations/001_init.sql:11 |
| NAV-CYBER-DB-018 Vérifier audits de lecture/écriture sensibles et protections des dumps | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |

## 12. Protection des données et vie privée (10/10 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-DATA-001 Identifier données sensibles, personnelles, financières et secrets mét | non concluant | navin_discovery 2.4.14 | partial check: PII-like field names in sampled code and SQL |
| NAV-CYBER-DATA-002 Cartographier parcours, réplications, exports et stockage des données | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-003 Contrôler minimisation des données renvoyées par API et UI | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-004 Contrôler anonymisation et masquage des jeux de test et de démonstrati | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-005 Vérifier politiques de rétention, purge et suppression autorisée | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-006 Contrôler partages de fichiers, liens signés, expiration et autorisati | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-007 Vérifier segmentation des stockages et isolation par tenant | non concluant | navin_tenant 2.4.14 | partial check: tenant scoping of queries |
| NAV-CYBER-DATA-008 Vérifier protections contre exfiltration via logs, analytics et télémé | non concluant | navin_rules 2.4.14 | partial check: secrets and tokens in log statements |
| NAV-CYBER-DATA-009 Vérifier recherche/index/vector store sans accès transversal non autor | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-010 Tester données personnelles présentes dans caches, exports et artefact | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |

## 13. Dépendances et chaîne d'approvisionnement (13/13 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-SCA-001 Recenser dépendances directes, transitives et dépendances de build | non concluant | navin_discovery 2.4.14 | partial check: dependency names from the manifests; transitive dependencies need a scanner's resolution |
| NAV-CYBER-SCA-002 Produire un SBOM avec versions et provenance quand disponible | non testé | - | no Navin engine covers it yet: no SBOM generator (Syft or Trivy SBOM) is integrated |
| NAV-CYBER-SCA-003 Faire la correspondance avec CVE, avis fournisseurs et bases de vulnér | bloqué | - | no engine of this control ran: osv_scanner not_installed (https://google.github.io/osv-scanner/installation/); trivy not_installed (https://trivy.dev/latest/getting-started/installation/); pip_audit not_installed (pipx i |
| NAV-CYBER-SCA-004 Croiser vulnérabilités exploitables connues CISA KEV, EPSS et contexte | non testé | - | no Navin engine covers it yet: CISA KEV and EPSS are not queried |
| NAV-CYBER-SCA-005 Identifier packages abandonnés ou non maintenus et versions obsolètes | non testé | - | no Navin engine covers it yet: abandoned or unmaintained packages are not detected |
| NAV-CYBER-SCA-006 Identifier typosquatting, dependency confusion et paquets non attendus | échec | navin_rules 2.4.14 | package.json:1 |
| NAV-CYBER-SCA-007 Vérifier intégrité et cohérence des lockfiles et résolutions de versio | échec | navin_rules 2.4.14 | package.json:1 |
| NAV-CYBER-SCA-008 Vérifier absence de dépendances flottantes sur chemins critiques | non testé | - | no Navin engine covers it yet: floating versions are not detected |
| NAV-CYBER-SCA-009 Vérifier scripts d'installation et hooks potentiellement dangereux | non testé | - | no Navin engine covers it yet: install scripts and hooks are not inspected |
| NAV-CYBER-SCA-010 Vérifier provenance des packages, signatures et attestations disponibl | non testé | - | no Navin engine covers it yet: package provenance, signatures and attestations are not verified |
| NAV-CYBER-SCA-011 Scanner licences et obligations contractuelles selon la politique de l | non testé | - | no Navin engine covers it yet: licences are not scanned |
| NAV-CYBER-SCA-012 Analyser vendored code, sous-modules et binaires tiers embarqués | non concluant | navin_discovery 2.4.14 | partial check: vendored directories and submodules are listed; their content is not scanned |
| NAV-CYBER-SCA-013 Suivre nouvelles vulnérabilités affectant les versions déjà déployées | non testé | - | no Navin engine covers it yet: deployed versions are not monitored for new vulnerabilities |

## 14. Git, dépôt, build et CI/CD (12/12 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-CICD-001 Vérifier protections des branches critiques et règles de revue | non testé | - | no Navin engine covers it yet: branch protection needs the forge API |
| NAV-CYBER-CICD-002 Vérifier CODEOWNERS, approbations et séparation des rôles | non testé | - | no Navin engine covers it yet: CODEOWNERS and approvals are not read |
| NAV-CYBER-CICD-003 Vérifier protections contre injection dans scripts de pipeline | échec | navin_rules 2.4.14 | .github/workflows/ci.yml:6; .github/workflows/ci.yml:1 |
| NAV-CYBER-CICD-004 Vérifier permissions minimales des tokens et intégrations CI | non concluant | navin_rules 2.4.14 | partial check: GitHub Actions token permissions only |
| NAV-CYBER-CICD-005 Vérifier isolation des runners, caches et artefacts entre projets | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CICD-006 Vérifier absence de secrets dans les variables accessibles aux contrib | échec | navin_rules 2.4.14 | .github/workflows/ci.yml:1 |
| NAV-CYBER-CICD-007 Vérifier signatures de commits/releases si exigées par la politique | non testé | - | no Navin engine covers it yet: commit and release signatures are not verified |
| NAV-CYBER-CICD-008 Vérifier provenance, signature et traçabilité des artefacts de build | non testé | - | no Navin engine covers it yet: build provenance and artifact signatures are not verified |
| NAV-CYBER-CICD-009 Vérifier contrôles de sécurité bloquants avant publication et déploiem | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CICD-010 Vérifier séparation dev/test/preprod/prod et validations de production | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CICD-011 Vérifier reproductibilité ou traçabilité du build et intégrité des dép | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CICD-012 Vérifier qu'une version déployée correspond au commit et à l'artefact  | non testé | - | no Navin engine covers it yet: the deployed version is not matched with the audited commit |

## 15. Cloud, containers, IaC et Kubernetes applicatif (15/15 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-IAC-001 Scanner Dockerfiles et images de base pour versions vulnérables | échec | navin_rules 2.4.14 | Dockerfile:1 |
| NAV-CYBER-IAC-002 Vérifier exécution non-root et capacités Linux minimales des conteneur | échec | navin_rules 2.4.14 | k8s/deploy.yaml:12; Dockerfile:1 |
| NAV-CYBER-IAC-003 Contrôler systèmes de fichiers read-only et restrictions de privilèges | non concluant | navin_rules 2.4.14 | partial check: securityContext flags |
| NAV-CYBER-IAC-004 Scanner configurations Terraform, OpenTofu, CloudFormation et équivale | non concluant | navin_rules 2.4.14 | partial check: Terraform rules; CloudFormation and OpenTofu are not parsed; not run: trivy |
| NAV-CYBER-IAC-005 Scanner Helm, Kustomize et manifests Kubernetes du projet | échec | navin_rules 2.4.14 | k8s/deploy.yaml:12; k8s/deploy.yaml:1 |
| NAV-CYBER-IAC-006 Vérifier RBAC Kubernetes et permissions des service accounts | non concluant | navin_rules 2.4.14 | partial check: cluster-admin bindings |
| NAV-CYBER-IAC-007 Vérifier NetworkPolicies et exposition des services/ingress | non concluant | navin_rules 2.4.14 | partial check: open ingress rules; NetworkPolicy presence is not checked |
| NAV-CYBER-IAC-008 Vérifier secrets Kubernetes et gestion via coffre externe si nécessair | non concluant | navin_rules 2.4.14 | partial check: secrets in manifests |
| NAV-CYBER-IAC-009 Vérifier admission policies, quotas et Pod Security Standards | non testé | - | no Navin engine covers it yet: admission policies and Pod Security Standards are not checked |
| NAV-CYBER-IAC-010 Vérifier limites de ressources pour réduire risques d'épuisement | échec | navin_rules 2.4.14 | k8s/deploy.yaml:1 |
| NAV-CYBER-IAC-011 Vérifier buckets, volumes, DB cloud et stockages exposés publiquement | échec | navin_rules 2.4.14, navin_datastores 2.4.14 | docker-compose.yml:7 |
| NAV-CYBER-IAC-012 Vérifier IAM cloud et absence de wildcard excessifs | non concluant | navin_rules 2.4.14 | partial check: IAM wildcards in Terraform |
| NAV-CYBER-IAC-013 Vérifier accès aux métadonnées cloud et protections contre SSRF | non concluant | navin_rules 2.4.14 | partial check: IMDSv1 and metadata literals |
| NAV-CYBER-IAC-014 Vérifier séparation des réseaux et des identités par environnement | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-IAC-015 Vérifier cohérence des configurations déployées et déclarées (drift) | non testé | - | no Navin engine covers it yet: drift between declared and deployed configuration is not computed |

## 16. Configuration runtime et qualité du déploiement (11/11 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-RUN-001 Vérifier mode debug désactivé et stack traces protégées en production | non concluant | navin_rules 2.4.14 | partial check: debug flags in code |
| NAV-CYBER-RUN-002 Contrôler configuration effective versus configuration attendue | non testé | - | no Navin engine covers it yet: effective configuration is not read from a running target |
| NAV-CYBER-RUN-003 Vérifier validation des variables et absence de valeurs sûres remplacé | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-RUN-004 Vérifier absence de routes cachées, endpoints test et consoles exposée | échec | navin_web_baseline 2.4.14, navin_rules 2.4.14 | /actuator/env |
| NAV-CYBER-RUN-005 Contrôler permissions système et chemins de fichiers de l'application | non concluant | navin_rules 2.4.14 | partial check: permissive modes and temporary files |
| NAV-CYBER-RUN-006 Vérifier chargement dynamique et exécution de plugins non fiables | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-RUN-007 Vérifier mises à jour signées et mécanismes de rollback | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-RUN-008 Vérifier limitation de consommation mémoire, CPU et connexions | échec | navin_rules 2.4.14 | k8s/deploy.yaml:1 |
| NAV-CYBER-RUN-009 Vérifier échecs de dépendances, timeouts, retries et circuit breakers | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-RUN-010 Vérifier comportement fail-closed des opérations de sécurité critiques | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-RUN-011 Vérifier sécurité des tâches planifiées, workers, files et consumers | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |

## 17. Applications mobiles Android et iOS (0/12 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-MOBILE-001 Analyser manifests, permissions et composants exportés Android | non applicable | - | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-002 Analyser entitlements, ATS, URL schemes et universal links iOS | non applicable | - | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-003 Vérifier stockage local de secrets et données sensibles | non applicable | - | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-004 Vérifier usage Android Keystore/iOS Keychain selon le contexte | non applicable | - | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-005 Vérifier chiffrement et intégrité des données locales sensibles | non applicable | - | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-006 Contrôler TLS, validation des certificats et politiques de transport | non applicable | navin_rules 2.4.14 | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-007 Tester deep links, intents, webviews et ponts JavaScript natifs | non applicable | - | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-008 Vérifier authentification biométrique et protections de session | non applicable | - | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-009 Vérifier absence de secrets et endpoints administratifs dans APK/IPA | non applicable | - | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-010 Tester résilience à la modification des packages et instrumentation se | non applicable | - | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-011 Vérifier mécanismes de mise à jour, signature et publication | non applicable | - | no mobile surface found by discovery in 11 files read |
| NAV-CYBER-MOBILE-012 Vérifier confidentialité des SDK analytics/publicitaires et permission | non applicable | - | no mobile surface found by discovery in 11 files read |

## 18. Applications desktop, CLI, extensions et installateurs (0/11 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-DESK-001 Scanner installateurs, binaires et bibliothèques embarquées | non applicable | - | no desktop surface found by discovery in 11 files read |
| NAV-CYBER-DESK-002 Vérifier signature de code et vérification de signature au lancement/m | non applicable | - | no desktop surface found by discovery in 11 files read |
| NAV-CYBER-DESK-003 Vérifier mise à jour sécurisée contre substitution et rétrogradation | non applicable | - | no desktop surface found by discovery in 11 files read |
| NAV-CYBER-DESK-004 Vérifier permissions de fichiers, dossiers, journaux et données utilis | non applicable | navin_rules 2.4.14 | no desktop surface found by discovery in 11 files read |
| NAV-CYBER-DESK-005 Vérifier stockage des identifiants dans coffre-fort système quand disp | non applicable | - | no desktop surface found by discovery in 11 files read |
| NAV-CYBER-DESK-006 Vérifier sécurité IPC, sockets locaux, protocol handlers et localhost  | non applicable | navin_rules 2.4.14 | no desktop surface found by discovery in 11 files read |
| NAV-CYBER-DESK-007 Vérifier sécurité Electron/Tauri/WebView, navigation et isolation cont | non applicable | - | no desktop surface found by discovery in 11 files read |
| NAV-CYBER-DESK-008 Vérifier protection des commandes CLI contre injections et arguments n | non applicable | navin_dataflow 2.4.14, navin_rules 2.4.14 | no desktop surface found by discovery in 11 files read |
| NAV-CYBER-DESK-009 Vérifier sûreté des plugins, extensions et modules téléchargés | non applicable | - | no desktop surface found by discovery in 11 files read |
| NAV-CYBER-DESK-010 Vérifier élévation de privilèges et séparation des processus | non applicable | - | no desktop surface found by discovery in 11 files read |
| NAV-CYBER-DESK-011 Vérifier absence d'informations sensibles dans crash dumps et logs | non applicable | navin_rules 2.4.14 | no desktop surface found by discovery in 11 files read |

## 19. Applications IA, LLM, agents, RAG et MCP (0/17 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-AI-001 Identifier modèles, providers, prompts, tools, agents, mémoires et RAG | non applicable | navin_discovery 2.4.14 | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-002 Tester injections de prompt directes et indirectes sur contenus non fi | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-003 Tester franchissement des frontières entre instructions système et don | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-004 Vérifier que sorties du modèle sont validées avant commandes, SQL et A | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-005 Contrôler autorisations par outil avec identité et permissions du dema | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-006 Détecter excessive agency, scopes trop larges et autonomie non bornée | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-007 Vérifier sandbox des outils d'exécution de code et restrictions d'accè | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-008 Vérifier absence de secrets et PII dans prompts, traces et appels prov | non applicable | navin_rules 2.4.14 | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-009 Contrôler isolation multi-tenant de la mémoire, des embeddings et du R | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-010 Tester empoisonnement de documents, index, métadonnées et retrieval | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-011 Vérifier provenance et inspection de documents ou fichiers ingérés | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-012 Contrôler MCP: authentification, scopes, descriptions d'outils et trus | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-013 Tester tool poisoning, shadowing et substitutions de serveurs MCP | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-014 Vérifier prévention d'exfiltration via tools, URL, messages et fichier | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-015 Tester boucles autonomes, budgets, quotas, limites temporelles et kill | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-016 Vérifier journalisation et attribution utilisateur de chaque action ag | non applicable | - | no ai surface found by discovery in 11 files read |
| NAV-CYBER-AI-017 Vérifier que les actions destructrices exigent une approbation selon l | non applicable | - | no ai surface found by discovery in 11 files read |

## 20. Journalisation, surveillance, erreurs et résilience (10/10 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-OBS-001 Vérifier journaux d'authentification et d'accès aux ressources sensibl | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-002 Vérifier journaux d'administration et modifications d'autorisations | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-003 Vérifier corrélation trace-id, user-id, tenant-id et transaction-id sa | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-004 Vérifier protections contre altération et suppression des journaux d'a | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-005 Vérifier absence de secrets et données personnelles non nécessaires da | non concluant | navin_rules 2.4.14 | partial check: log statements |
| NAV-CYBER-OBS-006 Vérifier alertes sur tentatives d'accès interdites et anomalies critiq | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-007 Vérifier gestion des exceptions sans fuite d'informations sensibles | non concluant | navin_web_baseline 2.4.14, navin_rules 2.4.14 | partial check: debug mode and error pages |
| NAV-CYBER-OBS-008 Vérifier sauvegardes, restauration testée et intégrité des données | non concluant | navin_rules 2.4.14 | partial check: database snapshot flags; restore is not tested |
| NAV-CYBER-OBS-009 Vérifier dégradation contrôlée et continuité des fonctions de sécurité | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-010 Vérifier cohérence horodatage, rétention et capacité d'investigation | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |

## 21. Fonctions serverless, edge, workers et jobs (0/9 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-SRV-001 Vérifier authentification des fonctions HTTP et déclencheurs événement | non applicable | - | no serverless surface found by discovery in 11 files read |
| NAV-CYBER-SRV-002 Vérifier IAM minimal des fonctions et comptes d'exécution | non applicable | navin_rules 2.4.14 | no serverless surface found by discovery in 11 files read |
| NAV-CYBER-SRV-003 Vérifier validation des payloads de files, événements et objets | non applicable | - | no serverless surface found by discovery in 11 files read |
| NAV-CYBER-SRV-004 Vérifier idempotence, déduplication et reprise après échec | non applicable | - | no serverless surface found by discovery in 11 files read |
| NAV-CYBER-SRV-005 Vérifier sécurité des variables d'environnement et secrets runtime | non applicable | navin_rules 2.4.14 | no serverless surface found by discovery in 11 files read |
| NAV-CYBER-SRV-006 Vérifier restrictions réseau, VPC et endpoints internes | non applicable | - | no serverless surface found by discovery in 11 files read |
| NAV-CYBER-SRV-007 Vérifier protections contre déclenchement massif et dépassement de coû | non applicable | - | no serverless surface found by discovery in 11 files read |
| NAV-CYBER-SRV-008 Vérifier permissions et intégrité des packages et layers | non applicable | - | no serverless surface found by discovery in 11 files read |
| NAV-CYBER-SRV-009 Vérifier conformité des politiques de timeout, retry et dead-letter qu | non applicable | - | no serverless surface found by discovery in 11 files read |

## 22. Bibliothèques natives, firmware, WebAssembly et embarqué (0/9 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-BIN-001 Analyser code natif pour erreurs mémoire et arithmétiques | non applicable | navin_rules 2.4.14 | no native surface found by discovery in 11 files read |
| NAV-CYBER-BIN-002 Vérifier compilation durcie et protections de la plateforme | non applicable | - | no native surface found by discovery in 11 files read |
| NAV-CYBER-BIN-003 Vérifier provenance et signatures des firmwares et binaires | non applicable | - | no native surface found by discovery in 11 files read |
| NAV-CYBER-BIN-004 Vérifier intégrité et authenticité du mécanisme de mise à jour | non applicable | - | no native surface found by discovery in 11 files read |
| NAV-CYBER-BIN-005 Vérifier parser de formats binaires, paquets et protocole custom | non applicable | - | no native surface found by discovery in 11 files read |
| NAV-CYBER-BIN-006 Vérifier limitations et permissions des modules WebAssembly | non applicable | - | no native surface found by discovery in 11 files read |
| NAV-CYBER-BIN-007 Vérifier l'isolation des bibliothèques FFI et appels natifs | non applicable | - | no native surface found by discovery in 11 files read |
| NAV-CYBER-BIN-008 Vérifier absence de clés embarquées dans firmware ou ressources | non applicable | navin_rules 2.4.14 | no native surface found by discovery in 11 files read |
| NAV-CYBER-BIN-009 Vérifier fuzzing des parsers sur cible de test isolée | non applicable | - | no native surface found by discovery in 11 files read |

## 23. Intégrations et services tiers (0/10 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-INT-001 Inventorier fournisseurs externes, SDK et flux de données | non applicable | navin_discovery 2.4.14 | no intégrations surface found by discovery in 11 files read |
| NAV-CYBER-INT-002 Vérifier authentification et scopes des connecteurs | non applicable | - | no intégrations surface found by discovery in 11 files read |
| NAV-CYBER-INT-003 Vérifier validation et authentification des événements entrants | non applicable | navin_auth 2.4.14 | no intégrations surface found by discovery in 11 files read |
| NAV-CYBER-INT-004 Vérifier sorties vers domaines externes autorisés et contrôles SSRF | non applicable | navin_dataflow 2.4.14, navin_rules 2.4.14 | no intégrations surface found by discovery in 11 files read |
| NAV-CYBER-INT-005 Vérifier rotation et révocation des clés d'intégration | non applicable | - | no intégrations surface found by discovery in 11 files read |
| NAV-CYBER-INT-006 Vérifier traitement des erreurs et réponses malformées de tiers | non applicable | - | no intégrations surface found by discovery in 11 files read |
| NAV-CYBER-INT-007 Vérifier la séparation des tenants chez les intégrations et mappings d | non applicable | navin_tenant 2.4.14 | no intégrations surface found by discovery in 11 files read |
| NAV-CYBER-INT-008 Vérifier gestion des limites API, retries et déduplication | non applicable | - | no intégrations surface found by discovery in 11 files read |
| NAV-CYBER-INT-009 Vérifier implications de la désactivation d'un fournisseur ou SDK | non applicable | - | no intégrations surface found by discovery in 11 files read |
| NAV-CYBER-INT-010 Vérifier sécurité des partages de fichiers et URLs pré-signées | non applicable | - | no intégrations surface found by discovery in 11 files read |

## 24. Qualité des scans, preuve et couverture (12/12 applicables)

| Contrôle | Statut | Outils | Preuve ou raison |
|---|---|---|---|
| NAV-CYBER-QA-001 Exécuter les moteurs SAST/SCA/secret/IaC pertinents et enregistrer leu | non concluant | navin_matrix 1.0.0 | only Navin's built-in engines ran: no external scanner (semgrep, gitleaks, trivy, osv-scanner...) ran |
| NAV-CYBER-QA-002 Tester que les moteurs s'exécutent réellement et produisent des résult | réussi | navin_matrix 1.0.0 | navin_discovery 2.4.14 ok; navin_threat_model 2.4.14 ok; navin_dataflow 2.4.14 ok |
| NAV-CYBER-QA-003 Comparer surfaces détectées et éléments réellement présents dans le dé | réussi | navin_matrix 1.0.0 | files read: 11; surfaces: web, api, auth, database, multitenant, docker, kubernetes, frontend, source, data, cicd, iac, runtime |
| NAV-CYBER-QA-004 Cartographier contrôles couverts, non couverts, non applicables et blo | réussi | navin_matrix 1.0.0 | this matrix: every control has a status, a reason or evidence |
| NAV-CYBER-QA-005 Vérifier que chaque résultat est lié à un fichier, ligne, endpoint ou  | réussi | navin_matrix 1.0.0 | 45 open finding(s), each with a file, endpoint or asset |
| NAV-CYBER-QA-006 Valider les findings critiques par reproduction sûre lorsque possible | non concluant | navin_matrix 1.0.0 | 23 critical or high finding(s), 12 confirmed or reproduced; reproduce the rest with the verification engine on an authorized sandbox |
| NAV-CYBER-QA-007 Évaluer faux positifs, faux négatifs et limites des outils utilisés | non testé | - | manual review: a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-QA-008 Conserver configurations, empreintes et date des scans pour reproducti | réussi | navin_matrix 1.0.0 | audit 20261114-154505-144939 at 2026-11-14T14:45:03Z; 12 engine version(s) recorded; inputs: {"base_url": "http://127.0.0.1:33731", "database_ephemeral": true, "navin_version": "2.4.14", "offline": false, "scanners": ["b |
| NAV-CYBER-QA-009 Tester que les correctifs génèrent des tests de régression et un nouve | non concluant | navin_matrix 1.0.0 | 0 open finding(s) carry a regression test; fixes are rescanned on demand |
| NAV-CYBER-QA-010 Vérifier non-régression sur pull requests, releases et nouveaux actifs | non testé | - | no Navin engine covers it yet: no pull request or release hook re-runs the audit |
| NAV-CYBER-QA-011 Bloquer l'étiquette "audit complet" si un contrôle requis n'a pas été  | réussi | navin_matrix 1.0.0 | audit_complete is computed from the 302 controls and stays false while one is not verified |
| NAV-CYBER-QA-012 Produire un rapport machine-readable des lacunes et preuves avec respo | non concluant | navin_matrix 1.0.0 | the matrix and the gaps are exported as JSON; owners are not recorded |
