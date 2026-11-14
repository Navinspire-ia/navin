# Navin Cyber - analyse des écarts sur les 302 contrôles

Généré le 2026-11-14T14:45:03Z sur Navin 2.4.14 par `scripts/cyber_coverage_report.py`. Les statuts viennent du code et des tests, pas d'une déclaration: voir `cyber-coverage-matrix.json` pour chaque contrôle et `cyber-execution-evidence/` pour les exécutions.

## Résumé chiffré

| Statut | Contrôles |
|---|---|
| Implémenté et testé | 37 |
| Implémenté, non testé | 0 |
| Partiel | 118 |
| Manquant | 146 |
| Bloqué | 1 |
| Non applicable | 0 |

**Couverture vérifiée : 12.3 %** (37 contrôles implémentés et testés sur 302 applicables). Les contrôles partiels ne comptent pas.

Parmi les 146 manquants, 82 relèvent d'une revue humaine : Navin les liste, le verdict reste humain. Aucun contrôle n'est non applicable pour l'audit de Navin lui-même : le référentiel décrit ce que Navin doit savoir vérifier, quel que soit le projet. La non-applicabilité se juge par projet, dans la matrice de chaque audit.

Lacunes par priorité: P0 91, P1 133, P2 41.

## Ce qui a été corrigé pendant cet audit (P0, faux sentiment de couverture)

- Après une passe de règles, `navin_rules` marquait comme vérifiés tous les contrôles du catalogue de 70 qui le citent, y compris le stockage mobile, les liens profonds, la limitation de débit et la journalisation d'événements, qu'aucune règle ne vérifie. Les contrôles réellement couverts sont maintenant nommés dans `navin/security/analyzers/__init__.py` (RULE_CONTROLS); les autres restent « non évalués ».
- Un contrôle n'est « réussi » dans la matrice que si un moteur de sa liste a réellement tourné et que le contrôle est automatisé. Un scanner absent donne « bloqué », jamais « réussi ».
- L'étiquette « audit complet » est refusée tant qu'un contrôle applicable n'est pas vérifié (`summary.audit_complete`).
- Les migrations d'un dépôt audité ne tournent jamais sous un superutilisateur: sans cela, `COPY ... TO PROGRAM` aurait exécuté une commande sur la machine. Le rejouage dans PostgreSQL reste optionnel (`database_ephemeral`).
- Les contrôles web dynamiques n'envoient rien sans une URL fournie par l'utilisateur, seulement GET, HEAD et OPTIONS, sans sortir de l'origine.

## Limites connues qui subsistent

- Les règles sont des motifs: un contrôle « réussi » veut dire « aucun motif connu trouvé », pas « code sûr ». L'analyse de flux n'existe que pour Python.
- Aucun scanner externe n'est installé sur cette machine (voir `cyber-execution-evidence/environment.json`): leurs adaptateurs sont testés sur des rapports enregistrés, les binaires n'ont pas été exécutés ici.
- Les tests d'isolation avec de vrais comptes (BOLA, BFLA, tenants par appartenance) et le DAST actif n'existent pas encore.
- La base de données réellement testée par requêtes est PostgreSQL/Supabase; les autres moteurs sont analysés dans leurs scripts, configurations et chaînes de connexion.

## P0 - sécurité fondamentale (91 lacunes)

failles non détectées sur les familles qui protègent les données et les accès.

### 03. Analyse statique du code et défauts techniques

11 lacunes: partiel 11.

- **À réutiliser :** Semgrep et Bandit (déjà au registre), règles Navin, analyse de flux Python; CodeQL si la licence le permet.
- **À intégrer :** analyse de flux JavaScript/TypeScript, Java, Go et C# (aujourd'hui des motifs sans flux), XQuery, ReDoS par analyse de complexité (recheck), XSS DOM, concurrence et TOCTOU.
- **Critère d'acceptation :** pour chaque langage, un projet vulnérable et un projet sain: chaque classe SAST détectée dans le premier, silence dans le second.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-SAST-003 Détecter injections LDAP, XPath, XQuery et moteurs de recherche | Partiel | LDAP and XPath patterns; no XQuery or search-engine rule |
| NAV-CYBER-SAST-005 Détecter XSS stockées, réfléchies et DOM liées aux flux de données | Partiel | sink patterns without a JavaScript dataflow |
| NAV-CYBER-SAST-008 Détecter SSRF et accès non maîtrisé aux URL, notamment aux métadonnées cloud | Partiel | Python dataflow plus patterns; cloud metadata only by literal address |
| NAV-CYBER-SAST-011 Détecter prototype pollution et manipulation dangereuse d'objets dynamiques | Partiel | one pattern rule |
| NAV-CYBER-SAST-012 Détecter redirections ouvertes et validation insuffisante des destinations | Partiel | pattern rules |
| NAV-CYBER-SAST-013 Détecter ReDoS et expressions régulières à complexité dangereuse | Partiel | regexes built from input; no complexity analysis |
| NAV-CYBER-SAST-014 Détecter débordements mémoire, use-after-free, integer overflow et accès non bor | Partiel | unsafe C functions and Rust unsafe blocks |
| NAV-CYBER-SAST-015 Détecter chemins de concurrence, data races, TOCTOU et défauts de synchronisatio | Partiel | one Python TOCTOU pattern |
| NAV-CYBER-SAST-016 Détecter manipulations de formats d'archives, Zip Slip et bombes de décompressio | Partiel | archive extraction patterns |
| NAV-CYBER-SAST-017 Détecter désactivation des contrôles de sécurité dans les branches de code | Partiel | security switches turned off in code |
| NAV-CYBER-SAST-018 Vérifier validation des entrées, encodage contextuel des sorties et typage des d | Partiel | validation is only seen as input reaching a sink |

### 04. Authentification et gestion des sessions

12 lacunes: manquant 7, partiel 5.

- **À réutiliser :** analyzers/auth.py (JWT, limitation de tentatives, webhooks, routes sans authentification).
- **À intégrer :** politique de mots de passe, MFA, récupération de compte, OAuth2/OIDC (redirect URI, PKCE, state, nonce), durée des sessions, avec comptes synthétiques sur une cible isolée.
- **Critère d'acceptation :** application de test avec deux comptes: chaque défaut volontaire (state absent, PKCE absent, session sans expiration) est trouvé et chaque contrôle sain passe.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-AUTH-001 Contrôler création de compte, validation d'email et invitation | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-002 Contrôler politique de mots de passe et stockage par hachage adapté | Partiel | hash algorithm and cost; no policy check |
| NAV-CYBER-AUTH-003 Contrôler récupération de compte, réinitialisation et changement de mot de passe | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-004 Vérifier activation, révocation et contournement éventuel de la MFA | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-005 Vérifier limites de tentatives, anti-énumération et protection contre credential | Partiel | login routes without rate limiting |
| NAV-CYBER-AUTH-006 Vérifier durée de vie, rotation et révocation des sessions et refresh tokens | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-007 Contrôler cookies Secure, HttpOnly, SameSite et fixation de session | Partiel | cookie attributes in code and on a live response |
| NAV-CYBER-AUTH-009 Contrôler OAuth2/OIDC: redirect URI, PKCE, state, nonce et échange de code | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-010 Vérifier le lien entre identité externe et compte local (account linking) | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-011 Contrôler déconnexion, invalidation de session et changement de privilèges | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTH-012 Vérifier authentification des services, workloads et tâches automatisées | Partiel | state-changing routes without an authentication check |
| NAV-CYBER-AUTH-013 Vérifier absence d'identifiants par défaut, comptes de test et chemins de contou | Partiel | default credentials, empty database passwords and bypass flags |

### 05. Autorisations et isolation des accès

14 lacunes: manquant 9, partiel 5.

- **À réutiliser :** verification/policy.py et probe.py (périmètre, lecture seule, rédaction des secrets), web_baseline.py.
- **À intégrer :** module authz_matrix: matrice rôles-ressources, deux comptes synthétiques, BOLA, BFLA, BOPLA et mass assignment rejoués depuis l'OpenAPI, isolation entre tenants.
- **Critère d'acceptation :** API de test avec une faille IDOR, une faille BFLA et un champ protégé modifiable: les trois sont trouvées, le compte A ne lit jamais l'objet de B sur la version corrigée.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-AUTHZ-001 Tester BOLA/IDOR sur chaque objet accessible par identifiant | Manquant | object-level access needs two synthetic accounts on an isolated target |
| NAV-CYBER-AUTHZ-002 Tester contrôle d'autorisation au niveau de chaque fonction et endpoint | Partiel | routes without authentication; role checks per function are not evaluated |
| NAV-CYBER-AUTHZ-003 Tester autorisation par propriété/champ (lecture et écriture) | Manquant | field-level access needs a running target |
| NAV-CYBER-AUTHZ-004 Tester élévation horizontale entre utilisateurs de même niveau | Manquant | horizontal escalation needs two accounts of the same level |
| NAV-CYBER-AUTHZ-005 Tester élévation verticale vers fonctions opérateur, support et administrateur | Partiel | missing authentication only; no role matrix |
| NAV-CYBER-AUTHZ-006 Tester isolation entre tenants, organisations, projets, entités et environnement | Partiel | static tenant scoping and policy analysis, plus cross-user SELECT / INSERT / UPDATE / DELETE in a throwaway PostgreSQL |
| NAV-CYBER-AUTHZ-007 Vérifier les contrôles d'accès côté serveur sur chaque opération sensible | Partiel | authentication on routes |
| NAV-CYBER-AUTHZ-008 Tester accès non autorisé via exports, recherche, listes et fonctionnalités batc | Manquant | exports, search and batch endpoints are not tested |
| NAV-CYBER-AUTHZ-009 Tester mass assignment et champs protégés acceptés à la création/modification | Partiel | mass assignment pattern |
| NAV-CYBER-AUTHZ-010 Contrôler l'héritage, la propagation et la révocation des rôles | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTHZ-011 Contrôler délégation, impersonation et accès support avec audit et expiration | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTHZ-012 Tester accès aux ressources liées, fichiers, commentaires et métadonnées | Manquant | related resources are not tested |
| NAV-CYBER-AUTHZ-013 Vérifier restrictions des clés API, scopes OAuth et permissions des comptes tech | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AUTHZ-014 Vérifier qu'un cache ou index de recherche ne contourne pas les règles d'accès | Manquant | caches and search indexes are not tested against access rules |

### 07. Secrets, identifiants et exposition

9 lacunes: bloqué 1, partiel 5, manquant 3.

- **À réutiliser :** gitleaks sur l'arbre et sur l'historique, Trivy, règles Navin.
- **À intégrer :** secrets dans images, binaires et bundles JS (gitleaks sur les bundles, trivy image), journaux et artefacts CI; rotation et durée de vie restent des contrôles humains.
- **Critère d'acceptation :** dépôt de test avec un secret supprimé de l'arbre mais présent dans l'historique, un secret dans un bundle: les deux sont trouvés, masqués dans le rapport.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-SECRET-002 Scanner secrets dans tout l'historique Git accessible et tags autorisés | Bloqué | gitleaks reads every commit of every ref |
| NAV-CYBER-SECRET-003 Scanner .env, fichiers de tests, exemples, notebooks et scripts | Partiel | env files and scripts; notebooks are not parsed |
| NAV-CYBER-SECRET-004 Scanner secrets dans images, binaires, bundles JS et ressources embarquées | Partiel | files in the tree only; no image layer or binary scan |
| NAV-CYBER-SECRET-005 Vérifier absence de secrets dans logs, traces, messages d'erreur et artefacts CI | Partiel | log statements and CI echo of secrets |
| NAV-CYBER-SECRET-006 Identifier clés réutilisées entre environnements et scopes trop étendus | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SECRET-007 Vérifier politique de rotation, révocation et durée de vie des secrets | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SECRET-008 Vérifier usage de gestionnaires de secrets et absence de clés dans le frontend | Partiel | keys in code and service keys in client code |
| NAV-CYBER-SECRET-009 Vérifier permissions d'accès aux secrets par service et environnement | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SECRET-011 Détecter données de connexion de production dans fixtures et sauvegardes | Partiel | connection strings in the tree |

### 09. API REST, GraphQL, gRPC et communications

13 lacunes: partiel 9, manquant 4.

- **À réutiliser :** web_baseline.py (introspection GraphQL, OpenAPI exposé, CORS), auth.py.
- **À intégrer :** comparaison routes / spécification (OpenAPI, GraphQL, protobuf), validation de schéma, limitation de débit dynamique, WebSocket, BOLA et BFLA.
- **Critère d'acceptation :** API de test dont une route manque à la spécification et une autre n'est pas protégée: écart et défaut signalés.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-API-001 Inventorier endpoints et méthodes à partir du code, schémas et runtime | Partiel | entry points from the code; no runtime inventory |
| NAV-CYBER-API-002 Comparer routes exposées aux spécifications OpenAPI/GraphQL/protobuf | Manquant | routes are not compared with an OpenAPI, GraphQL or protobuf specification |
| NAV-CYBER-API-003 Tester authentification et autorisation de chaque méthode ou resolver | Partiel | authentication per route; authorization per method is not evaluated |
| NAV-CYBER-API-004 Tester BOLA, BFLA, BOPLA et mass assignment des requêtes | Partiel | mass assignment only; no BOLA or BFLA test |
| NAV-CYBER-API-005 Contrôler validation stricte des paramètres, types, tailles et formats | Partiel | input reaching a sink; no schema validation check |
| NAV-CYBER-API-006 Contrôler pagination, limites de ressources, complexité GraphQL et profondeur | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-API-007 Contrôler rate limits, quotas et protections contre abus métier | Partiel | login rate limiting only |
| NAV-CYBER-API-008 Vérifier CORS, origines autorisées et requêtes cross-origin authentifiées | Partiel | CORS in code and on a live response |
| NAV-CYBER-API-009 Vérifier gestion des erreurs sans divulgation d'informations internes | Partiel | debug mode and error pages |
| NAV-CYBER-API-010 Vérifier sécurité des subscriptions, WebSocket et canaux temps réel | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-API-011 Vérifier signatures, idempotence et protection replay des webhooks | Partiel | signature presence; replay protection is not checked |
| NAV-CYBER-API-012 Vérifier consommation sûre des API tierces, schémas et erreurs | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-API-013 Vérifier impossibilité d'accès aux API internes/admin depuis canaux non prévus | Partiel | exposed admin and debug paths on a live target |

### 11. Bases SQL/NoSQL, RLS et sécurité des données

15 lacunes: partiel 14, manquant 1.

- **À réutiliser :** database.py, datastores.py, db_introspect.py (PostgreSQL éphémère: SELECT, INSERT, UPDATE, DELETE en anonyme et avec deux utilisateurs).
- **À intégrer :** tests réels pour MySQL/MariaDB, SQL Server, MongoDB dans des conteneurs éphémères, tenants par appartenance (table de membres), routes serveur utilisant service_role, introspection d'une base fournie depuis l'API, audit des lectures sensibles et des sauvegardes.
- **Critère d'acceptation :** pour chaque moteur, un schéma faible et un schéma sain en conteneur jetable: chaque défaut confirmé par une requête, rien sur la base saine.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-DB-001 Inventorier bases, schémas, tables, collections, vues, fonctions et triggers | Partiel | PostgreSQL objects declared in the migrations; MySQL/MariaDB, SQL Server, Oracle, MongoDB, Redis, Elasticsearch, Cassandra, Firebase, DynamoDB, Cosmos DB and ot |
| NAV-CYBER-DB-002 Inventorier rôles, utilisateurs, GRANT/REVOKE et privilèges par objet | Partiel | grants and roles in PostgreSQL, MySQL/MariaDB, SQL Server and Oracle scripts |
| NAV-CYBER-DB-003 Vérifier accès par moindre privilège et comptes séparés lecture/écriture/admin | Partiel | administrator grants and logins; separate read, write and admin accounts are not verified |
| NAV-CYBER-DB-004 Tester injection des requêtes générées par ORM, SQL brut et procédures | Partiel | SQL and NoSQL (MongoDB $where, operators) queries built from input; ORM-generated queries are not analysed |
| NAV-CYBER-DB-005 Détecter bases, consoles, ports et endpoints de données exposés | Partiel | published ports, bind addresses, default passwords and unauthenticated servers in Compose, server configs and Terraform; no network scan |
| NAV-CYBER-DB-006 Vérifier chiffrement des connexions et règles de connexion depuis l'application | Partiel | connection strings (PostgreSQL, MySQL, SQL Server, MongoDB, Redis, JDBC) and server TLS settings; a live server is not probed |
| NAV-CYBER-DB-008 Tester RLS SELECT en utilisateur normal, anonyme et utilisateurs de tenants diff | Partiel | SELECT is run in a throwaway PostgreSQL as anon and as two synthetic users on every table whose policies use auth.uid(); membership-based tenants are not simula |
| NAV-CYBER-DB-009 Tester RLS INSERT et WITH CHECK pour empêcher création dans un autre tenant | Partiel | INSERT of a row owned by another user is run in the throwaway PostgreSQL; other engines are analysed statically |
| NAV-CYBER-DB-010 Tester RLS UPDATE avec USING et WITH CHECK, y compris changement de tenant_id | Partiel | UPDATE of another user's row and owner change of one's own row are run in the throwaway PostgreSQL |
| NAV-CYBER-DB-011 Tester RLS DELETE et permissions de suppression des rôles ordinaires | Partiel | DELETE of another user's rows is run in the throwaway PostgreSQL |
| NAV-CYBER-DB-012 Vérifier politiques permissive/restrictive, combinaisons OR/AND et deny par défa | Partiel | OR and AND semantics of PostgreSQL policies |
| NAV-CYBER-DB-013 Vérifier contournements RLS via propriétaire, superuser et BYPASSRLS | Partiel | BYPASSRLS and superuser roles, disabled security policies; FORCE ROW LEVEL SECURITY is not checked |
| NAV-CYBER-DB-016 Tester routes RPC/ORM/jobs utilisant service_role ou connexion privilégiée | Partiel | service keys in client code and administrator logins in connection strings |
| NAV-CYBER-DB-017 Vérifier migrations SQL, déclencheurs, contraintes et défauts de privilèges | Partiel | grants, roles and accounts in migrations; default privileges are not parsed |
| NAV-CYBER-DB-018 Vérifier audits de lecture/écriture sensibles et protections des dumps/backups | Manquant | a human reviews it: Navin has no automated check for it yet |

### 13. Dépendances et chaîne d'approvisionnement

12 lacunes: partiel 4, manquant 8.

- **À réutiliser :** osv_scanner, trivy, pip_audit, npm_audit, cargo_audit (déjà au registre).
- **À intégrer :** SBOM CycloneDX (Syft ou Trivy), CISA KEV et EPSS, paquets abandonnés (deps.dev), typosquatting, scripts d'installation, provenance (sigstore), licences.
- **Critère d'acceptation :** projet avec une dépendance vulnérable connue exploitée, un paquet abandonné et un lockfile incohérent: trois constats, SBOM valide.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-SCA-001 Recenser dépendances directes, transitives et dépendances de build | Partiel | dependency names from the manifests; transitive dependencies need a scanner's resolution |
| NAV-CYBER-SCA-002 Produire un SBOM avec versions et provenance quand disponible | Manquant | no SBOM generator (Syft or Trivy SBOM) is integrated |
| NAV-CYBER-SCA-004 Croiser vulnérabilités exploitables connues CISA KEV, EPSS et contexte du projet | Manquant | CISA KEV and EPSS are not queried |
| NAV-CYBER-SCA-005 Identifier packages abandonnés ou non maintenus et versions obsolètes | Manquant | abandoned or unmaintained packages are not detected |
| NAV-CYBER-SCA-006 Identifier typosquatting, dependency confusion et paquets non attendus | Partiel | missing lockfile only; no typosquatting or confusion check |
| NAV-CYBER-SCA-007 Vérifier intégrité et cohérence des lockfiles et résolutions de versions | Partiel | presence of a lockfile; consistency is not checked |
| NAV-CYBER-SCA-008 Vérifier absence de dépendances flottantes sur chemins critiques | Manquant | floating versions are not detected |
| NAV-CYBER-SCA-009 Vérifier scripts d'installation et hooks potentiellement dangereux | Manquant | install scripts and hooks are not inspected |
| NAV-CYBER-SCA-010 Vérifier provenance des packages, signatures et attestations disponibles | Manquant | package provenance, signatures and attestations are not verified |
| NAV-CYBER-SCA-011 Scanner licences et obligations contractuelles selon la politique de l'organisat | Manquant | licences are not scanned |
| NAV-CYBER-SCA-012 Analyser vendored code, sous-modules et binaires tiers embarqués | Partiel | vendored directories and submodules are listed; their content is not scanned |
| NAV-CYBER-SCA-013 Suivre nouvelles vulnérabilités affectant les versions déjà déployées | Manquant | deployed versions are not monitored for new vulnerabilities |

### 24. Qualité des scans, preuve et couverture

5 lacunes: partiel 3, manquant 2.

- **À réutiliser :** appsec/matrix.py, moteur de vérification.
- **À intégrer :** crochet de pull request et de release qui relance l'audit, suivi des faux positifs, propriétaires des lacunes.
- **Critère d'acceptation :** une pull request qui ajoute une faille fait échouer le contrôle; le rapport machine liste les lacunes avec un responsable.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-QA-006 Valider les findings critiques par reproduction sûre lorsque possible | Partiel | critical findings are listed with their evidence level; safe reproduction runs on demand |
| NAV-CYBER-QA-007 Évaluer faux positifs, faux négatifs et limites des outils utilisés | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-QA-009 Tester que les correctifs génèrent des tests de régression et un nouveau scan | Partiel | the verification engine adds regression tests and rescans on demand, not on every audit |
| NAV-CYBER-QA-010 Vérifier non-régression sur pull requests, releases et nouveaux actifs | Manquant | no pull request or release hook re-runs the audit |
| NAV-CYBER-QA-012 Produire un rapport machine-readable des lacunes et preuves avec responsables | Partiel | machine-readable matrix and gaps; owners are not recorded |


## P1 - couverture renforcée (133 lacunes)

familles où la détection existe mais reste partielle ou humaine.

### 01. Découverte et cartographie

8 lacunes: partiel 6, manquant 2.

- **À réutiliser :** discovery.py (modules, sous-modules, branches, points d'entrée), Syft pour l'inventaire d'artefacts.
- **À intégrer :** lien artefact / commit / déploiement (DISC-010), composants orphelins (DISC-013), versions des frameworks résolues depuis les lockfiles (DISC-002), énumération DNS seulement sur une cible fournie (DISC-005).
- **Critère d'acceptation :** fixture monorepo à trois modules et un sous-module: chaque module, branche, point d'entrée et classe de fichier attendus; un composant sans référence est listé.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-DISC-002 Détecter chaque langage, framework, version et gestionnaire de dépendances | Partiel | languages, frameworks and manifests are detected; framework versions are not resolved |
| NAV-CYBER-DISC-003 Identifier les applications web, API, mobiles, desktop, CLI, bibliothèques et se | Partiel | module kinds come from each manifest's own signals |
| NAV-CYBER-DISC-005 Recenser les domaines, sous-domaines, URL, environnements et points de déploieme | Partiel | deploy targets are read from deployment files; no DNS or subdomain enumeration |
| NAV-CYBER-DISC-006 Découvrir les bases, schémas, files, caches, stockages objets et systèmes tiers | Partiel | databases, integrations and infrastructure files; queues and object stores only by signal |
| NAV-CYBER-DISC-007 Cartographier les flux de données, frontières de confiance et échanges interserv | Partiel | trust boundaries and scenarios are derived from the surfaces, not from a traced data flow |
| NAV-CYBER-DISC-008 Identifier les rôles humains, de service, administrateurs et leurs parcours | Partiel | role handling is only flagged (rbac signal); roles and journeys are not inventoried |
| NAV-CYBER-DISC-010 Associer artefacts compilés et versions déployées aux commits et environnements | Manquant | deployed artifacts are not tied to commits |
| NAV-CYBER-DISC-013 Détecter les surfaces non documentées et composants orphelins | Manquant | undocumented surfaces and orphan components are not compared with a reference |

### 02. Architecture et conception sécurisée

12 lacunes: partiel 7, manquant 5.

- **À réutiliser :** threat_model.py, metagraph et code_index du projet.
- **À intégrer :** graphe d'architecture exportable et revue guidée par scénario; l'essentiel reste humain, Navin fournit les scénarios et consigne la décision.
- **Critère d'acceptation :** chaque scénario de menace du modèle est lié à un test exécutable ou à une décision humaine datée.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-ARCH-001 Reconstituer l'architecture logique et les dépendances critiques | Partiel | logical map from surfaces and modules; no architecture graph |
| NAV-CYBER-ARCH-002 Identifier frontières de confiance entre clients, serveurs, données et services  | Partiel | trust boundaries from the threat model |
| NAV-CYBER-ARCH-003 Construire des scénarios de menace par fonctionnalité sensible et par rôle | Partiel | scenarios per surface, not per feature and role |
| NAV-CYBER-ARCH-004 Vérifier la séparation frontend/backend et l'absence de confiance dans le client | Partiel | client-set prices and service keys in the client |
| NAV-CYBER-ARCH-005 Vérifier les principes de moindre privilège et de refus par défaut | Partiel | deny by default is seen on database policies and route authentication only |
| NAV-CYBER-ARCH-006 Vérifier la segmentation des plans de contrôle, de données et d'administration | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-ARCH-007 Contrôler la séparation des tenants et des environnements de développement/produ | Partiel | tenant scoping of queries and policies; environments are not compared |
| NAV-CYBER-ARCH-008 Examiner les chemins de contournement des contrôles de sécurité | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-ARCH-009 Vérifier le modèle de confiance des services tiers et webhooks | Partiel | unsigned webhooks only |
| NAV-CYBER-ARCH-010 Identifier les points uniques de défaillance ayant un impact de sécurité | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-ARCH-011 Vérifier que les opérations sensibles sont journalisées et approuvées selon le r | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-ARCH-012 Vérifier la traçabilité des exigences de sécurité vers des tests exécutables | Manquant | a human reviews it: Navin has no automated check for it yet |

### 06. Logique métier, états et transactions

11 lacunes: manquant 11.

- **À réutiliser :** business_logic.py (montants et prix venant du client).
- **À intégrer :** machines à états décrites par le projet et rejouées sur une cible isolée, rafales concurrentes bornées pour la double dépense, idempotence.
- **Critère d'acceptation :** workflow de test avec un saut d'étape et une double soumission possibles: les deux sont reproduits, puis refusés après correction.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-BUS-001 Modéliser les états autorisés des processus métiers sensibles | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-002 Tester sauts d'étapes, validation prématurée et bypass d'approbation | Manquant | workflow step skipping needs scenario tests |
| NAV-CYBER-BUS-003 Tester accès ou modification après clôture, annulation ou expiration | Manquant | post-closure access needs scenario tests |
| NAV-CYBER-BUS-004 Tester doubles soumissions, replays et idempotence des opérations | Manquant | replay and idempotence need scenario tests |
| NAV-CYBER-BUS-005 Tester concurrence, courses critiques et double traitement | Manquant | concurrency needs an isolated target and concurrent requests |
| NAV-CYBER-BUS-006 Contrôler montants, unités, soldes, arrondis, bornes et valeurs négatives | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-007 Vérifier unicité, intégrité et cohérence des références métier | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-008 Tester séparation des tâches, double validation et privilèges incompatibles | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-010 Tester incohérence entre frontend, API, files et traitements différés | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-011 Vérifier protections contre abus de fonctionnalités sensibles et automatisation  | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BUS-012 Tester l'échec partiel des workflows et la restauration d'un état cohérent | Manquant | a human reviews it: Navin has no automated check for it yet |

### 08. Cryptographie et protection des échanges

9 lacunes: partiel 6, manquant 3.

- **À réutiliser :** règles de cryptographie, web_baseline.py (HSTS, redirection).
- **À intégrer :** configuration TLS d'une cible (sslyze ou testssl.sh), réutilisation de nonce, chiffrement des sauvegardes, rejeu.
- **Critère d'acceptation :** serveur de test en TLS 1.0 et avec un certificat expiré: les deux sont signalés; version saine silencieuse.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-CRYP-001 Vérifier TLS et certificats sur les flux applicatifs concernés | Partiel | TLS settings in code and database connection strings and, with a base URL, on the live site |
| NAV-CYBER-CRYP-003 Vérifier hachage des mots de passe avec sel et coût adaptés | Partiel | algorithm and cost |
| NAV-CYBER-CRYP-005 Vérifier chiffrement authentifié et gestion correcte nonce/IV | Partiel | static IV and ECB; no nonce reuse analysis |
| NAV-CYBER-CRYP-006 Vérifier signatures, validation et intégrité des objets critiques | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CRYP-007 Vérifier dérivation, stockage, rotation et séparation des clés cryptographiques | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CRYP-008 Détecter cryptographie maison ou contournement de validation TLS | Partiel | TLS validation turned off |
| NAV-CYBER-CRYP-009 Vérifier chiffrement des sauvegardes et données sensibles au repos | Partiel | Terraform encryption flags only |
| NAV-CYBER-CRYP-010 Vérifier non-divulgation de clés dans frontend, mobile, desktop et logs | Partiel | keys in the tree; binaries are not inspected |
| NAV-CYBER-CRYP-011 Vérifier résistance aux attaques de rejeu et anti-replay lorsque nécessaire | Manquant | a human reviews it: Navin has no automated check for it yet |

### 10. Applications web et tests dynamiques

11 lacunes: partiel 6, manquant 5.

- **À réutiliser :** web_baseline.py (contrôle passif GET/HEAD/OPTIONS borné).
- **À intégrer :** DAST actif en préproduction (OWASP ZAP baseline puis full) avec garde-fous: XSS, CSRF, injection, upload, cache et CDN.
- **Critère d'acceptation :** application volontairement vulnérable en préproduction isolée: chaque défaut actif retrouvé, aucune requête hors périmètre.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-WEB-001 Explorer les routes et ressources publiques avec un crawl borné | Partiel | a bounded set of paths, not a crawl |
| NAV-CYBER-WEB-004 Contrôler cookies, sessions et stockage navigateur sensibles | Partiel | cookie attributes on the probed responses |
| NAV-CYBER-WEB-005 Tester XSS réfléchies, stockées et DOM avec charges non destructrices | Manquant | active XSS payloads are not sent |
| NAV-CYBER-WEB-006 Tester CSRF sur actions qui modifient l'état | Manquant | CSRF on state-changing actions is not tested |
| NAV-CYBER-WEB-007 Tester injections et path traversal avec corpus sûr et limites strictes | Manquant | active injection and traversal payloads are not sent |
| NAV-CYBER-WEB-008 Tester upload de fichiers: types, taille, stockage, isolation et inspection | Manquant | uploads are not tested |
| NAV-CYBER-WEB-009 Tester contrôle des téléchargements et accès aux fichiers privés | Manquant | downloads and private files are not tested |
| NAV-CYBER-WEB-010 Tester sécurité CORS, postMessage et intégrations iframe | Partiel | CORS only; no postMessage or iframe test |
| NAV-CYBER-WEB-011 Vérifier protections clickjacking et open redirect | Partiel | framing headers; no open-redirect test |
| NAV-CYBER-WEB-012 Vérifier cache/CDN: absence de réponse privée partagée | Partiel | cache headers on the probed responses |
| NAV-CYBER-WEB-014 Vérifier séparation des routes d'administration et endpoints techniques | Partiel | a few well-known admin paths |

### 12. Protection des données et vie privée

10 lacunes: partiel 3, manquant 7.

- **À réutiliser :** data_signals de discovery.py.
- **À intégrer :** classification des données (Presidio ou équivalent), cartographie des exports, des caches et des index; l'essentiel reste humain.
- **Critère d'acceptation :** jeu de données synthétique avec des champs personnels: chacun classé, l'export et le cache qui le contiennent listés.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-DATA-001 Identifier données sensibles, personnelles, financières et secrets métier | Partiel | PII-like field names in sampled code and SQL |
| NAV-CYBER-DATA-002 Cartographier parcours, réplications, exports et stockage des données | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-003 Contrôler minimisation des données renvoyées par API et UI | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-004 Contrôler anonymisation et masquage des jeux de test et de démonstration | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-005 Vérifier politiques de rétention, purge et suppression autorisée | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-006 Contrôler partages de fichiers, liens signés, expiration et autorisations | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-007 Vérifier segmentation des stockages et isolation par tenant | Partiel | tenant scoping of queries |
| NAV-CYBER-DATA-008 Vérifier protections contre exfiltration via logs, analytics et télémétrie | Partiel | secrets and tokens in log statements |
| NAV-CYBER-DATA-009 Vérifier recherche/index/vector store sans accès transversal non autorisé | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DATA-010 Tester données personnelles présentes dans caches, exports et artefacts | Manquant | a human reviews it: Navin has no automated check for it yet |

### 14. Git, dépôt, build et CI/CD

11 lacunes: manquant 9, partiel 2.

- **À réutiliser :** règles GitHub Actions, outil forge du projet.
- **À intégrer :** protection des branches et CODEOWNERS via l'API de la forge, OpenSSF Scorecard, signatures de commits, provenance SLSA.
- **Critère d'acceptation :** dépôt de test sans protection de branche et avec un workflow injectable: les deux sont signalés.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-CICD-001 Vérifier protections des branches critiques et règles de revue | Manquant | branch protection needs the forge API |
| NAV-CYBER-CICD-002 Vérifier CODEOWNERS, approbations et séparation des rôles | Manquant | CODEOWNERS and approvals are not read |
| NAV-CYBER-CICD-004 Vérifier permissions minimales des tokens et intégrations CI | Partiel | GitHub Actions token permissions only |
| NAV-CYBER-CICD-005 Vérifier isolation des runners, caches et artefacts entre projets | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CICD-006 Vérifier absence de secrets dans les variables accessibles aux contributions ext | Partiel | pull_request_target workflows only |
| NAV-CYBER-CICD-007 Vérifier signatures de commits/releases si exigées par la politique | Manquant | commit and release signatures are not verified |
| NAV-CYBER-CICD-008 Vérifier provenance, signature et traçabilité des artefacts de build | Manquant | build provenance and artifact signatures are not verified |
| NAV-CYBER-CICD-009 Vérifier contrôles de sécurité bloquants avant publication et déploiement | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CICD-010 Vérifier séparation dev/test/preprod/prod et validations de production | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CICD-011 Vérifier reproductibilité ou traçabilité du build et intégrité des dépendances | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-CICD-012 Vérifier qu'une version déployée correspond au commit et à l'artefact audités | Manquant | the deployed version is not matched with the audited commit |

### 15. Cloud, containers, IaC et Kubernetes applicatif

13 lacunes: partiel 10, manquant 3.

- **À réutiliser :** règles Dockerfile, Kubernetes et Terraform; Checkov (au catalogue, hors registre); Trivy config.
- **À intégrer :** rendu Helm et Kustomize avant analyse, CloudFormation, NetworkPolicy, admission et Pod Security Standards, dérive Terraform.
- **Critère d'acceptation :** chart Helm avec un conteneur privilégié et sans NetworkPolicy: signalés après rendu.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-IAC-001 Scanner Dockerfiles et images de base pour versions vulnérables | Partiel | Dockerfile rules; image vulnerabilities need a scanner run |
| NAV-CYBER-IAC-003 Contrôler systèmes de fichiers read-only et restrictions de privilèges | Partiel | securityContext flags |
| NAV-CYBER-IAC-004 Scanner configurations Terraform, OpenTofu, CloudFormation et équivalents | Partiel | Terraform rules; CloudFormation and OpenTofu are not parsed |
| NAV-CYBER-IAC-005 Scanner Helm, Kustomize et manifests Kubernetes du projet | Partiel | manifest rules; Helm templates and Kustomize overlays are not rendered |
| NAV-CYBER-IAC-006 Vérifier RBAC Kubernetes et permissions des service accounts | Partiel | cluster-admin bindings |
| NAV-CYBER-IAC-007 Vérifier NetworkPolicies et exposition des services/ingress | Partiel | open ingress rules; NetworkPolicy presence is not checked |
| NAV-CYBER-IAC-008 Vérifier secrets Kubernetes et gestion via coffre externe si nécessaire | Partiel | secrets in manifests |
| NAV-CYBER-IAC-009 Vérifier admission policies, quotas et Pod Security Standards | Manquant | admission policies and Pod Security Standards are not checked |
| NAV-CYBER-IAC-010 Vérifier limites de ressources pour réduire risques d'épuisement | Partiel | missing resource limits |
| NAV-CYBER-IAC-012 Vérifier IAM cloud et absence de wildcard excessifs | Partiel | IAM wildcards in Terraform |
| NAV-CYBER-IAC-013 Vérifier accès aux métadonnées cloud et protections contre SSRF | Partiel | IMDSv1 and metadata literals |
| NAV-CYBER-IAC-014 Vérifier séparation des réseaux et des identités par environnement | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-IAC-015 Vérifier cohérence des configurations déployées et déclarées (drift) | Manquant | drift between declared and deployed configuration is not computed |

### 16. Configuration runtime et qualité du déploiement

11 lacunes: partiel 4, manquant 7.

- **À réutiliser :** web_baseline.py (debug, actuator), règles de configuration.
- **À intégrer :** lecture de la configuration effective d'une cible, comparaison avec l'attendue, comportement fail-closed.
- **Critère d'acceptation :** cible de test avec debug actif en production: signalé avec l'écart de configuration.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-RUN-001 Vérifier mode debug désactivé et stack traces protégées en production | Partiel | debug flags in code |
| NAV-CYBER-RUN-002 Contrôler configuration effective versus configuration attendue | Manquant | effective configuration is not read from a running target |
| NAV-CYBER-RUN-003 Vérifier validation des variables et absence de valeurs sûres remplacées en prod | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-RUN-004 Vérifier absence de routes cachées, endpoints test et consoles exposées | Partiel | actuator and debug endpoints |
| NAV-CYBER-RUN-005 Contrôler permissions système et chemins de fichiers de l'application | Partiel | permissive modes and temporary files |
| NAV-CYBER-RUN-006 Vérifier chargement dynamique et exécution de plugins non fiables | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-RUN-007 Vérifier mises à jour signées et mécanismes de rollback | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-RUN-008 Vérifier limitation de consommation mémoire, CPU et connexions | Partiel | missing resource limits |
| NAV-CYBER-RUN-009 Vérifier échecs de dépendances, timeouts, retries et circuit breakers | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-RUN-010 Vérifier comportement fail-closed des opérations de sécurité critiques | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-RUN-011 Vérifier sécurité des tâches planifiées, workers, files et consumers | Manquant | a human reviews it: Navin has no automated check for it yet |

### 19. Applications IA, LLM, agents, RAG et MCP

17 lacunes: partiel 2, manquant 15.

- **À réutiliser :** règles de clés LLM; compétence prompt-injection-defender du projet.
- **À intégrer :** analyse de la configuration MCP (serveurs non épinglés, portées trop larges), garak ou promptfoo contre un agent en bac à sable, isolation du RAG et de la mémoire, exfiltration par outils.
- **Critère d'acceptation :** agent de test avec un outil shell et un document piégé: l'injection indirecte est reproduite en bac à sable puis bloquée après correction.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-AI-001 Identifier modèles, providers, prompts, tools, agents, mémoires et RAG utilisés | Partiel | AI components are detected from dependencies and agent markers |
| NAV-CYBER-AI-002 Tester injections de prompt directes et indirectes sur contenus non fiables | Manquant | prompt injection tests need a sandboxed agent run |
| NAV-CYBER-AI-003 Tester franchissement des frontières entre instructions système et données exter | Manquant | instruction and data boundary tests need a sandboxed agent run |
| NAV-CYBER-AI-004 Vérifier que sorties du modèle sont validées avant commandes, SQL et API | Manquant | model output validation before commands, SQL and APIs is not traced |
| NAV-CYBER-AI-005 Contrôler autorisations par outil avec identité et permissions du demandeur | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AI-006 Détecter excessive agency, scopes trop larges et autonomie non bornée | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AI-007 Vérifier sandbox des outils d'exécution de code et restrictions d'accès réseau | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AI-008 Vérifier absence de secrets et PII dans prompts, traces et appels providers | Partiel | LLM provider keys committed; prompts and traces are not scanned |
| NAV-CYBER-AI-009 Contrôler isolation multi-tenant de la mémoire, des embeddings et du RAG | Manquant | memory and embedding isolation is not tested |
| NAV-CYBER-AI-010 Tester empoisonnement de documents, index, métadonnées et retrieval | Manquant | document and index poisoning is not tested |
| NAV-CYBER-AI-011 Vérifier provenance et inspection de documents ou fichiers ingérés | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AI-012 Contrôler MCP: authentification, scopes, descriptions d'outils et trust boundari | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AI-013 Tester tool poisoning, shadowing et substitutions de serveurs MCP | Manquant | MCP tool poisoning and shadowing are not tested |
| NAV-CYBER-AI-014 Vérifier prévention d'exfiltration via tools, URL, messages et fichiers | Manquant | exfiltration through tools and URLs is not tested |
| NAV-CYBER-AI-015 Tester boucles autonomes, budgets, quotas, limites temporelles et kill switch | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AI-016 Vérifier journalisation et attribution utilisateur de chaque action agentique | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-AI-017 Vérifier que les actions destructrices exigent une approbation selon la politiqu | Manquant | a human reviews it: Navin has no automated check for it yet |

### 20. Journalisation, surveillance, erreurs et résilience

10 lacunes: manquant 7, partiel 3.

- **À réutiliser :** règles de journaux sensibles.
- **À intégrer :** revue des journaux structurés et des alertes; contrôles surtout humains.
- **Critère d'acceptation :** journal de test contenant un jeton: signalé; absence d'alerte sur accès interdit consignée.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-OBS-001 Vérifier journaux d'authentification et d'accès aux ressources sensibles | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-002 Vérifier journaux d'administration et modifications d'autorisations | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-003 Vérifier corrélation trace-id, user-id, tenant-id et transaction-id sans secrets | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-004 Vérifier protections contre altération et suppression des journaux d'audit | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-005 Vérifier absence de secrets et données personnelles non nécessaires dans les log | Partiel | log statements |
| NAV-CYBER-OBS-006 Vérifier alertes sur tentatives d'accès interdites et anomalies critiques | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-007 Vérifier gestion des exceptions sans fuite d'informations sensibles | Partiel | debug mode and error pages |
| NAV-CYBER-OBS-008 Vérifier sauvegardes, restauration testée et intégrité des données | Partiel | database snapshot flags; restore is not tested |
| NAV-CYBER-OBS-009 Vérifier dégradation contrôlée et continuité des fonctions de sécurité | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-OBS-010 Vérifier cohérence horodatage, rétention et capacité d'investigation | Manquant | a human reviews it: Navin has no automated check for it yet |

### 23. Intégrations et services tiers

10 lacunes: partiel 4, manquant 6.

- **À réutiliser :** discovery.py (SDK tiers), auth.py (signature des webhooks).
- **À intégrer :** portées des connecteurs, rotation des clés, URLs pré-signées, séparation des tenants chez les tiers.
- **Critère d'acceptation :** projet avec un connecteur à portée excessive et un webhook non signé: les deux signalés.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-INT-001 Inventorier fournisseurs externes, SDK et flux de données | Partiel | third-party SDKs from manifests and imports |
| NAV-CYBER-INT-002 Vérifier authentification et scopes des connecteurs | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-INT-003 Vérifier validation et authentification des événements entrants | Partiel | signature presence on inbound webhooks |
| NAV-CYBER-INT-004 Vérifier sorties vers domaines externes autorisés et contrôles SSRF | Partiel | outbound requests built from input |
| NAV-CYBER-INT-005 Vérifier rotation et révocation des clés d'intégration | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-INT-006 Vérifier traitement des erreurs et réponses malformées de tiers | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-INT-007 Vérifier la séparation des tenants chez les intégrations et mappings d'identité | Partiel | tenant scoping of queries |
| NAV-CYBER-INT-008 Vérifier gestion des limites API, retries et déduplication | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-INT-009 Vérifier implications de la désactivation d'un fournisseur ou SDK | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-INT-010 Vérifier sécurité des partages de fichiers et URLs pré-signées | Manquant | a human reviews it: Navin has no automated check for it yet |


## P2 - surfaces spécialistes (41 lacunes)

mobile, desktop, serverless, natif: utiles quand la surface existe.

### 17. Applications mobiles Android et iOS

12 lacunes: manquant 11, partiel 1.

- **À réutiliser :** aucun moteur mobile: seulement les règles TLS du code source.
- **À intégrer :** MobSF en conteneur et apkleaks, lecture du manifeste Android (composants exportés) et du plist iOS (ATS, schémas d'URL).
- **Critère d'acceptation :** APK de test avec un composant exporté et du trafic en clair: les deux signalés.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-MOBILE-001 Analyser manifests, permissions et composants exportés Android | Manquant | no mobile analyzer (MobSF or MASTG) is integrated |
| NAV-CYBER-MOBILE-002 Analyser entitlements, ATS, URL schemes et universal links iOS | Manquant | no iOS analyzer is integrated |
| NAV-CYBER-MOBILE-003 Vérifier stockage local de secrets et données sensibles | Manquant | local storage of mobile apps is not analysed |
| NAV-CYBER-MOBILE-004 Vérifier usage Android Keystore/iOS Keychain selon le contexte | Manquant | Keychain and Keystore usage is not analysed |
| NAV-CYBER-MOBILE-005 Vérifier chiffrement et intégrité des données locales sensibles | Manquant | local data protection is not analysed |
| NAV-CYBER-MOBILE-006 Contrôler TLS, validation des certificats et politiques de transport | Partiel | TLS settings in source code; no ATS or network security config |
| NAV-CYBER-MOBILE-007 Tester deep links, intents, webviews et ponts JavaScript natifs | Manquant | deep links, intents and WebViews are not analysed |
| NAV-CYBER-MOBILE-008 Vérifier authentification biométrique et protections de session | Manquant | biometric flows are not analysed |
| NAV-CYBER-MOBILE-009 Vérifier absence de secrets et endpoints administratifs dans APK/IPA | Manquant | APK and IPA files are not scanned |
| NAV-CYBER-MOBILE-010 Tester résilience à la modification des packages et instrumentation selon risque | Manquant | package tampering resilience is not tested |
| NAV-CYBER-MOBILE-011 Vérifier mécanismes de mise à jour, signature et publication | Manquant | mobile update and signing flows are not analysed |
| NAV-CYBER-MOBILE-012 Vérifier confidentialité des SDK analytics/publicitaires et permissions | Manquant | mobile SDK privacy is not analysed |

### 18. Applications desktop, CLI, extensions et installateurs

11 lacunes: manquant 7, partiel 4.

- **À réutiliser :** règles de permissions et d'injection de commandes.
- **À intégrer :** analyse de tauri.conf.json (CSP, accès aux API) et d'Electron (nodeIntegration, contextIsolation), vérification des signatures de binaires, clés du mécanisme de mise à jour.
- **Critère d'acceptation :** configuration Tauri avec CSP absente et Electron avec nodeIntegration actif: signalées.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-DESK-001 Scanner installateurs, binaires et bibliothèques embarquées | Manquant | installers and binaries are not scanned |
| NAV-CYBER-DESK-002 Vérifier signature de code et vérification de signature au lancement/mise à jour | Manquant | code signing of an audited desktop app is not verified |
| NAV-CYBER-DESK-003 Vérifier mise à jour sécurisée contre substitution et rétrogradation | Manquant | update substitution and downgrade are not tested |
| NAV-CYBER-DESK-004 Vérifier permissions de fichiers, dossiers, journaux et données utilisateur | Partiel | permissive file modes |
| NAV-CYBER-DESK-005 Vérifier stockage des identifiants dans coffre-fort système quand disponible | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DESK-006 Vérifier sécurité IPC, sockets locaux, protocol handlers et localhost API | Partiel | services bound to all interfaces |
| NAV-CYBER-DESK-007 Vérifier sécurité Electron/Tauri/WebView, navigation et isolation contexte | Manquant | Electron and Tauri configurations are not analysed |
| NAV-CYBER-DESK-008 Vérifier protection des commandes CLI contre injections et arguments non fiables | Partiel | command injection patterns |
| NAV-CYBER-DESK-009 Vérifier sûreté des plugins, extensions et modules téléchargés | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DESK-010 Vérifier élévation de privilèges et séparation des processus | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-DESK-011 Vérifier absence d'informations sensibles dans crash dumps et logs | Partiel | sensitive data in log statements |

### 21. Fonctions serverless, edge, workers et jobs

9 lacunes: manquant 7, partiel 2.

- **À réutiliser :** règles Terraform (jokers IAM).
- **À intégrer :** analyse de serverless.yml, SAM, fonctions Terraform: authentification des déclencheurs, IAM minimal, délais, reprises et files de rebut.
- **Critère d'acceptation :** fonction de test sans authentification et avec un rôle administrateur: les deux signalées.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-SRV-001 Vérifier authentification des fonctions HTTP et déclencheurs événementiels | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SRV-002 Vérifier IAM minimal des fonctions et comptes d'exécution | Partiel | IAM wildcards in Terraform |
| NAV-CYBER-SRV-003 Vérifier validation des payloads de files, événements et objets | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SRV-004 Vérifier idempotence, déduplication et reprise après échec | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SRV-005 Vérifier sécurité des variables d'environnement et secrets runtime | Partiel | secrets in environment files |
| NAV-CYBER-SRV-006 Vérifier restrictions réseau, VPC et endpoints internes | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SRV-007 Vérifier protections contre déclenchement massif et dépassement de coûts | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-SRV-008 Vérifier permissions et intégrité des packages et layers | Manquant | layer and package permissions are not checked |
| NAV-CYBER-SRV-009 Vérifier conformité des politiques de timeout, retry et dead-letter queue | Manquant | timeout, retry and dead-letter settings are not checked |

### 22. Bibliothèques natives, firmware, WebAssembly et embarqué

9 lacunes: partiel 2, manquant 7.

- **À réutiliser :** cargo_audit, règles C et Rust unsafe.
- **À intégrer :** drapeaux de durcissement (checksec), harnais de fuzzing (cargo-fuzz, AFL++), modules WebAssembly.
- **Critère d'acceptation :** binaire de test sans PIE ni canari: signalé; parseur fuzzé 60 secondes sans plantage.
- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.

| Contrôle | Statut | Limite ou raison |
|---|---|---|
| NAV-CYBER-BIN-001 Analyser code natif pour erreurs mémoire et arithmétiques | Partiel | unsafe C functions and Rust unsafe blocks |
| NAV-CYBER-BIN-002 Vérifier compilation durcie et protections de la plateforme | Manquant | compiler hardening flags are not checked |
| NAV-CYBER-BIN-003 Vérifier provenance et signatures des firmwares et binaires | Manquant | firmware provenance is not verified |
| NAV-CYBER-BIN-004 Vérifier intégrité et authenticité du mécanisme de mise à jour | Manquant | firmware update integrity is not verified |
| NAV-CYBER-BIN-005 Vérifier parser de formats binaires, paquets et protocole custom | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BIN-006 Vérifier limitations et permissions des modules WebAssembly | Manquant | WebAssembly modules are not analysed |
| NAV-CYBER-BIN-007 Vérifier l'isolation des bibliothèques FFI et appels natifs | Manquant | a human reviews it: Navin has no automated check for it yet |
| NAV-CYBER-BIN-008 Vérifier absence de clés embarquées dans firmware ou ressources | Partiel | keys in the tree; firmware images are not unpacked |
| NAV-CYBER-BIN-009 Vérifier fuzzing des parsers sur cible de test isolée | Manquant | no fuzzing harness is integrated |


## Réponses aux questions de validation

1. **Catégories applicables entièrement manquantes :** aucune. Sans aucun contrôle automatisé et testé (seulement du partiel) : 02. Architecture et conception sécurisée, 05. Autorisations et isolation des accès, 12. Protection des données et vie privée, 16. Configuration runtime et qualité du déploiement, 17. Applications mobiles Android et iOS, 18. Applications desktop, CLI, extensions et installateurs, 19. Applications IA, LLM, agents, RAG et MCP, 20. Journalisation, surveillance, erreurs et résilience, 21. Fonctions serverless, edge, workers et jobs, 22. Bibliothèques natives, firmware, WebAssembly et embarqué, 23. Intégrations et services tiers.
2. **Contrôles marqués couverts sans preuve :** 0 aujourd'hui. Avant cet audit, une passe de règles déclarait aussi vérifiés des contrôles qu'aucune règle ne vérifie, ou si peu qu'un « clean » était trompeur (une seule règle de TOCTOU, la seule présence d'un lockfile) : C1.08, C1.09, C2.05, C3.02, C3.03, C3.10, C4.02, C5.01, C5.02, C5.03, C5.04, C5.05, C5.06, C5.07, C5.08, C5.09, C5.10, C6.09, C6.10, C7.01, C7.04, C7.05.
3. **Lecture seule ou environnement isolé :** 55 contrôles en lecture seule, 233 hybrides, 14 actifs en préproduction. Entrées nécessaires : URL autorisée pour 15, comptes synthétiques pour 6, dépôt git pour 1; le rejouage des migrations est une option explicite (`database_ephemeral`).
4. **Rôles réels pour la RLS :** PostgreSQL local jetable, migrations appliquées par un rôle sans superutilisateur ni BYPASSRLS, puis SELECT, INSERT, UPDATE et DELETE sous `anon` et sous `authenticated` avec deux utilisateurs synthétiques (claims JWT simulés), le tout annulé par ROLLBACK. Résultat sur le projet synthétique : public.notes: seeded 2, anon_read 0, own_read 1, other_read 0, other_update 0, other_delete 0; public.posts: seeded 2, anon_read 2, own_read 1, other_read 1, other_update 0, other_delete 0, tenant_change 1.
5. **Déduplication, vérification, correctif :** les constats sont fusionnés par empreinte (famille, CWE, fichier, bloc de lignes, composant); un constat statique confirmé par la base éphémère ou par le contrôle web passe à confirmé, à exploitable quand la requête le reproduit. Le moteur de vérification ne ferme un constat qu'avec un nouveau scan et un test de régression, à la demande (NAV-CYBER-QA-009 reste partiel).
6. **Tous les modules d'un monorepo :** la découverte liste modules, espaces de travail et sous-modules; elle lit au plus 4000 fichiers et le dit (`truncated`). Sur ce dépôt, elle s'est arrêtée à 4000 fichiers : NAV-CYBER-QA-003 ressort alors non concluant, jamais réussi.
7. **Contrôles non automatisables :** 82 contrôles sont marqués `manual_review` dans les deux matrices; ils ne passent jamais à réussi automatiquement.
8. **Scanners intégrés, installés, exécutés :** 9 au registre (tableau ci-dessous); installés sur cette machine : npm_audit. Chaque audit enregistre pour chacun son état et sa version.

## Scanners au registre

| Scanner | Binaire | Licence | Familles | Installé ici | Version |
|---|---|---|---|---|---|
| semgrep | semgrep | LGPL-2.1 | injection, code_execution, files, cryptography, authentication | non | - |
| gitleaks | gitleaks | MIT | secrets | non | - |
| gitleaks_history | gitleaks | MIT | secrets | non | - |
| trivy | trivy | Apache-2.0 | supply_chain, infrastructure, secrets | non | - |
| osv_scanner | osv-scanner | Apache-2.0 | supply_chain | non | - |
| bandit | bandit | Apache-2.0 | injection, code_execution, cryptography, secrets | non | - |
| pip_audit | pip-audit | Apache-2.0 | supply_chain | non | - |
| npm_audit | npm | Artistic-2.0 | supply_chain | oui | 11.16.0 |
| cargo_audit | cargo-audit | Apache-2.0 OR MIT | supply_chain | non | - |

## Audit de ce dépôt

Navin a audité son propre dépôt sans scanner externe en 101.6 s : 139 constats (high 80, critical 12, medium 11, low 30, info 6), couverture vérifiée 20.3 % des contrôles applicables. Ce sont des signaux à trier (une partie vient des jeux d'essai volontairement vulnérables des tests), pas un verdict.
