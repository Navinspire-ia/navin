# Contrat loop + heartbeat

Deux horloges, un store par desk. Jamais une 3e cron creee dans le chat.

## Deux horloges

| Horloge | Qui tourne | Travail | Interdit |
| --- | --- | --- | --- |
| **Loop** (supervisor gateway, ~20 s) | `navin-tenders-loop` / `navin-career-loop` / `navin-trading-loop` / `navin-marketing-loop` | Travail autonome sur le calendrier du desk tant que le gateway tourne | Inventer un apply / un fill broker / un mail acheteur / un post publie |
| **Heartbeat** | `tick_heartbeat_desks` avant le tour LLM | `watch` / `follow` silencieux sur le livre local | start, stop, schedule, tick, search, collect, write, send, pipeline, publish |

Le supervisor appelle `maybe_tick` sans `force`. Si la loop est en pause ou pas encore due, il ne fait rien.

Le heartbeat appelle `tick_watch` puis ajoute une note au prompt seulement si `watch.count > 0`. Sinon le tour peut repondre `HEARTBEAT_OK`.

## Un store, toutes les surfaces

| Desk | Store | Surfaces |
| --- | --- | --- |
| Tenders | `~/.navin/tenders` | `#/tenders`, `/api/tenders`, `navin tenders`, `python -m navin.tenders.desk_cli`, outil `tenders` |
| Career | `~/.navin/career` | `#/career`, `/api/career`, `navin career`, `python -m navin.career.desk_cli`, outil `career` |
| Trading | `~/.navin/trading` | `#/trading`, `/api/trading`, `navin trading`, `python -m navin.trading.desk_cli`, outil `trading` |
| Marketing | `~/.navin/marketing` | `#/marketing`, `/api/marketing`, `navin marketing`, `python -m navin.marketing.desk_cli`, outil `marketing` |

Meme `loop.json`, meme `loop.intent.json`, meme `desk.lock`.

## Allow-lists heartbeat

| Desk | Actions autorisees | Refuse |
| --- | --- | --- |
| Tenders | `status`, `snapshot`, `get`, `search`, `list`, `index`, `file`, `read-file`, `follow`, `watch`, `follow-up` | start, stop, schedule, tick, collect, write, send |
| Career | `status`, `dossier`, `snapshot`, `read`, `book`, `watch` | search, collect, apply, start, stop, schedule, tick |
| Trading | `status`, `snapshot`, `journal`, `watch` | start, stop, schedule, tick, research, scan |
| Marketing | `status`, `snapshot`, `watch` | understand, pipeline, approve, start, stop, schedule, tick, publish |

Le refus est le meme sur l'outil agent et sur HTTP (`is_heartbeat_turn()`).

## Stop gagne toujours

Start / stop / schedule ecrivent d'abord `loop.intent.json`, meme si un hunt ou un cycle tient `desk.lock` (FileLock non reentrant).

A la fin du cycle, `_finish_*_state` applique l'intent puis efface le fichier. Si `enabled` est false, `phase` devient `paused`.

Un peek (Studio, snapshot, CLI) appelle `recover_stale_*` puis applique l'intent. Un Stop orphelin (ecrit pendant le lock, process mort avant la fin) est persiste des que le lock est libre.

Career / Trading : le heartbeat **n'ecrit jamais** `last_watch` dans `loop.json`. Un watch heartbeat ne peut plus reecrire un vieux `enabled=true` par-dessus une pause.

Tenders : le heartbeat tamponne `last_watch` apres un follow reussi (`_stamp_watch`), sans ecraser une chasse vivante ni une pause (intent + `recover_stale_hunt`). Detail : [tenders.md](./tenders.md).

Marketing : `run_watch` tamponne `last_watch` apres un passage reussi (count=0 ou notify livre). Un notify rate ne pose pas `last_watch` : le prochain heartbeat retente. Detail : [marketing.md](./marketing.md).

## Tick force en pause

- `maybe_tick(..., force=False)` : si pause, no-op.
- `maybe_tick(..., force=True)` : un passage, puis `enabled` reste false, `phase=paused`.
- HTTP `action=tick` avec `{}` : `force` defaut **true** (bouton Studio / Tauri). La loop reste en pause.

Ne pas confondre avec Start (`enabled=true`) ni avec `run_now` (Start + un hunt/cycle tout de suite).

## `last_watch` et grace 90 s

`last_watch` n'est pose qu'a la **fin de la loop** (Tenders / Career / Trading), et seulement si le watch de fin de cycle a ete livré (ou `count=0`). Marketing : `run_watch` pose aussi `last_watch` apres un watch heartbeat reussi (voir plus haut).

Si le heartbeat voit `last_watch` datant de moins de `LOOP_WATCH_GRACE_S` (90 s), il skip `loop_just_watched`. Evite le double digest juste apres un hunt/cycle.

Si le notify a echoue, `last_watch` n'est pas pose : le prochain heartbeat reessaie.

## Pas de cron de chat

L'agent ne doit pas creer un job cron de session qui search / tick. Le calendrier vit dans le store du desk.

Sources de cette regle :

- seeds EN/FR `createSeed.tenders` / `createSeed.career` / `createSeed.trading` / `createSeed.marketing` / `createSeed.montage`
- brief `/tenders`, `/career`, `/trading` et `/marketing` dans `navin/command/builtin.py`
- skills `tender-agent`, `job-search-agent`, `trading-agent`, `marketing-strategist`
- Tenders / Career `module_stack()["rules"]`
- `.navin/HEARTBEAT.md` et `navin/templates/HEARTBEAT.md`

Pas de job `tenders-loop` dans le cron. Job CLI legacy `if job.name == "trading-loop"` : alias Trading seulement. Ne pas enregistrer un 2e job auto.

## Cron produit

Le cron built-in de l'outil `cron` reste pour les loops de **chat** (rappels). Ce n'est pas la loop Tenders / Career / Trading / Marketing. Ne pas appeler `navin cron` via `exec`.
