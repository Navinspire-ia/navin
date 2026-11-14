# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Cyber AppSec referential: 302 vérification controls in 24 categories.

The referential is the yardstick an audit is measured against, not à list of
things Navin claims to check: ``capabilities`` says what Navin really does for
each control and ``matrix`` turns one audit into a status per control with
its evidence. Control ids are ``NAV-CYBER-<PREFIX>-<NNN>``, numbered in the
order below. Task texts are the French wording of the referential (version
1.0.0, 2026-11-14); category names ship in French, English and Arabic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REFERENTIAL_ID = "navin-cyber-appsec"
VERSION = "1.0.0"
UPDATED = "2026-11-14"
TITLE = "Navin Cyber - Catalogue de vérification Application Security"
STATUS_ENUM: tuple[str, ...] = ("not_tested", "passed", "failed", "inconclusive", "blocked", "not_applicable")
APPLICABILITY_TAGS: tuple[str, ...] = (
    "all", "source", "auth", "api", "web", "db", "data", "cicd", "iac", "runtime",
    "mobile", "desktop", "ai", "serverless", "native", "intégrations",
)
VERIFICATION_MODES: tuple[str, ...] = ("read_only", "hybrid", "staging_active")
COVERAGE_RULE = (
    "Un contrôle ne compte comme couvert que si son exécution et les preuves sont disponibles; "
    "non-applicable doit être justifié."
)
SAFETY = (
    "Pas de scan actif sur production sans mandat et garde-fous; jamais de tests destructifs, de modification "
    "directe de base, ni de vraies données clients pour les tests d'isolement."
)
REFERENCES: dict[str, str] = {
    "OWASP ASVS 5.0": "https://asvs.owasp.org/",
    "OWASP Top 10 2025": "https://top10.owasp.org/",
    "OWASP WSTG": "https://wstg.owasp.org/",
    "OWASP API Security": "https://api-security.owasp.org/",
    "OWASP MASVS": "https://mas.owasp.org/MASVS/",
    "OWASP LLM Top 10": "https://genai.owasp.org/llm-top-10/",
    "OWASP MCP Top 10": "https://owasp.org/projects/mcp-top-10",
    "NIST SSDF": "https://csrc.nist.gov/pubs/sp/800/218/final",
    "PostgreSQL RLS": "https://www.postgresql.org/docs/current/ddl-rowsecurity.html",
    "Supabase RLS": "https://supabase.com/docs/guides/database/postgres/row-level-security",
    "OWASP SAMM": "https://owaspsamm.org/model/",
}


@dataclass(frozen=True)
class Category:
    id: str  # id prefix, e.g. "SAST"
    number: int
    name: dict[str, str]
    applicability: str
    verification_mode: str
    tool_family: str
    tasks: tuple[str, ...]


@dataclass(frozen=True)
class Control:
    id: str
    category: str  # Category.id
    number: int
    task: str
    applicability: str
    verification_mode: str
    tool_family: str


def _n(fr: str, en: str, ar: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "ar": ar}


CATEGORIES: tuple[Category, ...] = (
    Category("DISC", 1, _n("Découverte et cartographie", "Discovery and mapping", "الاكتشاف ورسم الخرائط"),
             "all", "read_only", "inventaire statique, parsers, SBOM, manifests", (
                 "Recenser tous les dépôts, sous-modules, branches et monorepos",
                 "Détecter chaque langage, framework, version et gestionnaire de dépendances",
                 "Identifier les applications web, API, mobiles, desktop, CLI, bibliothèques et services",
                 "Inventorier les points d'entrée HTTP, RPC, WebSocket, GraphQL, workers et tâches planifiées",
                 "Recenser les domaines, sous-domaines, URL, environnements et points de déploiement autorisés",
                 "Découvrir les bases, schémas, files, caches, stockages objets et systèmes tiers",
                 "Cartographier les flux de données, frontières de confiance et échanges interservices",
                 "Identifier les rôles humains, de service, administrateurs et leurs parcours",
                 "Localiser les fichiers de configuration, secrets, scripts, migrations et manifestes",
                 "Associer artefacts compilés et versions déployées aux commits et environnements",
                 "Distinguer code applicatif, code généré, exemples, tests et dépendances vendorisées",
                 "Établir pour chaque contrôle le périmètre autorisé, les exclusions et les preuves disponibles",
                 "Détecter les surfaces non documentées et composants orphelins",
             )),
    Category("ARCH", 2, _n("Architecture et conception sécurisée", "Secure architecture and design", "البنية والتصميم الآمن"),
             "all", "hybrid", "graphe architecture, revue guidee, threat modeling", (
                 "Reconstituer l'architecture logique et les dépendances critiques",
                 "Identifier frontières de confiance entre clients, serveurs, données et services tiers",
                 "Construire des scénarios de menace par fonctionnalité sensible et par rôle",
                 "Vérifier la séparation frontend/backend et l'absence de confiance dans le client",
                 "Vérifier les principes de moindre privilège et de refus par défaut",
                 "Vérifier la segmentation des plans de contrôle, de données et d'administration",
                 "Contrôler la séparation des tenants et des environnements de développement/production",
                 "Examiner les chemins de contournement des contrôles de sécurité",
                 "Vérifier le modèle de confiance des services tiers et webhooks",
                 "Identifier les points uniques de défaillance ayant un impact de sécurité",
                 "Vérifier que les opérations sensibles sont journalisées et approuvées selon le risque",
                 "Vérifier la traçabilité des exigences de sécurité vers des tests exécutables",
             )),
    Category("SAST", 3, _n("Analyse statique du code et défauts techniques", "Static code analysis and technical flaws",
                           "التحليل الثابت للشيفرة والعيوب التقنية"),
             "source", "read_only", "Semgrep/CodeQL, linters sécurité, analyse de flux", (
                 "Détecter injections SQL, NoSQL et requêtes construites par concaténation",
                 "Détecter injections de commandes système et paramètres shell non maîtrisés",
                 "Détecter injections LDAP, XPath, XQuery et moteurs de recherche",
                 "Détecter injections de templates, expressions et interpréteurs",
                 "Détecter XSS stockées, réfléchies et DOM liées aux flux de données",
                 "Détecter désérialisation non sûre et chargement de types arbitraires",
                 "Détecter traversée de chemins, lecture/écriture arbitraire et inclusion de fichiers",
                 "Détecter SSRF et accès non maîtrisé aux URL, notamment aux métadonnées cloud",
                 "Détecter XXE et parsers XML non sécurisés",
                 "Détecter exécution de code dynamique, eval et chargement de modules non maîtrisé",
                 "Détecter prototype pollution et manipulation dangereuse d'objets dynamiques",
                 "Détecter redirections ouvertes et validation insuffisante des destinations",
                 "Détecter ReDoS et expressions régulières à complexité dangereuse",
                 "Détecter débordements mémoire, use-after-free, integer overflow et accès non bornés si langage concerné",
                 "Détecter chemins de concurrence, data races, TOCTOU et défauts de synchronisation",
                 "Détecter manipulations de formats d'archives, Zip Slip et bombes de décompression",
                 "Détecter désactivation des contrôles de sécurité dans les branches de code",
                 "Vérifier validation des entrées, encodage contextuel des sorties et typage des données",
             )),
    Category("AUTH", 4, _n("Authentification et gestion des sessions", "Authentication and session management", "المصادقة وإدارة الجلسات"),
             "auth", "hybrid", "tests de comptes et sessions isolés, revue config", (
                 "Contrôler création de compte, validation d'email et invitation",
                 "Contrôler politique de mots de passe et stockage par hachage adapté",
                 "Contrôler récupération de compte, réinitialisation et changement de mot de passe",
                 "Vérifier activation, révocation et contournement éventuel de la MFA",
                 "Vérifier limites de tentatives, anti-énumération et protection contre credential stuffing",
                 "Vérifier durée de vie, rotation et révocation des sessions et refresh tokens",
                 "Contrôler cookies Secure, HttpOnly, SameSite et fixation de session",
                 "Valider JWT: algorithmes autorisés, signatures, audiences, issuers, expiration et clés",
                 "Contrôler OAuth2/OIDC: redirect URI, PKCE, state, nonce et échange de code",
                 "Vérifier le lien entre identité externe et compte local (account linking)",
                 "Contrôler déconnexion, invalidation de session et changement de privilèges",
                 "Vérifier authentification des services, workloads et tâches automatisées",
                 "Vérifier absence d'identifiants par défaut, comptes de test et chemins de contournement",
                 "Contrôler authentification et validation des signatures des callbacks/webhooks",
             )),
    Category("AUTHZ", 5, _n("Autorisations et isolation des accès", "Authorization and access isolation", "التفويض وعزل الوصول"),
             "auth", "hybrid", "matrice rôles-ressources, tests multi-comptes isolés", (
                 "Tester BOLA/IDOR sur chaque objet accessible par identifiant",
                 "Tester contrôle d'autorisation au niveau de chaque fonction et endpoint",
                 "Tester autorisation par propriété/champ (lecture et écriture)",
                 "Tester élévation horizontale entre utilisateurs de même niveau",
                 "Tester élévation verticale vers fonctions opérateur, support et administrateur",
                 "Tester isolation entre tenants, organisations, projets, entités et environnements",
                 "Vérifier les contrôles d'accès côté serveur sur chaque opération sensible",
                 "Tester accès non autorisé via exports, recherche, listes et fonctionnalités batch",
                 "Tester mass assignment et champs protégés acceptés à la création/modification",
                 "Contrôler l'héritage, la propagation et la révocation des rôles",
                 "Contrôler délégation, impersonation et accès support avec audit et expiration",
                 "Tester accès aux ressources liées, fichiers, commentaires et métadonnées",
                 "Vérifier restrictions des clés API, scopes OAuth et permissions des comptes techniques",
                 "Vérifier qu'un cache ou index de recherche ne contourne pas les règles d'accès",
             )),
    Category("BUS", 6, _n("Logique métier, états et transactions", "Business logic, states and transactions", "منطق الأعمال والحالات والمعاملات"),
             "all", "hybrid", "tests métiers, scénarios de transitions et concurrence contrôles", (
                 "Modéliser les états autorisés des processus métiers sensibles",
                 "Tester sauts d'étapes, validation prématurée et bypass d'approbation",
                 "Tester accès ou modification après clôture, annulation ou expiration",
                 "Tester doubles soumissions, replays et idempotence des opérations",
                 "Tester concurrence, courses critiques et double traitement",
                 "Contrôler montants, unités, soldes, arrondis, bornes et valeurs négatives",
                 "Vérifier unicité, intégrité et cohérence des références métier",
                 "Tester séparation des tâches, double validation et privilèges incompatibles",
                 "Tester falsification de prix, quotas, limites et paramètres métier venant du client",
                 "Tester incohérence entre frontend, API, files et traitements différés",
                 "Vérifier protections contre abus de fonctionnalités sensibles et automatisation massive",
                 "Tester l'échec partiel des workflows et la restauration d'un état cohérent",
             )),
    Category("SECRET", 7, _n("Secrets, identifiants et exposition", "Secrets, credentials and exposure", "الأسرار وبيانات الاعتماد والتعرض"),
             "source", "read_only", "Gitleaks/TruffleHog, gestion secrets", (
                 "Scanner secrets dans les fichiers source et de configuration",
                 "Scanner secrets dans tout l'historique Git accessible et tags autorisés",
                 "Scanner .env, fichiers de tests, exemples, notebooks et scripts",
                 "Scanner secrets dans images, binaires, bundles JS et ressources embarquées",
                 "Vérifier absence de secrets dans logs, traces, messages d'erreur et artefacts CI",
                 "Identifier clés réutilisées entre environnements et scopes trop étendus",
                 "Vérifier politique de rotation, révocation et durée de vie des secrets",
                 "Vérifier usage de gestionnaires de secrets et absence de clés dans le frontend",
                 "Vérifier permissions d'accès aux secrets par service et environnement",
                 "Détecter certificats et clés privées divulgués, expirés ou mal protégés",
                 "Détecter données de connexion de production dans fixtures et sauvegardes",
             )),
    Category("CRYP", 8, _n("Cryptographie et protection des échanges", "Cryptography and transport protection", "التشفير وحماية التبادلات"),
             "all", "hybrid", "analyse statique, revue bibliothèques crypto et configs TLS", (
                 "Vérifier TLS et certificats sur les flux applicatifs concernés",
                 "Rechercher algorithmes obsolètes et paramètres cryptographiques faibles",
                 "Vérifier hachage des mots de passe avec sel et coût adaptés",
                 "Vérifier usage d'aléa cryptographique approprié pour jetons et clés",
                 "Vérifier chiffrement authentifié et gestion correcte nonce/IV",
                 "Vérifier signatures, validation et intégrité des objets critiques",
                 "Vérifier dérivation, stockage, rotation et séparation des clés cryptographiques",
                 "Détecter cryptographie maison ou contournement de validation TLS",
                 "Vérifier chiffrement des sauvegardes et données sensibles au repos",
                 "Vérifier non-divulgation de clés dans frontend, mobile, desktop et logs",
                 "Vérifier résistance aux attaques de rejeu et anti-replay lorsque nécessaire",
             )),
    Category("API", 9, _n("API REST, GraphQL, gRPC et communications", "REST, GraphQL, gRPC APIs and communications",
                          "واجهات REST وGraphQL وgRPC والاتصالات"),
             "api", "hybrid", "schémas OpenAPI, tests API en préproduction", (
                 "Inventorier endpoints et méthodes à partir du code, schémas et runtime",
                 "Comparer routes exposées aux spécifications OpenAPI/GraphQL/protobuf",
                 "Tester authentification et autorisation de chaque méthode ou resolver",
                 "Tester BOLA, BFLA, BOPLA et mass assignment des requêtes",
                 "Contrôler validation stricte des paramètres, types, tailles et formats",
                 "Contrôler pagination, limites de ressources, complexité GraphQL et profondeur",
                 "Contrôler rate limits, quotas et protections contre abus métier",
                 "Vérifier CORS, origines autorisées et requêtes cross-origin authentifiées",
                 "Vérifier gestion des erreurs sans divulgation d'informations internes",
                 "Vérifier sécurité des subscriptions, WebSocket et canaux temps réel",
                 "Vérifier signatures, idempotence et protection replay des webhooks",
                 "Vérifier consommation sûre des API tierces, schémas et erreurs",
                 "Vérifier impossibilité d'accès aux API internes/admin depuis canaux non prévus",
                 "Vérifier protection SSRF des endpoints prenant des URLs en entrée",
             )),
    Category("WEB", 10, _n("Applications web et tests dynamiques", "Web applications and dynamic testing", "تطبيقات الويب والاختبارات الديناميكية"),
             "web", "staging_active", "OWASP ZAP baseline; DAST actif seulement en préproduction autorisée", (
                 "Explorer les routes et ressources publiques avec un crawl borné",
                 "Contrôler TLS, HSTS et redirections HTTP vers HTTPS",
                 "Contrôler en-têtes CSP, X-Content-Type-Options et politique de framing",
                 "Contrôler cookies, sessions et stockage navigateur sensibles",
                 "Tester XSS réfléchies, stockées et DOM avec charges non destructrices",
                 "Tester CSRF sur actions qui modifient l'état",
                 "Tester injections et path traversal avec corpus sûr et limites strictes",
                 "Tester upload de fichiers: types, taille, stockage, isolation et inspection",
                 "Tester contrôle des téléchargements et accès aux fichiers privés",
                 "Tester sécurité CORS, postMessage et intégrations iframe",
                 "Vérifier protections clickjacking et open redirect",
                 "Vérifier cache/CDN: absence de réponse privée partagée",
                 "Vérifier source maps, mode debug et ressources internes exposées",
                 "Vérifier séparation des routes d'administration et endpoints techniques",
             )),
    Category("DB", 11, _n("Bases SQL/NoSQL, RLS et sécurité des données", "SQL/NoSQL databases, RLS and data security",
                          "قواعد بيانات SQL/NoSQL وRLS وأمن البيانات"),
             "db", "hybrid", "introspection read-only, pgTAP / tests rôles en base ephemere", (
                 "Inventorier bases, schémas, tables, collections, vues, fonctions et triggers",
                 "Inventorier rôles, utilisateurs, GRANT/REVOKE et privilèges par objet",
                 "Vérifier accès par moindre privilège et comptes séparés lecture/écriture/admin",
                 "Tester injection des requêtes générées par ORM, SQL brut et procédures",
                 "Détecter bases, consoles, ports et endpoints de données exposés",
                 "Vérifier chiffrement des connexions et règles de connexion depuis l'application",
                 "Vérifier activation de RLS sur tables exposées lorsqu'applicable",
                 "Tester RLS SELECT en utilisateur normal, anonyme et utilisateurs de tenants différents",
                 "Tester RLS INSERT et WITH CHECK pour empêcher création dans un autre tenant",
                 "Tester RLS UPDATE avec USING et WITH CHECK, y compris changement de tenant_id",
                 "Tester RLS DELETE et permissions de suppression des rôles ordinaires",
                 "Vérifier politiques permissive/restrictive, combinaisons OR/AND et deny par défaut",
                 "Vérifier contournements RLS via propriétaire, superuser et BYPASSRLS",
                 "Contrôler fonctions SECURITY DEFINER, search_path et droits EXECUTE",
                 "Contrôler vues security_invoker et droits des vues matérialisées/exposées",
                 "Tester routes RPC/ORM/jobs utilisant service_role ou connexion privilégiée",
                 "Vérifier migrations SQL, déclencheurs, contraintes et défauts de privilèges",
                 "Vérifier audits de lecture/écriture sensibles et protections des dumps/backups",
             )),
    Category("DATA", 12, _n("Protection des données et vie privée", "Data protection and privacy", "حماية البيانات والخصوصية"),
             "data", "hybrid", "classification, inspection des flux autorisés", (
                 "Identifier données sensibles, personnelles, financières et secrets métier",
                 "Cartographier parcours, réplications, exports et stockage des données",
                 "Contrôler minimisation des données renvoyées par API et UI",
                 "Contrôler anonymisation et masquage des jeux de test et de démonstration",
                 "Vérifier politiques de rétention, purge et suppression autorisée",
                 "Contrôler partages de fichiers, liens signés, expiration et autorisations",
                 "Vérifier segmentation des stockages et isolation par tenant",
                 "Vérifier protections contre exfiltration via logs, analytics et télémétrie",
                 "Vérifier recherche/index/vector store sans accès transversal non autorisé",
                 "Tester données personnelles présentes dans caches, exports et artefacts",
             )),
    Category("SCA", 13, _n("Dépendances et chaîne d'approvisionnement", "Dependencies and supply chain", "التبعيات وسلسلة التوريد"),
             "source", "read_only", "OSV-Scanner, Grype, Trivy, Syft/SBOM", (
                 "Recenser dépendances directes, transitives et dépendances de build",
                 "Produire un SBOM avec versions et provenance quand disponible",
                 "Faire la correspondance avec CVE, avis fournisseurs et bases de vulnérabilités",
                 "Croiser vulnérabilités exploitables connues CISA KEV, EPSS et contexte du projet",
                 "Identifier packages abandonnés ou non maintenus et versions obsolètes",
                 "Identifier typosquatting, dependency confusion et paquets non attendus",
                 "Vérifier intégrité et cohérence des lockfiles et résolutions de versions",
                 "Vérifier absence de dépendances flottantes sur chemins critiques",
                 "Vérifier scripts d'installation et hooks potentiellement dangereux",
                 "Vérifier provenance des packages, signatures et attestations disponibles",
                 "Scanner licences et obligations contractuelles selon la politique de l'organisation",
                 "Analyser vendored code, sous-modules et binaires tiers embarqués",
                 "Suivre nouvelles vulnérabilités affectant les versions déjà déployées",
             )),
    Category("CICD", 14, _n("Git, dépôt, build et CI/CD", "Git, repository, build and CI/CD", "Git والمستودع والبناء وCI/CD"),
             "cicd", "hybrid", "analyse Git et pipelines, OpenSSF Scorecard", (
                 "Vérifier protections des branches critiques et règles de revue",
                 "Vérifier CODEOWNERS, approbations et séparation des rôles",
                 "Vérifier protections contre injection dans scripts de pipeline",
                 "Vérifier permissions minimales des tokens et intégrations CI",
                 "Vérifier isolation des runners, caches et artefacts entre projets",
                 "Vérifier absence de secrets dans les variables accessibles aux contributions externes",
                 "Vérifier signatures de commits/releases si exigées par la politique",
                 "Vérifier provenance, signature et traçabilité des artefacts de build",
                 "Vérifier contrôles de sécurité bloquants avant publication et déploiement",
                 "Vérifier séparation dev/test/preprod/prod et validations de production",
                 "Vérifier reproductibilité ou traçabilité du build et intégrité des dépendances",
                 "Vérifier qu'une version déployée correspond au commit et à l'artefact audités",
             )),
    Category("IAC", 15, _n("Cloud, containers, IaC et Kubernetes applicatif", "Cloud, containers, IaC and application Kubernetes",
                           "السحابة والحاويات وIaC وKubernetes"),
             "iac", "hybrid", "Checkov, Trivy config, Kubernetes policy", (
                 "Scanner Dockerfiles et images de base pour versions vulnérables",
                 "Vérifier exécution non-root et capacités Linux minimales des conteneurs",
                 "Contrôler systèmes de fichiers read-only et restrictions de privilèges",
                 "Scanner configurations Terraform, OpenTofu, CloudFormation et équivalents",
                 "Scanner Helm, Kustomize et manifests Kubernetes du projet",
                 "Vérifier RBAC Kubernetes et permissions des service accounts",
                 "Vérifier NetworkPolicies et exposition des services/ingress",
                 "Vérifier secrets Kubernetes et gestion via coffre externe si nécessaire",
                 "Vérifier admission policies, quotas et Pod Security Standards",
                 "Vérifier limites de ressources pour réduire risques d'épuisement",
                 "Vérifier buckets, volumes, DB cloud et stockages exposés publiquement",
                 "Vérifier IAM cloud et absence de wildcard excessifs",
                 "Vérifier accès aux métadonnées cloud et protections contre SSRF",
                 "Vérifier séparation des réseaux et des identités par environnement",
                 "Vérifier cohérence des configurations déployées et déclarées (drift)",
             )),
    Category("RUN", 16, _n("Configuration runtime et qualité du déploiement", "Runtime configuration and deployment quality",
                           "إعدادات التشغيل وجودة النشر"),
             "runtime", "hybrid", "revue configuration et analyse runtime non intrusive", (
                 "Vérifier mode debug désactivé et stack traces protégées en production",
                 "Contrôler configuration effective versus configuration attendue",
                 "Vérifier validation des variables et absence de valeurs sûres remplacées en prod",
                 "Vérifier absence de routes cachées, endpoints test et consoles exposées",
                 "Contrôler permissions système et chemins de fichiers de l'application",
                 "Vérifier chargement dynamique et exécution de plugins non fiables",
                 "Vérifier mises à jour signées et mécanismes de rollback",
                 "Vérifier limitation de consommation mémoire, CPU et connexions",
                 "Vérifier échecs de dépendances, timeouts, retries et circuit breakers",
                 "Vérifier comportement fail-closed des opérations de sécurité critiques",
                 "Vérifier sécurité des tâches planifiées, workers, files et consumers",
             )),
    Category("MOBILE", 17, _n("Applications mobiles Android et iOS", "Android and iOS mobile applications", "تطبيقات الجوال Android وiOS"),
             "mobile", "hybrid", "MobSF, MASVS/MASTG, simulateur de test", (
                 "Analyser manifests, permissions et composants exportés Android",
                 "Analyser entitlements, ATS, URL schemes et universal links iOS",
                 "Vérifier stockage local de secrets et données sensibles",
                 "Vérifier usage Android Keystore/iOS Keychain selon le contexte",
                 "Vérifier chiffrement et intégrité des données locales sensibles",
                 "Contrôler TLS, validation des certificats et politiques de transport",
                 "Tester deep links, intents, webviews et ponts JavaScript natifs",
                 "Vérifier authentification biométrique et protections de session",
                 "Vérifier absence de secrets et endpoints administratifs dans APK/IPA",
                 "Tester résilience à la modification des packages et instrumentation selon risque",
                 "Vérifier mécanismes de mise à jour, signature et publication",
                 "Vérifier confidentialité des SDK analytics/publicitaires et permissions",
             )),
    Category("DESK", 18, _n("Applications desktop, CLI, extensions et installateurs", "Desktop applications, CLI, extensions and installers",
                            "تطبيقات سطح المكتب وواجهات الأوامر والإضافات والمثبتات"),
             "desktop", "hybrid", "analyse binaires et packaging isolée", (
                 "Scanner installateurs, binaires et bibliothèques embarquées",
                 "Vérifier signature de code et vérification de signature au lancement/mise à jour",
                 "Vérifier mise à jour sécurisée contre substitution et rétrogradation",
                 "Vérifier permissions de fichiers, dossiers, journaux et données utilisateur",
                 "Vérifier stockage des identifiants dans coffre-fort système quand disponible",
                 "Vérifier sécurité IPC, sockets locaux, protocol handlers et localhost API",
                 "Vérifier sécurité Electron/Tauri/WebView, navigation et isolation contexte",
                 "Vérifier protection des commandes CLI contre injections et arguments non fiables",
                 "Vérifier sûreté des plugins, extensions et modules téléchargés",
                 "Vérifier élévation de privilèges et séparation des processus",
                 "Vérifier absence d'informations sensibles dans crash dumps et logs",
             )),
    Category("AI", 19, _n("Applications IA, LLM, agents, RAG et MCP", "AI, LLM, agents, RAG and MCP applications",
                          "تطبيقات الذكاء الاصطناعي والنماذج اللغوية والوكلاء وRAG وMCP"),
             "ai", "hybrid", "OWASP LLM/MCP, tests de sécurité agentique en bac à sable", (
                 "Identifier modèles, providers, prompts, tools, agents, mémoires et RAG utilisés",
                 "Tester injections de prompt directes et indirectes sur contenus non fiables",
                 "Tester franchissement des frontières entre instructions système et données externes",
                 "Vérifier que sorties du modèle sont validées avant commandes, SQL et API",
                 "Contrôler autorisations par outil avec identité et permissions du demandeur",
                 "Détecter excessive agency, scopes trop larges et autonomie non bornée",
                 "Vérifier sandbox des outils d'exécution de code et restrictions d'accès réseau",
                 "Vérifier absence de secrets et PII dans prompts, traces et appels providers",
                 "Contrôler isolation multi-tenant de la mémoire, des embeddings et du RAG",
                 "Tester empoisonnement de documents, index, métadonnées et retrieval",
                 "Vérifier provenance et inspection de documents ou fichiers ingérés",
                 "Contrôler MCP: authentification, scopes, descriptions d'outils et trust boundaries",
                 "Tester tool poisoning, shadowing et substitutions de serveurs MCP",
                 "Vérifier prévention d'exfiltration via tools, URL, messages et fichiers",
                 "Tester boucles autonomes, budgets, quotas, limites temporelles et kill switch",
                 "Vérifier journalisation et attribution utilisateur de chaque action agentique",
                 "Vérifier que les actions destructrices exigent une approbation selon la politique",
             )),
    Category("OBS", 20, _n("Journalisation, surveillance, erreurs et résilience", "Logging, monitoring, errors and résilience",
                           "السجلات والمراقبة والأخطاء والمرونة"),
             "all", "hybrid", "revue logs, instrumentation et exercices isolés", (
                 "Vérifier journaux d'authentification et d'accès aux ressources sensibles",
                 "Vérifier journaux d'administration et modifications d'autorisations",
                 "Vérifier corrélation trace-id, user-id, tenant-id et transaction-id sans secrets",
                 "Vérifier protections contre altération et suppression des journaux d'audit",
                 "Vérifier absence de secrets et données personnelles non nécessaires dans les logs",
                 "Vérifier alertes sur tentatives d'accès interdites et anomalies critiques",
                 "Vérifier gestion des exceptions sans fuite d'informations sensibles",
                 "Vérifier sauvegardes, restauration testée et intégrité des données",
                 "Vérifier dégradation contrôlée et continuité des fonctions de sécurité",
                 "Vérifier cohérence horodatage, rétention et capacité d'investigation",
             )),
    Category("SRV", 21, _n("Fonctions serverless, edge, workers et jobs", "Serverless, edge, workers and jobs",
                           "الدوال بدون خادم والحافة والعمال والمهام"),
             "serverless", "hybrid", "analyse IaC et fonctions serverless", (
                 "Vérifier authentification des fonctions HTTP et déclencheurs événementiels",
                 "Vérifier IAM minimal des fonctions et comptes d'exécution",
                 "Vérifier validation des payloads de files, événements et objets",
                 "Vérifier idempotence, déduplication et reprise après échec",
                 "Vérifier sécurité des variables d'environnement et secrets runtime",
                 "Vérifier restrictions réseau, VPC et endpoints internes",
                 "Vérifier protections contre déclenchement massif et dépassement de coûts",
                 "Vérifier permissions et intégrité des packages et layers",
                 "Vérifier conformité des politiques de timeout, retry et dead-letter queue",
             )),
    Category("BIN", 22, _n("Bibliothèques natives, firmware, WebAssembly et embarqué", "Native libraries, firmware, WebAssembly and embedded",
                           "المكتبات الأصلية والبرامج الثابتة وWebAssembly والأنظمة المدمجة"),
             "native", "hybrid", "fuzzing securise, analyse binaire et compilation", (
                 "Analyser code natif pour erreurs mémoire et arithmétiques",
                 "Vérifier compilation durcie et protections de la plateforme",
                 "Vérifier provenance et signatures des firmwares et binaires",
                 "Vérifier intégrité et authenticité du mécanisme de mise à jour",
                 "Vérifier parser de formats binaires, paquets et protocole custom",
                 "Vérifier limitations et permissions des modules WebAssembly",
                 "Vérifier l'isolation des bibliothèques FFI et appels natifs",
                 "Vérifier absence de clés embarquées dans firmware ou ressources",
                 "Vérifier fuzzing des parsers sur cible de test isolée",
             )),
    Category("INT", 23, _n("Intégrations et services tiers", "Third-party intégrations and services", "التكاملات وخدمات الأطراف الثالثة"),
             "intégrations", "hybrid", "analyse des flux API et permissions fournisseurs", (
                 "Inventorier fournisseurs externes, SDK et flux de données",
                 "Vérifier authentification et scopes des connecteurs",
                 "Vérifier validation et authentification des événements entrants",
                 "Vérifier sorties vers domaines externes autorisés et contrôles SSRF",
                 "Vérifier rotation et révocation des clés d'intégration",
                 "Vérifier traitement des erreurs et réponses malformées de tiers",
                 "Vérifier la séparation des tenants chez les intégrations et mappings d'identité",
                 "Vérifier gestion des limites API, retries et déduplication",
                 "Vérifier implications de la désactivation d'un fournisseur ou SDK",
                 "Vérifier sécurité des partages de fichiers et URLs pré-signées",
             )),
    Category("QA", 24, _n("Qualité des scans, preuve et couverture", "Scan quality, evidence and coverage", "جودة الفحص والأدلة والتغطية"),
             "all", "hybrid", "matrice de preuves, validation des résultats et suivi de couverture", (
                 "Exécuter les moteurs SAST/SCA/secret/IaC pertinents et enregistrer leurs versions",
                 "Tester que les moteurs s'exécutent réellement et produisent des résultats exploitables",
                 "Comparer surfaces détectées et éléments réellement présents dans le dépôt",
                 "Cartographier contrôles couverts, non couverts, non applicables et bloqués",
                 "Vérifier que chaque résultat est lié à un fichier, ligne, endpoint ou actif précis",
                 "Valider les findings critiques par reproduction sûre lorsque possible",
                 "Évaluer faux positifs, faux négatifs et limites des outils utilisés",
                 "Conserver configurations, empreintes et date des scans pour reproductibilité",
                 "Tester que les correctifs génèrent des tests de régression et un nouveau scan",
                 "Vérifier non-régression sur pull requests, releases et nouveaux actifs",
                 "Bloquer l'étiquette \"audit complet\" si un contrôle requis n'a pas été exécuté",
                 "Produire un rapport machine-readable des lacunes et preuves avec responsables",
             )),
)

CONTROLS: tuple[Control, ...] = tuple(
    Control(f"NAV-CYBER-{cat.id}-{index:03d}", cat.id, index, task, cat.applicability, cat.verification_mode, cat.tool_family)
    for cat in CATEGORIES
    for index, task in enumerate(cat.tasks, start=1)
)
CONTROL_IDS: tuple[str, ...] = tuple(c.id for c in CONTROLS)
_CATEGORY_BY_ID = {c.id: c for c in CATEGORIES}
_CONTROL_BY_ID = {c.id: c for c in CONTROLS}


def category(category_id: str) -> Category:
    try:
        return _CATEGORY_BY_ID[category_id]
    except KeyError:
        raise KeyError(f"unknown referential category {category_id!r}") from None


def control(control_id: str) -> Control:
    try:
        return _CONTROL_BY_ID[control_id]
    except KeyError:
        raise KeyError(f"unknown referential control {control_id!r}") from None


def controls_of(category_id: str) -> list[Control]:
    return [c for c in CONTROLS if c.category == category_id]


def referential_json() -> dict[str, Any]:
    """The referential in the exchange format of the catalogue (metadata + 302 controls)."""
    return {
        "metadata": {
            "title": TITLE,
            "version": VERSION,
            "updated": UPDATED,
            "scope": ("Inventaire, revue, scan et tests pour projets applicatifs de toute technologie; "
                      "activer seulement les contrôles applicables."),
            "control_count": len(CONTROLS),
            "category_count": len(CATEGORIES),
            "status_enum": list(STATUS_ENUM),
            "coverage_rule": COVERAGE_RULE,
            "safety": SAFETY,
            "reference_urls": dict(REFERENCES),
        },
        "controls": [
            {
                "id": c.id,
                "category": category(c.category).name["fr"],
                "applicability": c.applicability,
                "verification_mode": c.verification_mode,
                "task": c.task,
                "default_status": "not_tested",
                "evidence_required": True,
                "tool_family": c.tool_family,
            }
            for c in CONTROLS
        ],
    }


def referential_markdown() -> str:
    """The referential as a checklist, one section per category."""
    lines = [
        f"# {TITLE}",
        "",
        f"**Version :** {VERSION} - **Date :** {UPDATED} - **Contrôles :** {len(CONTROLS)} - **Catégories :** {len(CATEGORIES)}",
        "",
        "## Conditions d'exécution",
        "",
        "- Détecter automatiquement types de projets, langages, environnements et surfaces.",
        "- Appliquer les contrôles `all` dans chaque audit; appliquer les autres lorsqu'une surface correspondante existe.",
        f"- Statuts autorisés : {', '.join(f'`{s}`' for s in STATUS_ENUM)}.",
        "- Un contrôle `passed` exige une preuve d'exécution horodatée, le périmètre et la version des outils.",
        "- `not_applicable` exige une justification. `blocked` et `not_tested` ne sont pas des réussites.",
        f"- {SAFETY}",
        "",
    ]
    for cat in CATEGORIES:
        lines += [
            f"## {cat.number:02d}. {cat.name['fr']} - {len(cat.tasks)} contrôles",
            "",
            f"**Applicabilité :** `{cat.applicability}` - **Verification :** `{cat.verification_mode}` - "
            f"**Moteurs possibles :** {cat.tool_family}",
            "",
        ]
        lines += [f"- [ ] **{c.id}** - {c.task}" for c in controls_of(cat.id)]
        lines.append("")
    lines += ["## References", ""]
    lines += [f"- [{name}]({url})" for name, url in REFERENCES.items()]
    return "\n".join(lines).rstrip() + "\n"


assert len(CONTROLS) == 302, len(CONTROLS)
assert len(CATEGORIES) == 24
assert len(set(CONTROL_IDS)) == len(CONTROL_IDS)
assert all(c.applicability in APPLICABILITY_TAGS and c.verification_mode in VERIFICATION_MODES for c in CATEGORIES)
