# Navin Cyber - plan de test

Généré le 2026-11-14T14:45:03Z. Chaque scénario dit ce qui est déjà testé (avec le test) et ce qui reste à construire. Règles communes: données et comptes synthétiques, bases éphémères, aucune action sur la production, aucune cible hors périmètre autorisé, aucun vrai secret dans les jeux d'essai.

## Web

| Scénario | Jeu d'essai | Test | Résultat attendu |
|---|---|---|---|
| Passif (déjà testé) | serveur local faible puis durci | `tests/test_security_verification.py::WebBaselineTest` | en-têtes, cookies, CORS, source maps, debug exposé trouvés sur le faible; rien sur le durci; seulement GET, HEAD et OPTIONS; aucune requête hors origine |
| DAST actif (à construire) | application vulnérable en préproduction isolée, ZAP baseline puis full | `tests/test_security_dast.py (à créer)` | XSS, CSRF, injection, upload retrouvés; budget de requêtes respecté; cible hors périmètre refusée |

## API

| Scénario | Jeu d'essai | Test | Résultat attendu |
|---|---|---|---|
| Authentification des routes (déjà testé) | projet Flask de test | `tests/test_security_analyzers.py::AuthTest` | routes sans authentification, JWT sans algorithmes, webhooks non signés trouvés |
| BOLA, BFLA, BOPLA (à construire) | API de test, deux comptes synthétiques, OpenAPI | `tests/test_security_authz.py (à créer)` | le compte A ne lit, ne modifie, ne supprime aucun objet de B; champs protégés refusés |
| Routes contre spécification (à construire) | API avec une route absente de l'OpenAPI | `tests/test_security_api_spec.py (à créer)` | écart signalé dans les deux sens |

## Bases de données et RLS

| Scénario | Jeu d'essai | Test | Résultat attendu |
|---|---|---|---|
| PostgreSQL / Supabase (déjà testé) | migrations faibles et saines, PostgreSQL éphémère | `tests/test_security_analyzers.py::DatabaseTest::test_ephemeral_cluster_confirms_the_static_findings` | lecture anonyme, lecture entre utilisateurs, insertion avec le propriétaire d'un autre, changement de propriétaire constatés; table saine silencieuse; COPY TO PROGRAM refusé |
| Autres moteurs, analyse statique (déjà testé) | scripts MySQL, SQL Server, Oracle, configs Redis, MongoDB, Elasticsearch, Cassandra, règles Firebase, Compose, chaînes de connexion | `tests/test_security_analyzers.py::DatabaseTest::test_other_datastores_mysql_sqlserver_oracle_nosql_configs_and_connections` | chaque règle ds-* déclenchée sur le faible, aucune sur le durci |
| Autres moteurs, base réelle (à construire) | conteneurs jetables MySQL, SQL Server, MongoDB | `tests/test_security_datastores_live.py (à créer)` | comptes, privilèges et rôles lus dans le catalogue; défauts confirmés par une requête |
| Tenants par appartenance (à construire) | schéma avec table de membres et politique par organisation | `tests/test_security_analyzers.py (à étendre)` | un membre de l'organisation 1 ne lit rien de l'organisation 2 |

## Mobile

| Scénario | Jeu d'essai | Test | Résultat attendu |
|---|---|---|---|
| Android et iOS (à construire) | APK et IPA minimaux, un composant exporté, du trafic en clair, une clé en dur | `tests/test_security_mobile.py (à créer)` | manifeste et plist analysés; chaque défaut volontaire trouvé; MobSF exécuté et sa version consignée |

## Desktop

| Scénario | Jeu d'essai | Test | Résultat attendu |
|---|---|---|---|
| Tauri et Electron (à construire) | tauri.conf.json avec CSP absente, Electron avec nodeIntegration actif, binaire non signé | `tests/test_security_desktop.py (à créer)` | chaque défaut signalé; signature vérifiée avec l'outil du système |

## Infrastructure

| Scénario | Jeu d'essai | Test | Résultat attendu |
|---|---|---|---|
| Dockerfile, Kubernetes, Terraform, GitHub Actions (déjà testé) | fichiers faibles et sains | `tests/test_security_rules.py` | règles tf-*, k8s-*, docker-*, gha-* déclenchées sur le faible, silence sur le sain |
| Helm, Kustomize, NetworkPolicy (à construire) | chart rendu puis analysé | `tests/test_security_iac_render.py (à créer)` | conteneur privilégié et absence de NetworkPolicy trouvés après rendu |

## IA, agents et MCP

| Scénario | Jeu d'essai | Test | Résultat attendu |
|---|---|---|---|
| Configuration MCP et agents (à construire) | configuration MCP avec serveur non épinglé et portée large; agent avec outil shell | `tests/test_security_ai.py (à créer)` | serveur non épinglé et portée large signalés; injection indirecte reproduite en bac à sable, outil bloqué après correction |

## Exécution de référence

Les suites de sécurité tournent avec:

```bash
NAVIN_PG_BIN=/chemin/vers/postgresql/bin .venv/bin/python -m pytest tests/test_security_catalog.py tests/test_security_analyzers.py tests/test_security_audit.py tests/test_security_scanners.py tests/test_security_reports.py tests/test_security_orchestrator.py tests/test_security_rules.py tests/test_security_verification.py -q
.venv/bin/python scripts/cyber_coverage_report.py
```

`NAVIN_PG_BIN` est facultatif: sans PostgreSQL, les tests du cluster éphémère sont ignorés et la matrice marque la lecture du catalogue comme non faite.
