# Guide de test - demontrer le depassement de Cursor et Claude Code

Ce guide donne deux niveaux de preuve :

1. **Preuves machine** : commandes qui doivent toutes etre vertes, rejouables devant n'importe quel auditeur.
2. **Parcours manuel** : quoi cliquer dans le WebUI pour montrer chaque fonctionnalite en live, phase par phase, y compris les ajouts recents.

URL locale : `http://127.0.0.1:5173/` (dev Vite) ou `http://127.0.0.1:8766/` (gateway prod).

---

## 1. Preuves machine (10 minutes)

A lancer depuis la racine du repo. Tout doit passer sans faute.

```bash
# 1. Suite backend complete (2679 tests)
.venv/bin/python -m pytest tests/ navin/evals/tests/ -q

# 2. Suite frontend + typecheck (280 tests)
cd webui && npx vitest run && npx tsc --noEmit && cd ..

# 3. Gate d'eval agent : corpus 52 taches (15 bugfix / 10 refactor / 10 feature
#    / 10 test_fail / 5 debug + 2 ask), seuils 95 % global / 80 % par categorie
.venv/bin/python -m navin.evals.runner \
  navin/evals/datasets/code_agent_v1.jsonl --scoreboard --gate

# 4. Bake-off champion vs baseline (artefact JSON pour l'audit)
.venv/bin/python -m navin.evals.bakeoff \
  navin/evals/datasets/code_agent_v1.jsonl --out artifacts/evals/bakeoff_report.json

# 5. Smoke live du gateway : happy path + budgets de latence
#    (regression du bug "gateway gele par cli-apps")
.venv/bin/python -m pytest tests/test_live_gateway_smoke.py tests/test_cli_apps_perf.py -q
```

Resultats attendus : `GATE PASS`, `BAKEOFF PASS`, 0 echec partout, `/health` en millisecondes.

---

## 2. Parcours manuel dans le WebUI

### Phase 0 - Fondations et sante

- Ouvrir le workbench **Code**. En bas, la **status bar** affiche la sante runtime (memoire, disque) rafraichie toutes les 30 s.
- Verifier que `curl http://127.0.0.1:8766/health` repond `{"status": "ok"}` instantanement meme pendant que l'UI est ouverte (c'est le correctif perf : avant, la page Plugins gelait tout le gateway).

### Phase 1 - Editeur et navigation (parite Cursor)

Dans l'editeur du workbench Code :

- **Tab / ghost text** : taper du code, une suggestion grise apparait. `Tab` accepte tout, `Mod-→` accepte un mot, `Mod-Enter` accepte la ligne.
- **Edit inline** : `Cmd/Ctrl+K` sur une selection, demander une modification, accepter/refuser les hunks avec `Mod-Y` / `Mod-N`, naviguer avec `F7` / `Shift+F7`.
- **Navigation LSP** : `F12` (definition), `Shift+F12` (references), `F2` (rename multi-fichiers).
- **Palettes** : `Ctrl/Cmd+P` (Quick Open avec historique), `Ctrl/Cmd+Shift+P` (commandes), `Ctrl+T` (symboles), `Ctrl+Shift+F` (recherche projet).
- **Problems** : ouvrir le panneau Problems, les diagnostics lint/LSP y arrivent en direct.
- **Telemetrie Tab (ajout recent)** : apres quelques completions, la status bar montre la latence P50/P90 locale. Kill-switch : `localStorage` cle `navin.tabTelemetry.enabled=0`.

### Phase 2 - Boucle agent stricte (avantage vs Claude Code)

Dans le chat, mode Code :

- Demander "explique ce module, ne modifie rien" : l'agent reste en lecture seule (outils d'ecriture bloques, pas juste promis).
- Demander une correction : l'agent passe par `apply_patch` uniquement, puis lint/tests, puis `verify`. Il ne dit jamais "termine" avec un verify rouge.
- Montrer le gate : `python -m navin.evals.runner ... --gate` (preuve machine n° 3).

### Phase 3 - Contexte local

- **Regles projet** : panneau Rules du workbench, editer une regle, montrer qu'elle est injectee dans le prompt suivant.
- **Context meter** : la status bar affiche l'usage du contexte (tokens) en continu.
- **Impact / metagraph** : ouvrir la vue Metagraph, selectionner un fichier, l'analyse d'impact liste ce qui depend de lui.

### Phase 4 - Git, PR, CI sans quitter Navin

Panneau **Git** du workbench :

- Stage/unstage par fichier, commit avec message genere, creation de branche.
- **Draft PR** : bouton de creation de PR ; la notification affiche le `head_sha` court (ajout recent) pour la continuite avec le board.
- Blame par ligne et statut CI visibles dans l'UI.

### Phase 5 - Debugger DAP

Panneau **Debug** :

- **Python** : `pip install debugpy`, poser un breakpoint, lancer un script, inspecter variables et pile.
- **Node (ajout recent)** : plus besoin de configurer `NAVIN_JS_DEBUG_ADAPTER` si `@vscode/js-debug` est installe quelque part (npm local/global, extensions VS Code/Cursor) : l'auto-detection le trouve. L'env var reste supportee comme override.

### Phase 6 - Continuite (avantage vs les deux concurrents)

- **Resume seed** : ouvrir la page projet apres une pause, le bloc "reprendre" resume l'etat (branche, taches, derniers commits).
- **Handoff** : demander a l'agent de laisser un handoff, le retrouver au retour.
- **Board** : les taches gardent branche + `head_sha`, synchronisees avec GitHub.

### Phase 7 / polish (ajouts recents)

- **Drift de contraintes** : declarer une contrainte projet (ex : "pas de dependance X"), l'enfreindre dans le code, la page projet affiche un bandeau d'avertissement ambre.
- **PR merge -> board** : merger une PR liee a une tache, cliquer **Check merged PRs** dans le board : suggestion "Mark done" (jamais de fermeture automatique).

---

## 3. Demontrer le depassement (protocole audit)

| Critere du plan | Preuve |
|---|---|
| Tab + nav IDE >= Cursor | Parcours Phase 1 + P50/P90 affiches ; A/B 5 users a organiser (humain) |
| Agent success >= Claude Code | Gate 100 % sur corpus 52 taches + harness bake-off pret pour vrais modeles |
| Commit/PR/CI sans quitter Navin | Parcours Phase 4 en live |
| Friction setup < Cursor | `tests/test_live_gateway_smoke.py` : happy path en millisecondes |
| Reprise J+14 | Parcours Phase 6 (resume seed + handoff + HEARTBEAT) |

Pour le bake-off avec de vrais modeles : brancher deux `Model` (profil Navin vs baseline) dans `run_bakeoff()` de `navin/evals/bakeoff.py` ; le rapport JSON fait foi.

Arguments a montrer en face de Cursor : tout est local (index, regles, telemetrie), continuite J+14 native, boucle agent avec gates verifiables. En face de Claude Code : IDE complet (Tab, LSP, Git, debug, board) en plus de la boucle agent stricte.
