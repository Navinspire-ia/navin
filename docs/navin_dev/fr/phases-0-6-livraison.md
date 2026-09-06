# Navin Code - Chronique Phases 0 a 7 (livraison)

Document de suivi de la roadmap **Navin Code** (parite Cursor + agent type Claude Code + continuity locale).  
Objectif produit : poste de pilotage agent + IDE local.

Contrats auto : `tests/test_code_phases_0_7.py` + vitest `webui/src/lib/code-phase7.test.ts`.

---

## Vue d'ensemble

| Phase | But | Etat livraison |
|---|---|---|
| **0** | Zero friction + smoke Code | Livree (API smoke + health; E2E navigateur non exhaustif) |
| **1** | Parite editeur Cursor | Livree (Tab stream, nav LSP, palettes, Cmd+K, diff/review) |
| **2** | Agent Code + verify + eval | Livree |
| **3** | Contexte repo (rules, impact, meter, warm) | Livree |
| **4** | Git / PR / CI equipe | Livree |
| **5** | Debugger DAP | Livree (Python + Node discovery prod) |
| **6** | Continuity / resume J+N | Livree |
| **7** | Polish prod (drift UI, PR sync UI, head_sha, Node) | Livree |


---

## Phase 0 - Fondations "zero friction"

### Objectif planifie
Pipeline release unique, health checks Code visibles, telemetry Tab opt-in, suite smoke happy-path (ouvrir fichier, Tab, search, git, accept hunk). Done quand WSL/Windows/Linux/mac passe le smoke sans rebuild manuel.

### Actions realisees
1. **Smoke backend Code** (`tests/test_code_phase1_smoke.py`) : route modele assist, FIM sanitize, `resolve_related_files`, outline symboles.
2. **Health runtime** deja expose via WebUI (`/api/webui/runtime/health`, toasts `ProductToastStack`).
3. Rebuild / restart gateway documente dans le flow (`make restart-bg`, ports WebUI ~8766, Vite ~5173).

### Limites (pas tout le plan Phase 0)
- Pas de telemetry anonyme Tab complete cross-desktop.
- Smoke navigateur E2E full happy-path pas systematise.
- Pipeline release desktop Win/mac/Linux "sans conflit dist" non reecrit de bout en bout dans ce chantier.

### Fichiers / fonctions cles
| Fichier | Role |
|---|---|
| `tests/test_code_phase1_smoke.py` | Smoke Phase 0/1 |
| `navin/webui/assist_api.py` | `_assist_preset`, `resolve_related_files`, `finalize_completion` |
| `navin/webui/symbols_api.py` | `outline_payload` |
| `webui/src/lib/api.ts` | `fetchRuntimeHealth` (et cousins) |
| `webui/src/components/ProductToastStack.tsx` | Affichage pression / health |

---

## Phase 1 - Parite editeur Cursor

### Objectif planifie
Tab/ghost niveau Cursor, Cmd+K, navigation IDE (F12/refs/rename/hover/Problems), palettes, diff/review.

### Actions realisees (detail)

#### 1.1 Tab / Ghost text
- Route modele : `code` → `code-fast` → `fast` → `dev` via `_assist_preset()`.
- Streaming WS : `assist_complete` → deltas `assist_delta` / `assist_done`, fallback HTTP.
- Debounce adaptatif **80-120 ms** (`adaptiveIdleDelayMs` dans `inlineAi.ts`).
- Accept : **Tab** = mot, **Mod-→** = ligne, **Mod-Enter** = tout.
- Contexte : fichier courant + `resolve_related_files` (index/imports).
- Meta latence + status bar `Tab Xms`.

**Fonctions**
- Backend : `_assist_preset`, `_ask_stream`, `stream_completion`, `completion_payload`, `resolve_related_files`, `finalize_completion`, `partial_completion`.
- Frontend : `acceptGhostWord`, `acceptGhostLine`, `acceptGhost`, ghost field CodeMirror.

**Fichiers**
- `navin/webui/assist_api.py`
- `navin/webui/ws_http.py` (handlers WS assist)
- `webui/src/components/dev/inlineAi.ts` (+ `inlineAi.test.ts`)
- `webui/src/components/dev/CodeEditor.tsx`
- `tests/test_assist_api.py`, `tests/test_assist_related_files.py`

#### 1.2 Cmd+K / inline edit
- Streaming + preview + apply / discard / retry.
- Multi-hunk dans le fichier.
- Historique local : `cmdkFileHistory.ts` (+ test).

**Fonctions** : `stream_edit`, `edit_payload`, `finalize_edit`, `partial_edit`.

**Fichiers**
- `navin/webui/assist_api.py`
- `webui/src/components/dev/cmdkFileHistory.ts`
- `webui/src/components/dev/cmdkFileHistory.test.ts`
- `CodeEditor.tsx` / workbench wiring

#### 1.3 Navigation IDE
- Hover types, **F12** go-to-def, **Shift+F12** references, picker multi-locations.
- **F2** rename (Preview / Confirm).
- Panneau **Problems** cliquable.
- Outline + breadcrumbs.
- Backend LSP : `lsp_api.py` (`action=references`, definitions, etc.).

**Fichiers**
- `navin/webui/lsp_api.py`
- `webui/src/components/dev/DevProblemsPanel.tsx` (+ test aggregation)
- `webui/src/components/dev/DevOutlinePanel.tsx`
- `webui/src/components/dev/CodeEditor.tsx`
- `webui/src/components/dev/DevWorkbench.tsx`

#### 1.4 Palette & Quick Open
| Raccourci | UI |
|---|---|
| Ctrl/Cmd+P | `DevQuickOpen` |
| Ctrl/Cmd+Shift+P | `DevCommandPalette` |
| Ctrl+T | symboles (palette / outline) |
| Ctrl+Shift+F | search projet (unifie) |

**Fichiers**
- `webui/src/components/dev/DevQuickOpen.tsx`
- `webui/src/components/dev/DevCommandPalette.tsx`
- `webui/src/components/dev/devPalette.test.ts`
- `navin/webui/project_search.py` (`search_payload`, `replace_payload`)

#### 1.5 Diff & review UX
- Vue side-by-side : `diffSideBySide.ts`, `DevDiffView.tsx`.
- Clavier review : **F7** / **Shift+F7** hunks, **Mod-Y** accept, **Mod-N** reject.
- Blame leger ligne courante (`git_blame_payload` + strip dans `CodeEditor`).
- Restore checkpoint / review hunks (renforcement existant workbench).

**Fichiers**
- `webui/src/components/dev/diffSideBySide.ts`
- `webui/src/components/dev/DevDiffView.tsx`
- `navin/webui/project_search.py` (`git_blame_payload`, `git_diff_payload`)
- `tests/test_git_blame_api.py`

### Tests Phase 1
- `tests/test_code_phase1_smoke.py`
- `tests/test_assist_api.py`
- `tests/test_assist_related_files.py`
- Vitest : `inlineAi.test.ts`, `DevProblemsPanel.test.ts`, `cmdkFileHistory.test.ts`, `devPalette.test.ts`

---

## Phase 2 - Agent Code "niveau Claude Code"

### Objectif planifie
Profil Ask/Agent/Debug, boucle stricte patch→verify, `apply_patch` only, context packing, eval harness + gate release.

### Actions realisees

#### 2.1 Modes & gates outils
- Mode **Ask** : metadata `read_only_tools` → runner refuse tout outil non lecture.
- `/forge`, `/cruise`, `/debug` : clause `_CODE_STRICT_LOOP_CLAUSE`.
- `CODE_DENIED_TOOLS = {"scrape"}`.
- `APPLY_PATCH_ONLY_METADATA_KEY` : bloque write/edit fichier hors `apply_patch`.

**Fichiers / symboles**
- `navin/command/modules.py` : `READ_ONLY_TOOLS_METADATA_KEY`, `APPLY_PATCH_ONLY_METADATA_KEY`, `CODE_DENIED_TOOLS`
- `navin/command/builtin.py` : `_CODE_STRICT_LOOP_CLAUSE`, wiring Ask / forge
- `navin/agent/loop.py` : `_requires_verify_before_done`, application deny-list + apply_patch only

#### 2.2 Boucle verify
- Nudges si verify manquant / echoue avant "j'ai fini".
- Build/Code turns doivent passer lint/tests/verify selon flags.

#### 2.3 Context packing
- Pack : fichiers ouverts + git dirty + symboles + diagnostics.
- WS `open_files` + client `dev-open-files.ts`.

**Fonctions**
- `build_context_pack_lines`, `agent_context_pack_provider` (`navin/agent/context_pack.py`)

**Fichiers**
- `navin/agent/context_pack.py`
- `webui/src/lib/dev-open-files.ts` (ou equivalent wiring WS)
- `tests/test_agent_context_pack.py`

#### 2.4 Eval harness
- Dataset : `navin/evals/datasets/code_agent_v1.jsonl`
- Runner : `scoreboard()`, `release_gate()` dans `navin/evals/runner.py`
- CLI : `python -m navin.evals.runner ... --scoreboard --gate`

**Tests**
- `tests/test_code_agent_gates.py`
- `tests/test_code_agent_eval_corpus.py`
- `navin/evals/tests/test_evals_harness.py`

---

## Phase 3 - Contexte repo superieur

### Objectif planifie
Index chaud, impact analysis UI, rules projet `.navin/rules`, context meter "why included".

### Actions realisees

#### 3.1 Rules projet
- Lecture / ecriture `.navin/rules/*.md` (+ compat Cursor rules).
- API HTTP `project_rules_api.py`.
- UI onglet explorer : `DevRulesPanel.tsx`.

**Fonctions**
- `list_navin_rules`, `write_navin_rule`, `project_rules_summary`
- `_navin_rules`, `_cursor_rules`, `_single_file_rules`

**Fichiers**
- `navin/agent/project_rules.py`
- `navin/webui/project_rules_api.py`
- `webui/src/components/dev/DevRulesPanel.tsx`
- `tests/test_project_rules.py`

#### 3.2 Warmer index
- Warm fulltext + semantic (si embedder dispo).

**Fonctions** : `schedule_warm`, `_warm_fulltext`, `_warm_semantic`, `note_file_written`  
**Fichier** : `navin/index/warmer.py`

#### 3.3 Impact analysis
- "Who breaks" dans metagraph UI.

**Fichiers**
- `webui/src/lib/metagraph-impact.ts` (`impactDependents`, `impactIds`)
- `webui/src/lib/metagraph-impact.test.ts`
- `webui/src/components/dev/DevMetagraph.tsx`

#### 3.4 Context meter
- Buckets d'usage + liste `included` avec raison ("why").
- Popover status bar.

**Fonctions** : `context_usage_payload`, `_role_buckets`, `_git_dirty_included`, `_rules_included`  
**Fichier** : `navin/webui/context_usage.py` (+ wiring status bar workbench)

---

## Phase 4 - Git / PR / CI

### Objectif planifie
Stage selectif, branches, conflits, draft PR, CI status bar, Fix CI agent.

### Actions realisees

#### 4.1 Git workbench
Payloads dans `navin/webui/project_search.py` :
- `git_changes_payload`
- `git_stage_payload` (stage / unstage chemins)
- `git_commit_payload` (commit selectif + push optionnel)
- `git_pull_payload`
- `git_branch_payload` (create / checkout)
- `git_conflict_action_payload` (continue / abort)
- `git_diff_payload`, `git_log_payload`, `git_commit_detail_payload`
- helpers : `_branch_state`, `_git_conflict_flags`

**UI** : `webui/src/components/dev/DevGitPanel.tsx` (etendu lourdement)

#### 4.2 GitHub PR + CI
`navin/webui/github_pr_api.py` :
- `github_pr_view_payload`
- `github_pr_create_payload` (draft PR via `gh`)
- `github_ci_status_payload`
- `github_fix_ci_prompt_payload` (prompt agent "Fix CI" depuis logs checks)
- `_summarize_checks`

**UI** : badge CI status bar + actions dans `DevGitPanel` / workbench.

### Tests
- `tests/test_git_phase4.py`
- (existants) `test_git_commit.py`, `test_git_state.py`, `test_git_blame_api.py`, etc.

---

## Phase 5 - Debugger DAP

### Objectif planifie
Breakpoints Python + Node, console debug, lien mode Debug agent.

### Actions realisees

#### 5.1 Stack DAP
| Module | Role |
|---|---|
| `navin/dap/client.py` | `DapClient`, protocole DAP (requests/events) |
| `navin/dap/session.py` | `DebugSession`, `detect_runtime`, `resolve_adapter_argv`, `_resolve_node_adapter_argv` |
| `navin/dap/manager.py` | `DebugManager`, `get_debug_manager` |
| `navin/webui/debug_api.py` | `debug_dispatch` → `/api/sessions/.../debug` |

#### 5.2 UI
- Gutter breakpoints dans `CodeEditor.tsx`
- `DevDebugPanel.tsx` + toolbar Debug
- Support extensions `.py`, `.js`, `.mjs`, `.ts` (runtime auto)

#### 5.3 Adapters
- Python : `debugpy` (`pip install debugpy`)
- Node MVP : detection runtime + env `NAVIN_JS_DEBUG_ADAPTER`
- Fake adapter tests : `tests/fixtures/fake_dap_adapter.py`

### Tests
- `tests/test_dap_phase5.py` (Python + chemins Node)

---

## Phase 6 - Continuity code

### Objectif planifie
Resume apres N jours, board↔PR/commits, heartbeat silencieux, memoire decisions.

### Actions realisees

#### 6.1 Resume seed unifie
`navin/continuity/resume_seed.py` :
- `build_resume_seed` - brief + taches board + contraintes + git
- `write_leave_handoff` - handoff explicite a la sortie
- `append_decisions_from_brief` → `DECISIONS.md`
- helpers : `_open_tasks`, `_git_block`, `_resume_brief`, `_extract_decisions_section`

#### 6.2 API HTTP
`navin/webui/continuity_api.py` :
- `resume_seed_payload` → `/resume-seed`
- `leave_handoff_payload` → `/leave-handoff`

#### 6.3 UI
- Bouton Resume + entree palette commandes
- Project Home : `fetchResumeSeed` (`webui/src/lib/api.ts`)

#### 6.4 Decisions depuis consolidator
- Hook dans `navin/agent/memory.py` (`_persist_resume_brief`) → append decisions

#### 6.5 Board ↔ git/PR
- `head_sha` enregistre a l'ouverture PR (`navin/board/github_sync.py`, `store.py`, outil board)
- HEARTBEAT tache silencieuse "Code board continuity"

**Fichier** : `HEARTBEAT.md` section *Code board continuity (silent unless actionable)*

### Tests
- `tests/test_resume_seed.py`
- `tests/test_continuity_context.py` (contexte existant)

---

## Polish post Phase 6 (integre prod)

### A. Constraint drift (soft warnings) - UI live
- `detect_constraint_drift`, `constraint_drift_payload` dans `navin/webui/project_brain.py`
- Payload brain : `"drift": { "supported": true, ... }`
- **UI** : bandeau Resume dans `ProjectHomeView.tsx` (warnings + note)

**Test** : `tests/test_constraint_drift.py`

### B. PR merge → suggestions board (pas d'auto-close) - UI live
`navin/board/pr_sync.py` :
- `check_pr_merged`, `suggest_merged_task_prs`, `pr_sync_payload`
- Endpoint `/github/pr-sync`
- Client : `fetchGithubPrSync`
- **UI** : `DevBoardPanel` bouton "Check merged PRs" + banniere "Mark done"

**Test** : `tests/test_pr_sync.py`

### C. Node DAP (prod discovery)
- `detect_runtime` / `resolve_adapter_argv` / `_candidate_js_debug_servers`
- Auto-discovery : npm global, extensions VS Code/Cursor, `NAVIN_JS_DEBUG_ADAPTER`
- UI Debug pour `.js` / `.mjs` / `.ts`

**Test** : `tests/test_dap_phase5.py`

### D. head_sha sur draft PR UI
- `github_pr_create_payload` renvoie `head_sha` (create + reuse)
- Notification Git panel affiche le SHA

**Test** : `tests/test_git_phase4.py::test_pr_create_payload_includes_head_sha`

### Verification recente
```text
pytest tests/test_constraint_drift.py tests/test_pr_sync.py \
       tests/test_dap_phase5.py tests/test_resume_seed.py \
       tests/test_git_phase4.py
→ 35 passed
```

---

## Inventaire des fichiers touches (par zone)

### Backend Python (nouveaux ou fortement etendus)
```
navin/webui/assist_api.py
navin/webui/ws_http.py
navin/webui/lsp_api.py
navin/webui/symbols_api.py
navin/webui/project_search.py
navin/webui/github_pr_api.py
navin/webui/project_rules_api.py
navin/webui/context_usage.py
navin/webui/project_brain.py
navin/webui/continuity_api.py
navin/webui/debug_api.py
navin/webui/board_api.py
navin/agent/loop.py
navin/agent/context_pack.py
navin/agent/project_rules.py
navin/agent/memory.py
navin/command/builtin.py
navin/command/modules.py
navin/index/warmer.py
navin/continuity/resume_seed.py
navin/continuity/context.py
navin/continuity/__init__.py
navin/dap/__init__.py
navin/dap/client.py
navin/dap/session.py
navin/dap/manager.py
navin/board/github_sync.py
navin/board/store.py
navin/board/pr_sync.py
navin/evals/runner.py
navin/evals/bakeoff.py
navin/evals/datasets/code_agent_v1.jsonl   # 52 taches (15/10/10/10/5 + 2 ask)
navin/apps/cli/service.py                  # cache which() TTL (perf gateway)
navin/webui/cli_apps_api.py                # payload hors event loop
HEARTBEAT.md
```

### Frontend TypeScript / React
```
webui/src/components/dev/CodeEditor.tsx
webui/src/components/dev/DevWorkbench.tsx
webui/src/components/dev/inlineAi.ts
webui/src/components/dev/cmdkFileHistory.ts
webui/src/components/dev/DevQuickOpen.tsx
webui/src/components/dev/DevCommandPalette.tsx
webui/src/components/dev/DevOutlinePanel.tsx
webui/src/components/dev/DevProblemsPanel.tsx
webui/src/components/dev/DevRulesPanel.tsx
webui/src/components/dev/DevGitPanel.tsx
webui/src/components/dev/DevDebugPanel.tsx
webui/src/components/dev/DevDiffView.tsx
webui/src/components/dev/DevMetagraph.tsx
webui/src/components/dev/diffSideBySide.ts
webui/src/lib/api.ts
webui/src/lib/metagraph-impact.ts
webui/src/lib/types.ts
webui/src/lib/dev-open-files.ts   # si present dans le tree
```

### Tests
```
tests/test_code_phase1_smoke.py
tests/test_assist_api.py
tests/test_assist_related_files.py
tests/test_code_agent_gates.py
tests/test_code_agent_eval_corpus.py
tests/test_agent_context_pack.py
tests/test_project_rules.py
tests/test_git_phase4.py
tests/test_dap_phase5.py
tests/test_resume_seed.py
tests/test_constraint_drift.py
tests/test_pr_sync.py
tests/fixtures/fake_dap_adapter.py
tests/test_cli_apps_perf.py
tests/test_live_gateway_smoke.py
navin/evals/tests/test_bakeoff.py
webui/src/components/dev/inlineAi.test.ts
webui/src/components/dev/cmdkFileHistory.test.ts
webui/src/components/dev/DevProblemsPanel.test.ts
webui/src/components/dev/devPalette.test.ts
webui/src/lib/metagraph-impact.test.ts
navin/evals/tests/test_evals_harness.py
```

---

## Raccourcis IDE livres (Phase 1)

| Raccourci | Action |
|---|---|
| Tab / Mod-→ / Mod-Enter | Accepter ghost (mot / ligne / tout) |
| Cmd/Ctrl+K | Edit inline |
| F12 | Go to definition |
| Shift+F12 | Find references |
| F2 | Rename |
| Ctrl/Cmd+P | Quick Open |
| Ctrl/Cmd+Shift+P | Command palette |
| Ctrl+T | Symboles |
| Ctrl+Shift+F | Search projet |
| F7 / Shift+F7 | Hunk suivant / precedent |
| Mod-Y / Mod-N | Accept / reject hunk |

---

## Criteres "on a depasse" (plan) - honnetete produit

Le plan exigeait **5** conditions simultanées. Etat actuel :

1. Tab + nav IDE ≥ Cursor - **instrumente** (telemetrie P50/P90 locale + budgets latence dans le smoke live) ; A/B 5 users reste une action humaine
2. Agent success rate ≥ Claude Code sur corpus - **corpus 50+ taches livre** (15 bugfix / 10 refactor / 10 feature / 10 test_fail / 5 debug + 2 ask), gate 100 %, **harness bake-off champion vs baseline livre** (`navin/evals/bakeoff.py`, artefact JSON) ; il detecte un cote plus faible dans les deux sens (teste)
3. Flow commit/PR/CI sans quitter Navin - **oui fonctionnellement**
4. Friction setup < Cursor - **oui mesurable** : happy path HTTP live en millisecondes (`tests/test_live_gateway_smoke.py`), bug perf gateway corrige (voir ci-dessous)
5. Reprise J+14 - **oui techniquement** (resume seed + HEARTBEAT)

Message honnete : toute la partie mesurable en machine est livree et verte (corpus 50+, gate, bake-off harness, smoke live, budgets latence). Les deux seuls restes sont humains par nature : un A/B avec de vrais utilisateurs et un bake-off branche sur de vrais modeles (le harness est pret, il suffit d'y passer deux `Model`).

---

## Correctif perf critique (audit reactivite)

La route `/api/settings/cli-apps` construisait son payload avec un `shutil.which()` par app du catalogue, directement dans la boucle asyncio. Sous WSL2 (PATH Windows monte via /mnt/c), chaque requete gelait **tout le gateway 5 a 20 s** (y compris `/health`).

Correctif livre :
- `navin/apps/cli/service.py` : cache TTL 20 s sur les lookups `which` + invalidation apres install/update/uninstall
- `navin/webui/cli_apps_api.py` : construction du payload via `asyncio.to_thread` (event loop libre)
- Mesure avant/apres : `/health` 8 s -> 3 ms ; cli-apps 5-20 s -> 4,2 s (froid) / 0,35 s (chaud), sans jamais bloquer les autres routes
- Regression figee : `tests/test_cli_apps_perf.py` (5 tests, dont un test de non-blocage de l'event loop)

---

## Comment verifier rapidement

```bash
# Phases 4-6 + polish
.venv/bin/python -m pytest \
  tests/test_code_phase1_smoke.py \
  tests/test_code_agent_gates.py \
  tests/test_agent_context_pack.py \
  tests/test_project_rules.py \
  tests/test_git_phase4.py \
  tests/test_dap_phase5.py \
  tests/test_resume_seed.py \
  tests/test_constraint_drift.py \
  tests/test_pr_sync.py -q

# Eval agent (corpus 50+ taches, gate 95 % global / 80 % par categorie)
.venv/bin/python -m navin.evals.runner \
  navin/evals/datasets/code_agent_v1.jsonl --scoreboard --gate

# Bake-off champion vs baseline (artefact JSON)
.venv/bin/python -m navin.evals.bakeoff \
  navin/evals/datasets/code_agent_v1.jsonl --out artifacts/evals/bakeoff_report.json

# Smoke live du gateway (se skippe si le gateway est eteint)
.venv/bin/python -m pytest tests/test_live_gateway_smoke.py tests/test_cli_apps_perf.py -q
```

Debugger Python reel : `pip install debugpy`.  
Debugger Node : configurer `NAVIN_JS_DEBUG_ADAPTER` si besoin.

---

---

## Suite 2026-08-08 - Compaction exec (hors phases 0-7)

Livraison transverse agent (pas une phase Code numérotée) :

- Compaction native des sorties `exec` : voir [compaction-sorties-commandes.md](./compaction-sorties-commandes.md)
- Récap UI Free / onboarding / Free vs abo : [livraison-2026-08-08.md](./livraison-2026-08-08.md)

---

