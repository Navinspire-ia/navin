# Outils expert (Review / Security / Debug)

Cette page documente les outils agent qui font tourner les modes **Review**, **Security** et **Debug** de bout en bout : périmètre, scan, filtres, rapports HTML, File Preview, DebugMCP et commentaires PR.

Pour la posture UI et les flux utilisateur, voir [Modes](./modes.md). Pour les slash, voir [Commandes](./commands.md).

## Vue d'ensemble

| Outil | Mode | Rôle |
| --- | --- | --- |
| `code_review` | Review (`/inspect`) | Périmètre de change-set, filtre de précision, rapport `review-report-*.html` |
| `security_scan` | Security (`/fortify`, `/probe`, …) | Scan AppSec structuré + option rapport `security-report-*.html` |
| `debug_repair` | Debug (`/debug`) | Statut DebugMCP, branche isolée, rapport `debug-report-*.html` |
| `pr_comments` | Review / Security (optionnel) | Preview puis post de commentaires inline sur une PR GitHub via `gh` |
| `set_composer_mode` | Tous | Synchronise le menu Mode + couleur du composer (WebUI) |
| `open_file_preview` | Tous (studios) | Ouvre un fichier dans File Preview (fallback ; les rapports expert s'ouvrent aussi seuls) |

Découverte automatique : ces outils sont chargés avec le reste du catalogue agent (`navin/agent/tools/`).

## File Preview automatique

Quand un rapport expert est écrit **depuis la WebUI** (canal websocket) :

1. L'outil émet un événement `file_preview_open_request` avec le chemin HTML.
2. Le panneau **File Preview** s'ouvre (Download HTML / Export PDF).
3. La réponse outil contient `preview_opened: true` (sinon `false` hors WebUI, ex. CLI).

Noms reconnus aussi côté UI (file_edit) :

- `review-report-*.html`
- `security-report-*.html`
- `debug-report-*.html`

(plus les studios : `risklens-`, `seo-`, `marketing-`, `campaign-`, `leads-`, `scrape-`).

En CLI (`navin agent`), le chemin du rapport est renvoyé dans la réponse ; File Preview n'existe pas sur ce canal.

---

## `code_review`

**Actions :**

| `action` | Effet |
| --- | --- |
| `scope` | Change-set à reviewer : dirty git, tracked si arbre propre, ou walk filesystem hors git ; gates OCR (extensions / excludes) ; règles `.navin/review-rules.json` si présentes |
| `filter` | Filtre de précision sur `findings_json` (confiance, chemin/ligne, bruit théorique) |
| `report` | Filtre puis écrit `review-report-<timestamp>.html` et ouvre File Preview en WebUI |

**Paramètres utiles pour `report` :** `findings_json`, `verdict` (`approve` / `request_changes` / `comment`), `effort` (1-5), `summary` (bullets).

**Modules Python :** `navin/review/` (`scope`, `gates`, `rules`, `schema`, `report_html`).

---

## `security_scan`

**Kinds :** `secrets` | `sast` | `sca` | `quick` | `full`.

Toujours : heuristiques embarquées (secrets, injection, XSS, JWT faible, CORS, `shell=True`, pickle, …) avec esquisse de PoC.

Optionnel : CLIs hôtes s'ils sont installés (`gitleaks`, `bandit`, `semgrep`, `npm audit`, `pip-audit`, …). Si un CLI manque, le résultat le note honnêtement (`available: false`) sans inventer de findings.

`write_report=true` écrit `security-report-<timestamp>.html` et ouvre File Preview en WebUI.

**Modules Python :** `navin/security/` (`scan`, `fp_filter`, `poc`, `report_html`).

---

## `debug_repair`

| `action` | Effet |
| --- | --- |
| `mcp_status` | Handshake MCP réel (`initialize` + `tools/list`) vers DebugMCP (défaut `http://127.0.0.1:3001/mcp`, localhost only) |
| `start_branch` | Crée une branche isolée `navin/debug-*` (refuse hors-git, merge/rebase/cherry-pick en cours ; préserve l'arbre dirty) |
| `status` | Branche courante |
| `report` | Écrit `debug-report-<timestamp>.html` (+ JSON twin) depuis `payload_json` ; File Preview auto en WebUI |

**Payload `report` typique :** `signal`, `repro_steps`, `root_cause`, `hypotheses`, `before`, `after`, `stack`, `variables`, `ask_log`, `findings`, `latent_bugs`. Les champs mal typés (ex. string au lieu de liste) sont coercés pour éviter un crash.

Si DebugMCP est down : `mcp_status` renvoie `reachable: false` + hint clair ; l'agent doit basculer logs / `pdb` / instrumentation temporaire.

**Modules Python :** `navin/debug/` (`repair`, `mcp_client`, `mcp_status`).

---

## DebugMCP (preset `debugmcp`)

1. Install the **DebugMCP** extension (VS Code / Cursor) and open a debug-capable workspace.
2. Navin **installe le preset `debugmcp` au démarrage** (`http://127.0.0.1:3001/mcp` dans `tools.mcp_servers`) - pas besoin de l'activer à la main dans Apps. Désactiver : `"autoEnableMcpPresets": false` sous `tools`.
3. Dans `/debug`, l'agent appelle `debug_repair(action=mcp_status)` puis les outils MCP (`add_breakpoint`, `start_debugging`, `list_variable_names`, `get_variables_values`, `evaluate_expression`, `step_*`, `continue_execution`, …).

Sans extension : le mode Debug reste utilisable (repro + logs + branche + rapport), sans breakpoints live. Dès que l'extension tourne, la reconnexion MCP peut charger les outils.

Guide MCP général : [Configure MCP Tools](../../guides/configure-mcp-tools.md).

---

## `pr_comments` (optionnel)

| `action` | Effet |
| --- | --- |
| `preview` | Résout la PR + liste les payloads `path:line` (pas d'écriture réseau) |
| `post` | Crée une vraie review GitHub avec commentaires inline (`kind=review` ou `security`) - demande une approval |

**Prérequis :** `gh` installé et authentifié, PR ouverte dont les fichiers/lignes matchent le diff, findings avec `file_path` + `start_line` / `line`.

Sans PR ou sans `gh` : `preview` reste honnête (erreur / liste vide) ; ne pas inventer de commentaires postés.

**Module Python :** `navin/github/pr_comments.py`.

---

## Skills associés

| Skill | Usage |
| --- | --- |
| `code-reviewer` | Méthodologie `/inspect` |
| `security-auditor` | Méthodologie `/fortify` |
| `debug-live` | Workflow `/debug` + DebugMCP |
| `studio-html-report` | Contrat HTML expert (Real example, Deliverables, Start with #N, print CSS) |

Détail : [Skills](./skills.md).

---

## Flux bout en bout

```text
WebUI → Mode Review / Security / Debug  (ou Actions Code)
      → slash /inspect | /fortify | /debug
      → outils ci-dessus
      → rapport HTML + File Preview ouvert
      → vous : « Start with #1 »
      → Agent /forge pour implémenter
```

```text
Optionnel Review/Security :
  pr_comments(preview) → pr_comments(post)  [PR + gh]
```

```text
Optionnel Debug :
  debug_repair(mcp_status) → outils MCP DebugMCP  [extension :3001]
                 └─ sinon logs / pdb
  debug_repair(start_branch) → patch minimal → report
```

---

## Pages liées

- [Modes](./modes.md)
- [Commandes](./commands.md)
- [Actions](./actions.md)
- [Skills](./skills.md)
- [Atelier](./workbench.md) - File Preview
- [Configure MCP Tools](../../guides/configure-mcp-tools.md)
- [Configuration](../../configuration.md) - routage modèles (`review`, `security`, `deep`)
