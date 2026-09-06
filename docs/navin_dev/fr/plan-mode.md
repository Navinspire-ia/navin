# Mode Plan & Mission Ledger

Le Mode Plan de Navin est l'orchestrateur qui decide comment l'agent planifie, delegue, verifie et se reprend. C'est le coeur qualite d'un run long : pas une todo dans le chat, mais un Task Ledger et un Progress Ledger codes sur le board projet.

Inspiration : Magentic-One (faits, infos manquantes, progression, replan local, pause) et Deep Agents (plan structure, sous-agents isoles, skills, MCP). L'implantation est native sur le board et l'AgentLoop Navin. Pas de dependance LangGraph ni Deep Agents.

## Protocole orchestrateur

A chaque etape (`/forge`, `/cruise`, `/mission`) :

1. Lire le ledger (`board` `ledger_get`) et `board next`. Ne jamais deviner la file.
2. Choisir une etape ready (ou spawn des etapes ready independantes en cruise/mission).
3. Agir avec les outils Navin.
4. Verifier selon `validation` (`test` / `lint` / `verify` / `manual` / `none`).
5. Ecrire `ledger_progress` avec evidence.
6. Decider : next | retry (plafond `max_retries`) | `ledger_replan` (local) | `ledger_pause`.
7. Ne jamais marquer `done` sans evidence quand la validation est `test`, `lint` ou `verify`.

Si `missing_info` bloque l'etape : pause et demander a l'humain. Ne pas inventer de faits.

Invariants :

- Une seule source de verite : `.navin/board/mission.json` plus le board. Pas de plan fantome dans le chat seul.
- Un `done` affirme = appel outil board + evidence.
- Replan local d'abord. Reecrire tout le plan seulement si l'objectif a change, et le dire dans `history.reason`.
- Un sous-agent recoit un brief isole (role, objectif, chemins, contraintes, critere de fin). Il n'herite pas du transcript parent.
- `/blueprint` ne mute jamais le code, n'installe rien et ne lance pas de tests d'exec.

## Niveaux

| Intention | Commande | Comportement |
| --- | --- | --- |
| Plan seul | `/blueprint` | Analyse d'objectif + Task Ledger + taches board. **Aucune** execution de code. |
| Plan puis build | Bouton Build → `/forge` | Execute le plan avec Progress Ledger par etape. |
| Autopilot | `/cruise` | Planifie, execute, teste, detecte les blocages, replanifie localement jusqu'a done ou pause. Pas d'attente Build. |
| Mission longue | `/mission` | Objectif multi-tours durable + ledger + checkpoints + reprise + rapport final. |

Les modes UI restent `plan` / `agent`. `/cruise` et `/mission` passent le composer en Agent. Routage modele : `/cruise` → `dev`, `/mission` → `deep`.

En mode composer Plan, seuls les outils de lecture et les surfaces de planification (`board`, `ask_user`, `set_composer_mode`) sont actifs. `spawn(action="results")` est une lecture. `spawn(action="start")`, `exec` et les ecritures sont refuses jusqu'au passage Agent (Build ou `set_composer_mode`).

## Modele de donnees

Persiste dans `.navin/board/mission.json` a cote de `board.json`. Schema version 1.

**Task Ledger** (ce qu'on sait, ce qu'il manque, le plan) :

- `goal`, `status` (`draft` | `running` | `paused` | `blocked` | `done` | `failed`)
- `version`, `constraints[]`, `facts[]`, `missing_info[]`, `acceptance_criteria[]`
- `steps[]` : ids de taches board + `depends_on`, `agent`, `acceptance`, `validation`, `retry_count`, `evidence`
- `history[]` : `{version, reason, at, actor, changes}`
- `paused_at`, `pause_reason` (`human` | `budget` | `stall` | `loop` | `manual`)

**Progress Ledger** (ou on en est) :

- `current_step_id`, `last_result_ok`, `last_evidence`, `real_progress`
- `loop_detected`, `stall_count`, `replan_needed`, `recent_action_fingerprints[]`
- `budget` : `max_retries` (3), `max_replans` (5), `max_stalls` (3), plafonds tokens/cout optionnels
- `next_actor` : `main` | `subagent:...` | `human`

Un ledger par projet. `ledger_init` refuse d'ecraser une mission en cours sauf `replace=true`.

## Board

Les taches board restent les etapes executables. Champs optionnels :

- `acceptance` - ce que "done" veut dire pour cette carte
- `validation` - `test` | `lint` | `verify` | `manual` | `none`
- `retry_count`, `max_retries`, `agent`, `evidence`

`depends_on` alimente la file ready (`navin/board/plan.py`). Un replan local remet l'etape en echec et ses dependants en `planned` et laisse l'amont termine intact, donc la file ready ne relance pas l'aval tant que l'etape ratee n'est pas refaite.

Gate dure : un `done` avec `validation` dans `test|lint|verify` (ou avec un `acceptance` non vide) est **refuse** sans `evidence`. Pour un agent, la prose ne suffit pas : un run vert enregistre (`test_run` / `verify` / lint) doit exister.

## Runtime

Chaque tour, le Runtime Context injecte un digest board plus les lignes mission (goal, version, stall, pause, rappel du protocole) via `board_digest`.

- Si le ledger est `paused`, les lignes le disent et demandent d'attendre l'humain.
- `ledger_progress` peut debiter des tokens et mettre en pause sur budget, stall ou boucle.
- `/forge`, `/cruise` et `/mission` font un checkpoint automatique en debut de tour ; `/cruise` et `/mission` aussi avant un replan ou un changement destructif.
- Des empreintes d'actions repetees posent `loop_detected` puis forcent un replan local ou une pause HITL.

## Pause / reprise / edition manuelle

- Agent : `board` `ledger_pause` / `ledger_resume` / `ledger_update`
- Humain : boutons Pause / Reprendre la mission du panel, ou API board `pause_mission` / `resume_mission` / `update_mission`
- Toute edition humaine incremente `version` et ajoute une entree `history` avec `actor=human`

## Checkpoints et reprise multi-sessions

`/mission` cree ou met a jour `/goal` pour que l'objectif survive d'un chat a l'autre. Avec `/checkpoint` et le resume de Project Home, une session plus tard relit le meme ledger, voit `current_step_id` et continue. Fermer une mission terminee avec `ledger_update status=done` (et `update_goal complete`) avant d'en ouvrir une autre.

## Sous-agents

Les briefs `spawn` doivent reprendre `acceptance` et `validation` de l'etape. Champs requis : Role, Goal, chemins de contexte, Constraints, Done when, Do not. Le parent fusionne l'evidence avant `done` / `ledger_progress`. Les sous-agents ne voient pas le transcript parent.

## Panel chat

`ThreadPlanPanel` affiche `goal`, `v{version}`, le statut ledger, les badges stall / boucle / budget / pause, et la derniere raison `history`. Build reste le handoff `/blueprint` → `/forge`. Quand une mission est active, Pause et Reprendre la mission appellent l'API board sans remplacer Build.

`session_plan` attache ces champs ledger pour eviter un second fetch.

## Equivalence (inspiration → Navin)

| Capacite | Source | Primitive Navin |
| --- | --- | --- |
| Todos / plan structure | Deep Agents | Taches board + `mission.json` |
| Sous-agents isoles | Deep Agents | `spawn` + project agents |
| Skills / MCP | Deep Agents | Skills + outils MCP |
| Etat durable / reprise | LangGraph | `mission.json` + `/checkpoint` + `/goal` |
| Pause / reprise / HITL | LangGraph | statut ledger + panel + `next_actor=human` |
| Boucles / replan | Magentic | Progress Ledger + `ledger_replan` |
| Faits / infos manquantes | Magentic | `facts[]`, `missing_info[]` |

## Exemples

```text
/blueprint Ajouter OAuth GitHub sans casser Google Auth
# revoir le panel → Build

/forge Implementer le plan approuve

/cruise Livrer le dark mode de bout en bout avec tests

/mission Migrer l'auth vers les providers Supabase sur le monorepo
```

## Voir aussi

- [Modes](./modes.md)
- [Commandes](./commands.md)
- [Autonomie du board](./board-autonomy.md)
- [Skills](./skills.md)
- [Code agent](../en/code-agent.md)
