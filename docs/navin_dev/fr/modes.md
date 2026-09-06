# Modes du composer

Le composer de chat a un menu **Mode** (à gauche du sélecteur de modèle). Il fixe la posture de l'agent pour le prochain tour : concevoir, construire, reviewer, auditer la sécurité, déboguer, ou montage. Les modes d'investigation colorent le shell du composer avec une accentuation correspondante.

Quand vous tapez du texte libre (sans `/` en tête), Plan / Review / Security / Debug / Montage **préfixent** votre message avec le workflow slash correspondant pour que l'agent charge le bon brief, les skills et les outils. Le mode Agent préfixe `/forge`. Une commande slash explicite n'est jamais double-préfixée.

L'agent peut aussi basculer le mode UI lui-même via l'outil `set_composer_mode` (événement WebSocket `composer_mode_request`), pour que le menu et la couleur restent synchrones au démarrage d'un workflow ou lors d'un handoff.

## Référence rapide

| Mode | Slash (texte libre) | Rôle de routage modèle | Posture | Livrable principal |
| --- | --- | --- | --- | --- |
| **Plan** | `/blueprint` | `plan` | Conception seule - pas d'édition de code | Plan d'implémentation + handoff Build |
| **Agent** | (aucun ; `/forge` / `/cruise` / `/mission` / `/mobile` mappent ici) | `dev` (mission → `deep`) | Implémenter, tester, itérer | Code qui marche + vérification |
| **Review** | `/inspect` | `review` | Revue de code experte (lecture seule par défaut) | `review-report-*.html` + choix de remédiation numérotés |
| **Security** | `/fortify` | `security` | Audit AppSec expert (lecture seule par défaut) | `security-report-*.html` + choix de durcissement numérotés |
| **Debug** | `/debug` | `deep` | Reproduire et prouver la cause racine | `debug-report-*.html` + choix de fix numérotés |
| **Montage** | `/montage` | `docs` | Kit marketing projet, calendrier, créas | `marketing/montage/` + `montage-report-*.html` |

Le rôle de routage modèle pour `/debug` est `deep` ; le mode UI composer est `debug` (`set_composer_mode(mode=debug)`). Montage utilise `docs` et `set_composer_mode(mode=montage)`.

Familles slash associées qui synchronisent aussi le mode UI quand elles sont tapées explicitement :

| Mode UI | Mappe aussi depuis |
| --- | --- |
| Plan | `/board` |
| Agent | `/forge`, `/cruise`, `/mission`, `/mobile` |
| Review | `/inspect`, `/turbo` |
| Security | `/fortify`, `/probe`, `/unmask`, `/lineage`, `/xray`, `/gatekeeper`, `/perimeter`, `/bastion`, `/vault`, `/recon`, `/threatmap`, `/dast`, `/redteam`, `/pentest`, `/comply` |
| Debug | `/debug` |
| Montage | `/montage` |

Tables de commandes complètes : [Commandes](./commands.md). Audits en un clic : [Actions](./actions.md).

---

## Plan

**Quand l'utiliser :** vous voulez une conception avant tout code - contraintes, options, approche retenue, étapes ordonnées.

**Comment démarrer :**

- Choisissez **Plan** dans le menu Mode et décrivez la tâche en texte libre, ou
- Tapez `/blueprint <tâche>` (aussi disponible via Actions / routage par tâche).

**Ce que fait l'agent :**

1. Appelle `set_composer_mode(mode=plan)` pour afficher Plan dans l'UI.
2. Clarifie objectifs, contraintes et risques.
3. Produit un plan d'étapes concret et un Task Ledger de mission (pas d'édition de fichiers sauf demande explicite).
4. Passe le relais à Build : basculez en Agent ou lancez `/forge` sur le plan validé.

Détails ledger : [Mode Plan](./plan-mode.md).

**Routage modèle :** rôle `plan` (**Réglages → Modèles → Routage par tâche**).

**Astuces :**

- Enchaînement : `/blueprint` d'abord, validez le plan, puis `/forge`.
- Autopilot sans attendre Build : `/cruise`.
- Travail multi-sessions long : `/mission`.
- `/board` garde aussi le composer en Plan quand vous travaillez le board partagé du projet.

---

## Agent

**Quand l'utiliser :** implémenter des fonctionnalités, corriger des bugs déjà compris, lancer les tests et itérer jusqu'au bout.

**Comment démarrer :**

- Laissez le Mode sur **Agent** (défaut) et chattez normalement, ou
- Tapez `/forge <tâche>` pour le brief build autonome, ou `/mobile …` pour le run/preview mobile.

**Ce que fait l'agent :**

1. Planifie si besoin, puis édite le code avec les outils.
2. Lance lint / typecheck / tests quand les scripts existent.
3. Itère sur les échecs jusqu'à l'objectif ou un blocage.
4. Peut appeler `set_composer_mode(mode=agent)` en quittant Plan / les modes d'investigation.

**Routage modèle :** rôle `dev` pour `/forge` (et workflows build associés).

**Astuces :**

- Préférez Plan → Agent pour un travail large ou ambigu.
- Après Review / Security / Debug, choisissez un numéro de remédiation (`Start with #1` / `Commencer par #1`) puis passez en Agent ou `/forge` pour implémenter.

---

## Review

**Quand l'utiliser :** revue de code experte d'un diff, d'un chemin ou du projet entier - plus profonde qu'un coup d'œil.

**Comment démarrer :**

- Mode **Review** + texte libre, ou `/inspect [chemin|diff|scope]`, ou Actions → **Review de code**.

**Périmètre :** `git status` + diff staged/unstaged/récent, ou un chemin nommé. Utilise le métagraphe pour les chemins chauds si utile.

**Outils de preuve :** `read_file`, ripgrep, `exec` pour lint/typecheck/tests (`ruff`, `eslint`, `tsc`, `mypy`, `pytest`, …).

**Couches couvertes :**

| Couche | Focus |
| --- | --- |
| A. Correctness | Bugs de logique, null/undefined, gestion d'erreurs, courses, risques async |
| B. Data & SQL | SQL brut, paramétrage manquant, N+1, transactions, migrations, mauvais usage ORM |
| C. Contrats API | Formes, authz sur les handlers, mass assignment, breaking changes |
| D. Frontend | Puits XSS, CSRF, contrôles auth côté client seuls, gaps de validation de formulaires |
| E. Odeurs sécu | Injection, secrets, crypto faible, path traversal |
| F. Tests & qualité | Tests manquants/cassés/flaky ; quality gate si scripts présents |
| G. Performance | Boucles chaudes, requêtes non bornées, indexes/pagination manquants |
| H. Maintenabilité | Code mort, god objects, nommage, deps mortes |

**Chaque finding doit inclure :**

- Sévérité (Critical / High / Medium / Low / Info)
- `fichier:ligne` (ou plage de hunk)
- Impact
- Correctif concret
- Un **exemple réel** - extrait de code vulnérable/buggé, sortie de test en échec, ou petit PoC (pas un blabla checklist générique)

**Clôture :**

1. Appeler `code_review(action=report, …)` - écrit `review-report-[YYYYMMDD-HHMMSS].html` et **ouvre File Preview automatiquement** dans la WebUI (`preview_opened=true`).
2. En chat : résumé court + **demander par quel numéro de remédiation commencer** (`Start with #1`, `#2`, …).
3. Lecture seule sauf si vous avez demandé Auto-fix / tout corriger.
4. Optionnel : `pr_comments(preview)` puis `pr_comments(post, kind=review, …)` si une PR GitHub est ouverte (`gh` requis).

**Outils dédiés :** `code_review` (`scope` / `filter` / `report`). Détail : [Outils expert](./expert-tools.md).

**Routage modèle :** rôle `review`.

**Verdict :** Approve ou Request changes, plus un plan de remédiation numéroté (effort S/M/L, risque si on attend, premier pas concret).

---

## Security

**Quand l'utiliser :** AppSec élite + workflows défensifs (et offensifs associés) avec preuves de la source au sink.

**Comment démarrer :**

- Mode **Security** + texte libre → lance `/fortify`, ou
- Tapez `/fortify [chemin|scope]`, ou Actions → **Audit sécurité**, ou tout slash de la famille sécu (`/probe`, `/xray`, `/pentest`, …).

**Phases `/fortify` (toutes couvertes ; dire « clean » avec preuve si vide) :**

1. **Carte de surface** - langages, lockfiles, routes, GraphQL/WS/webhooks, auth, DB/SQL, formulaires, uploads, jobs, IaC/Docker/K8s, secrets CI, panneaux admin
2. **Scanners** (si présents) - gitleaks/trufflehog/detect-secrets ; npm audit/pip-audit/osv-scanner/cargo audit/govulncheck ; bandit/semgrep ; trivy/checkov/tfsec/kube-linter/hadolint ; sqlfluff
3. **Injection & données** - SQL/NoSQL/ORM, injection commande/LDAP/XPath/template, path traversal, XXE, désérialisation dangereuse, SSRF, injection header/host
4. **Frontend & client** - XSS, `dangerouslySetInnerHTML` / `innerHTML`, open redirects, CSRF, CSP, clickjacking, postMessage, pollution de prototype, authz côté client seule (Playwright si une UI tourne)
5. **AuthN / AuthZ** - hash de mots de passe, JWT `alg:none`/secrets faibles, sessions, MFA, IDOR/BOLA, escalade de privilèges, mass assignment
6. **Réseau & transport** - TLS, HSTS, CORS, en-têtes sécu, rate limits, auth websocket, signatures webhook, ports admin/debug exposés, SG/firewall ouverts dans l'IaC
7. **Chaîne d'appro & secrets** - CVE lockfile, typosquatting, clés fuitées dans l'arbre/l'historique, secrets dans les logs
8. **Vie privée & conformité** - flux PII, chiffrement, rétention, isolation tenant, écarts RGPD/CCPA/PCI
9. **LLM / agent** (si pertinent) - injection de prompt, outils trop larges, évasion de sandbox

**Chaque finding :** sévérité, `fichier:ligne` ou preuve scanner, impact, **PoC réel** (payload / curl / code), correctif minimal.

**Clôture :**

1. `security_scan(…, write_report=true)` écrit `security-report-[YYYYMMDD-HHMMSS].html` et **ouvre File Preview automatiquement** dans la WebUI.
2. Choix de durcissement numérotés ; demander quel `#` démarrer.
3. Lecture seule sauf demande de correction.
4. Optionnel : `pr_comments(preview)` puis `pr_comments(post, kind=security, …)` sur une PR ouverte.

**Outils dédiés :** `security_scan` (kinds `secrets|sast|sca|quick|full`). Détail : [Outils expert](./expert-tools.md).

**Routage modèle :** rôle `security` pour `/fortify` et la plupart des workflows audit/offensif.

**Éthique :** rester dans le périmètre autorisé ; PoC non destructifs ; ne jamais exfiltrer de vrais secrets. Vous êtes responsable de n'auditer que ce que vous êtes autorisé à tester.

---

## Debug

**Quand l'utiliser :** quelque chose est cassé et vous avez besoin de la cause racine avec preuves - pas de patches shotgun spéculatifs.

**Comment démarrer :**

- Mode **Debug** + texte libre, ou `/debug [signal|chemin|scope]`, ou Actions → **Debug** (si disponible).

**Processus :**

1. **Verrouiller le signal** - erreur exacte, test en échec, stack, code HTTP, champ/requête mauvais, ou étapes de repro. Sinon : git status/diff, logs récents, scripts package.
2. **Reproduire** avec `exec` - test en échec, typecheck, lint, ou commande cassée. Capturer stdout/stderr/codes de sortie. Pour SQL/données : schéma/requêtes/migrations. Pour API : handler → validation → DB. Pour frontend : props/state → réseau → serveur.
3. **Inspecter** - `debug_repair(action=mcp_status)` : si DebugMCP est up (preset `debugmcp` → `http://127.0.0.1:3001/mcp`), utiliser breakpoints / variables / evaluate ; sinon logs / `pdb`. Preuves réelles uniquement.
4. **Isoler** - `debug_repair(action=start_branch)` avant toute édition de code.
5. **Réduire** - `read_file`, git blame/diff, logs, métriques ; plus petite instrumentation qui prouve la cause. Surveiller courses, mauvais cache, mauvais env, tests flaky, N+1, fuites de connexions, tempêtes timeout/retry.
6. **Board** - classer chaque défaut confirmé (`status=fix`) quand le board projet est actif.
7. **Clôturer** avec `debug_repair(action=report, payload_json=…)` : écrit `debug-report-[YYYYMMDD-HHMMSS].html` et **ouvre File Preview automatiquement** (cause racine + preuves, bugs latents, choix `#N`). Demander quel `#` démarrer.

**Appliquer du code seulement si demandé ;** sinon l'agent peut basculer en Agent (`set_composer_mode(mode=agent)`) et pointer vers `/forge` après votre choix.

**Outils dédiés :** `debug_repair`. Détail : [Outils expert](./expert-tools.md).

**Routage modèle :** rôle `deep` (UI mode `debug`).

---

## Montage

**Quand l'utiliser :** vous avez importé un projet et voulez un plan de push marketing - kit, calendrier jour par jour, images/vidéos - sans embarquer HyperFrames dans l'install Navin.

**Comment démarrer :**

- Mode **Montage** + texte libre, ou `/montage [brief]`, ou carte studio Marketing **Montage projet**.

**Processus :**

1. `set_composer_mode(mode=montage)`.
2. **Démo live (préférée pour les showcases produit) :** `open_preview` / navigate → `browser(record_start)` → parcours happy path (Agent browser live) → `record_stop` → `montage(demo_register)` → sous-titres `.srt` → `montage(package)` pour **9:16 / 1:1 / 16:9** sous `marketing/montage/exports/`.
3. `montage(action=analyze)` → `marketing/montage/project-kit.md`.
4. Proposer un calendrier (`montage(action=calendar)`) et attendre validation avant les batches vidéo IA coûteux.
5. Stills/créas via screenshots navigateur + `generate_image` / `generate_video`.
6. HyperFrames : `doctor` → `setup` lazy → `render`. Fallback : clips IA + ffmpeg.
7. Livrer sous `marketing/montage/` + `montage-report-*.html`. **Jamais de publish auto.**

**Outils dédiés :** `montage`, `browser` (`record_start` / `record_stop`). Skills : `montage-studio`, `playwright-browser`.

**Routage modèle :** rôle `docs` (UI mode `montage`).

**Doc module :** [Studio Montage](../../navin_montage/fr/README.md) (actions, packages FFmpeg/HyperFrames/Remotion, profils).

---

## Autonomie de l'agent et sync UI

| Mécanisme | Rôle |
| --- | --- |
| Menu Mode | Vous choisissez Plan / Agent / Review / Security / Debug / Montage ; le choix est persisté pour le chat |
| Préfixe texte libre | Les modes non-Agent préfixent `/blueprint`, `/inspect`, `/fortify`, `/debug` ou `/montage` |
| Slash → mode | Envoyer un slash workflow connu met à jour le menu + l'accent |
| `set_composer_mode` | Outil agent ; émet `composer_mode_request` en WebSocket |
| Démarrage workflow | Lancer `/inspect`, `/fortify`, `/debug`, `/montage`, `/blueprint`, `/forge`, … demande aussi le mode UI correspondant |

L'outil ne change pas les outils mid-turn par lui-même - il met à jour ce que vous voyez dans le composer pour que l'accent suive la posture.

---

## Rapports HTML (Review / Security / Debug)

Les modes d'investigation **doivent** se clôturer par un rapport HTML soigné et autonome (skill `studio-html-report`) :

| Mission | Motif de nom de fichier |
| --- | --- |
| Review | `review-report-[YYYYMMDD-HHMMSS].html` |
| Security | `security-report-[YYYYMMDD-HHMMSS].html` |
| Debug | `debug-report-[YYYYMMDD-HHMMSS].html` |

**Contenu du rapport :**

1. En-tête (mission, scope, horodatage)
2. Résumé exécutif avec compteurs de sévérité
3. Cartes de findings - chip de sévérité, localisation, impact, **exemple réel**, fix
4. **Plan de remédiation** - choix numérotés (`#1`, `#2`, …) avec effort, risque si on attend, premier pas
5. Table des livrables (HTML + CSV/MD/XLSX/JSON éventuels)
6. Pied de page

**Règles :**

- Tout le CSS en inline ; pas de polices externes, CDN, ni JavaScript (File Preview sandboxe les scripts)
- Les outils `code_review` / `security_scan` / `debug_repair` **ouvrent File Preview automatiquement** en WebUI - ne pas coller tout le HTML dans le chat
- En chat : 3-6 phrases + demander quel item du plan démarrer
- Ne pas démarrer les fixes tant que vous n'avez pas choisi un numéro (sauf Auto-fix / « corrige tout »)

Le même motif HTML sert aussi à plusieurs studios (RiskLens, SEO, Marketing, Leads, Scraping) sous d'autres noms de fichiers - voir le skill pour la table complète. Référence outils : [Outils expert](./expert-tools.md).

---

## Flux recommandés

```text
Plan  →  (valider le plan)  →  Agent /forge
```

```text
Review ou Security ou Debug
        ↓
  Rapport HTML dans File Preview
        ↓
  Vous : "Start with #1"  (ou #2, …)
        ↓
  Agent /forge implémente cet item
```

```text
Famille Security (profondeur optionnelle) :
  /recon → /threatmap → /probe ou /dast → /redteam → /comply → /report
  (ou /pentest pour le cycle complet ; /fortify pour la passe défensive profonde)
```

---

## Pages liées

- [Outils expert](./expert-tools.md) - `code_review`, `security_scan`, `debug_repair`, `pr_comments`, DebugMCP
- [Commandes](./commands.md) - référence complète des commandes slash
- [Actions](./actions.md) - audits en un clic du module Code
- [Skills](./skills.md) - skills préchargés par les workflows
- [Atelier](./workbench.md) - panneaux et File Preview
- [Vue d'ensemble](./README.md) - module Dev / Code
