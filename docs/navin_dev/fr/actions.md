# Menu Actions - référence complète

Le bouton **Actions** (icône checklist, à côté du sélecteur de projet) ouvre le menu d'audits et de workflows en un clic du module **Code** (`#/code`). Chaque entrée construit une instruction précise et l'envoie automatiquement au chat de l'agent.

Les actions s'appuient sur les [commandes slash](./commands.md) et les [skills](./skills.md) correspondants. L'agent travaille sur le projet ouvert dans le workbench. Les modes du composer (Plan / Agent / Review / Security / Debug) sont couverts dans [Modes](./modes.md).

## Options du menu

| Option | Comportement |
| --- | --- |
| **Projet entier** | Périmètre = tout le projet ouvert (`the whole project`). |
| **Fichier : …** | Périmètre = le fichier actuellement ouvert dans l'éditeur. Désactivé s'il n'y a aucun fichier ouvert. |
| **Correction auto** | Coché : après le rapport, l'agent applique les corrections confirmées et les vérifie. Décoché : il propose les corrections classées par impact, sans modifier le code. |

La **correction auto** s'applique aux groupes Qualité, Audit sécurité, Performance, Design & UX et Maintenance. Les actions du groupe **Offensif & conformité** sont en lecture seule (pas de modification de code) : reconnaissance, tests, PoC non destructifs et reporting.

## Vue d'ensemble

| Groupe | Actions | Objectif |
| --- | --- | --- |
| [Qualité](#qualité) | 5 | Review, Debug, tests, barrière qualité, Lancer Mobile |
| [Audit sécurité](#audit-sécurité) | 9 | Audits défensifs / lecture de code et de config |
| [Offensif & conformité](#offensif--conformité) | 7 | Recon, DAST, red team, pentest, conformité, rapport |
| [Performance](#performance) | 2 | Hot paths, métriques de santé |
| [Design & UX](#design--ux) | 3 | UX, cohérence visuelle, accessibilité |
| [Maintenance](#maintenance) | 2 | Refactoring, documentation |

Total : **28 actions**.

---

## Qualité

| Action | Envoie | Ce qui se passe |
| --- | --- | --- |
| Review de code | `/inspect <scope>` | Revue experte (mode Review) : bugs, SQL/données, API, frontend, odeurs sécu, tests, perf - avec `code_review`, citations fichier/ligne, exemples réels, `review-report-*.html` (File Preview auto), et plan de remédiation numéroté. Voir [Modes](./modes.md) et [Outils expert](./expert-tools.md). |
| Debug | `/debug <scope>` | Reproduire l'échec, prouver la cause racine avec preuves (`debug_repair` / DebugMCP), clôturer avec `debug-report-*.html` (File Preview auto) et choix de fix numérotés - demande quel `#` démarrer. Voir [Modes](./modes.md) et [Outils expert](./expert-tools.md). |
| Tests | prompt | Exécute la suite de tests du périmètre, rapporte les échecs avec leurs causes ; avec auto-fix, répare code/tests jusqu'à ce que toute la suite passe. |
| Barrière qualité | prompt | Lint + vérification de types + tests + scan sécurité rapide + odeurs de performance, résumés en tableau pass/fail. |
| Lancer Mobile | `/mobile android` | Détecte Expo / React Native / Flutter, diagnostique l'environnement, démarre le packager, puis utilise l'onglet Mobile. Voir [Mobile](./mobile.md). |

---

## Audit sécurité

Audits **défensifs** : analyse de code, de config et de dépendances. En lecture seule sauf si **Correction auto** est cochée.

| Action | Envoie | Ce qui se passe |
| --- | --- | --- |
| Audit sécurité | `/fortify <scope>` | Passe AppSec experte (mode Security) : `security_scan`, couverture de toutes les phases, scanners si présents, PoC réels, `security-report-*.html` (File Preview auto), choix de durcissement numérotés. Voir [Modes](./modes.md) et [Outils expert](./expert-tools.md). |
| Scan de failles | `/probe <scope>` | Patterns OWASP Top 10, secrets en dur, dépendances vulnérables, SSRF / path traversal, désérialisation, injection de prompt - avec preuve de localisation et correctif minimal. |
| Scan de secrets | `/unmask <scope>` | Clés, tokens, mots de passe, chaînes de connexion dans le working tree **et** l'historique git. Prefère gitleaks / trufflehog / detect-secrets ; masque les valeurs ; exige rotation + purge pour chaque fuite réelle. |
| Audit chaîne d'appro | `/lineage <scope>` | Arbre de dépendances (lockfiles résolus, y compris transitifs) : CVE, paquets obsolètes / abandonnés, intégrité, typosquatting, confusion de dépendances, licences. Plan de mise à jour priorisé ; SBOM si demandé. |
| Analyse statique | `/xray <scope>` | SAST : sinks dangereux (eval, exec, SQL brut, `dangerouslySetInnerHTML`, pickle/yaml load…) avec trace source → sink avant de déclarer un finding. Cite `fichier:ligne`, prouve le chemin, note la sévérité. |
| Contrôle d'accès | `/gatekeeper <scope>` | AuthN/AuthZ de bout en bout : stockage de mots de passe, JWT (rejeter `alg:none`), sessions, MFA, rôles côté serveur, IDOR / BOLA, escalade de privilèges. |
| Surface API & web | `/perimeter <scope>` | Tout ce qui est exposé (HTTP, GraphQL, websockets, webhooks) contre l'OWASP API Top 10 : authz objet/fonction, mass assignment, CORS, CSRF, en-têtes, rate limiting, SSRF. |
| Infra & IaC | `/bastion <scope>` | Dockerfiles, Kubernetes, Terraform / cloud, CI/CD : utilisateur root, tags `latest`, secrets bakés, privileged, IAM trop large, actions non pinnées, `pull_request_target` abusif. Prefère trivy / checkov / tfsec / hadolint. |
| Données & vie privée | `/vault <scope>` | Flux PII / PHI / données financières : chiffrement en transit et au repos, fuites dans logs / télémétrie / prompts, rétention, suppression, minimisation, isolation multi-tenant. Écarts RGPD / CCPA / HIPAA / PCI si pertinent. |

---

## Offensif & conformité

Workflows **offensifs et de conformité**, inspirés d'un cycle de pentest. Toujours **en lecture seule** (pas d'auto-fix) : PoC non destructifs, pas d'exfiltration, strictement dans le périmètre autorisé.

| Action | Envoie | Ce qui se passe |
| --- | --- | --- |
| Reconnaissance | `/recon <scope>` | Cartographie la surface d'attaque avant toute exploitation : routes, GraphQL / websockets, formulaires, uploads, flux d'auth, intégrations tierces, services / ports, sous-domaines, empreinte techno. Livre un inventaire d'assets et les cibles prioritaires. |
| Modèle de menace | `/threatmap <scope>` | Décomposition (actifs, points d'entrée, dépendances), frontières de confiance, STRIDE par élément, table de menaces classée, chemins d'attaque à passer à `/probe` ou `/redteam`. Diagramme Mermaid bienvenu. |
| Test dynamique (DAST) | `/dast <scope>` | Lance ou attache la cible dans un bac à sable, puis sonde en direct : injections, sessions, IDOR, SSRF, XSS / CSRF via navigateur réel, abus de logique métier. Chaque finding confirmé par PoC minimal + preuves requête / réponse. |
| Simulation d'attaque | `/redteam <scope>` | Enchaîne des faiblesses confirmées en chemins d'exploit réalistes (ex. SSRF → metadata → IAM). PoC non destructifs, détection et correctif prioritaire pour chaque chaîne. |
| Pentest autonome | `/pentest <scope>` | Cycle complet orchestré : (1) recon, (2) modèle de menace, (3) scan OWASP et au-delà, (4) exploitation validée par PoC, (5) rapport (sévérité / CVSS, preuves, impact, remédiation). Peut dispatcher des phases à des sous-agents. |
| Conformité | `/comply <scope>` | Cartographie contre un standard (défaut OWASP ASVS L2 ; aussi CIS, SOC 2, ISO 27001 Annexe A, PCI-DSS). Pour chaque contrôle : statut (met / partial / gap / N-A), preuve, écart, remédiation, effort. Scorecard + feuille de route. **Analyse de readiness, pas une certification.** |
| Rapport de pentest | `/report <scope>` | Compile les findings déjà collectés (sans re-tester) : résumé exécutif, périmètre / méthodologie, table classée avec CVSS, preuves / PoC, impact, remédiation, feuille de route. Mapping OWASP / conformité. Fichier enregistré dans le workspace (Markdown par défaut, ou PPTX / DOCX / PDF si demandé). |

### Enchaînement recommandé (pentest)

```text
Reconnaissance  →  Modèle de menace  →  Scan de failles / DAST
        ↓
Simulation d'attaque  (ou  Pentest autonome  pour tout faire d'un coup)
        ↓
Conformité  →  Rapport de pentest
```

Pour un passage rapide : **Pentest autonome** seul, puis **Rapport de pentest**.

### Garde-fous éthiques

Ces actions demandent à l'agent de :

- rester **strictement dans le périmètre** (projet ouvert / cible fournie) ;
- utiliser des **PoC non destructifs** uniquement ;
- **ne jamais** exfiltrer de vrais secrets ou données ;
- **ne jamais** toucher des systèmes hors scope ou de production sans consentement explicite.

Vous êtes responsable de n'auditer que ce que vous êtes autorisé à tester.

---

## Performance

| Action | Envoie | Ce qui se passe |
| --- | --- | --- |
| Performance | `/turbo <scope>` | Chemins chauds, requêtes N+1, I/O bloquantes, caches, taille de bundle, mémoire - mesurés avant recommandation. Optimisations classées par impact / effort. |
| Supervision & métriques | `/pulse <scope>` | Santé du projet : complexité, fraîcheur des dépendances, lint, couverture, dette TODO, KPIs - tableau de bord noté et 3 améliorations prioritaires. |

---

## Design & UX

| Action | Envoie | Ce qui se passe |
| --- | --- | --- |
| Review UX/UI | prompt | Parcours utilisateur, clarté de navigation, états vide / chargement / erreur, espacements, responsive, feedback d'interaction - classés par impact utilisateur, avec fichier concerné. |
| Cohérence design | prompt | Palette, échelle typographique, variantes de composants, bordures / rayons / ombres, mode sombre, design tokens - incohérences + proposition unifiée. |
| Accessibilité | prompt | WCAG 2.2 AA : contraste, navigation clavier, focus, ARIA, labels de formulaires, textes alternatifs, parcours lecteur d'écran - violations par sévérité. |

---

## Maintenance

| Action | Envoie | Ce qui se passe |
| --- | --- | --- |
| Refactoring | prompt | Code mort, duplication, fonctions / composants surdimensionnés, dépendances enchevêtrées, nommage - classés par gain vs risque. |
| Documentation | prompt | Exactitude du README, instructions d'installation, docstrings manquantes, docs d'API, sections obsolètes ; avec auto-fix, écrit ou met à jour la documentation directement. |

---

## Correspondance Actions ↔ commandes slash

| Groupe | Action | Commande / prompt |
| --- | --- | --- |
| Qualité | Review de code | `/inspect` |
| Qualité | Debug | `/debug` |
| Qualité | Tests | prompt libre |
| Qualité | Barrière qualité | prompt libre |
| Qualité | Lancer Mobile | `/mobile` |
| Audit sécurité | Audit sécurité | `/fortify` |
| Audit sécurité | Scan de failles | `/probe` |
| Audit sécurité | Scan de secrets | `/unmask` |
| Audit sécurité | Audit chaîne d'appro | `/lineage` |
| Audit sécurité | Analyse statique | `/xray` |
| Audit sécurité | Contrôle d'accès | `/gatekeeper` |
| Audit sécurité | Surface API & web | `/perimeter` |
| Audit sécurité | Infra & IaC | `/bastion` |
| Audit sécurité | Données & vie privée | `/vault` |
| Offensif & conformité | Reconnaissance | `/recon` |
| Offensif & conformité | Modèle de menace | `/threatmap` |
| Offensif & conformité | Test dynamique (DAST) | `/dast` |
| Offensif & conformité | Simulation d'attaque | `/redteam` |
| Offensif & conformité | Pentest autonome | `/pentest` |
| Offensif & conformité | Conformité | `/comply` |
| Offensif & conformité | Rapport de pentest | `/report` |
| Performance | Performance | `/turbo` |
| Performance | Supervision & métriques | `/pulse` |
| Design & UX | Review UX/UI | prompt libre |
| Design & UX | Cohérence design | prompt libre |
| Design & UX | Accessibilité | prompt libre |
| Maintenance | Refactoring | prompt libre |
| Maintenance | Documentation | prompt libre |

Les commandes slash listées ci-dessus peuvent aussi être tapées directement dans le chat. Voir [Commandes](./commands.md).

---

## Exemples

### Cible fichier

1. Ouvrez `src/App.tsx` dans l'éditeur.
2. Dans **Actions**, passez le périmètre sur **Fichier : App.tsx**.
3. Cliquez **Review de code**.

L'agent reçoit `/inspect /chemin/du/projet/src/App.tsx` et ne passe en revue que ce fichier.

### Audit sécurité projet + corrections

1. Périmètre = **Projet entier**.
2. Cochez **Correction auto**.
3. Cliquez **Scan de secrets** ou **Audit sécurité**.

L'agent audite, puis applique et vérifie les corrections confirmées.

### Cycle pentest

1. **Reconnaissance** sur le projet (ou une URL cible dans le chat via `/recon https://…`).
2. **Modèle de menace**, puis **Scan de failles** et / ou **Test dynamique**.
3. **Simulation d'attaque** sur les findings confirmés.
4. **Conformité**, puis **Rapport de pentest** pour livrer le document.

Ou en un clic : **Pentest autonome**, puis **Rapport de pentest**.

---

## Pages liées

- [Modes](./modes.md) - Plan / Agent / Review / Security / Debug
- [Commandes](./commands.md) - référence complète des slash commands
- [Skills](./skills.md) - skills de sécurité et de développement chargés par l'agent
- [Atelier](./workbench.md) - panneaux du module Code
- [Vue d'ensemble](./README.md) - module Dev / Code
