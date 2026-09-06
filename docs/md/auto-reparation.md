# Auto-reparation, rapidite, non-blocage

Career / Trading / Marketing : `navin/loop_runtime.py` (`call_with_deadline`, `retry_due_after`).

Tenders : meme contrat dans `navin/tenders/loop.py` (`call_with_deadline`, `_retry_due`).

## Recovery d'une phase morte

Apres un crash, `loop.json` peut rester sur `phase=hunt` (Tenders / Career), `phase=scan` (Trading) ou `phase=measure|learn|busy` (Marketing) alors que plus rien ne tourne.

`peek_loop` (Studio, snapshot, CLI) et `tick_watch` appellent :

- Tenders / Career : `recover_stale_hunt`
- Trading : `recover_stale_cycle`
- Marketing : `recover_stale_cycle` (+ `heal_loop_state` au load, 120 s)

Un hunt/cycle est **vivant** seulement si :

- la phase est une phase de travail, et
- le set in-process (`_live_hunts` / `_live_cycles`) le connait, ou un `hunt_pid` / `cycle_pid` d'un **autre** process est encore alive, et
- `hunt_started_at` / `cycle_started_at` n'a pas depasse 8 min

Sinon : phase → `idle` (si enabled) ou `paused`, journal `stale hunt/cycle recovered`, pid et timestamp retires.

Un hunt/cycle **vivant** n'est pas touche. Recovery prend le lock en `wait_s=0` : si le lock est tenu, no-op.

Un hunt plus vieux que `MAX_HUNT_S` / `MAX_CYCLE_S` est expire meme si le set in-process le marque encore (process hung, PID recycle).

## Intent orphelin

Si Stop ou Horaires ont ete ecrits pendant que le lock etait pris, puis le process est mort :

`recover_*` persist l'intent des que le lock est libre, meme si la phase n'est plus `hunt`/`scan`. Le disque rejoint ce que peek montrait deja.

## Timeouts (pas de gel du gateway)

| Operation | Plafond | Si depasse |
| --- | --- | --- |
| Tenders collect | 8 min | erreur cycle, backoff, lock rendu |
| Career collect | 8 min | erreur cycle, backoff, lock rendu |
| Trading cycle | 8 min | idem |
| Marketing cycle | 8 min | idem |
| Watch en fin de loop | 45 s (ou reste du budget) | cycle OK, `last_watch` non pose si watch rate |
| Watch heartbeat | 20 s | skip `watch_error`, le tour LLM continue |

Le worker timeout est un thread daemon : il n'est pas join. Le supervisor n'attend plus.

## Skip heartbeat pendant un travail live

- Tenders / Career hunt vivant → `skipped=loop_hunting` tout de suite
- Trading cycle vivant → `skipped=loop_cycling` tout de suite
- Marketing cycle vivant → `skipped=busy` tout de suite

Pas d'attente lock 2 s. `run_watch` lui-meme prend le lock en `wait_s=0` et rend `busy` si un autre passage tient le desk.

## Backoff apres erreur

`error_streak` : 120 s, 300 s, 900 s, 1800 s. Le retry est le min(backoff, prochain creneau calendaire). Succes : `error_streak=0`.

Sans ca, un collect/quotes down attendait le prochain creneau quotidien.

## Lock

`desk.lock` via FileLock, `wait_s=0` partout (loop, recover, watch, start/stop). Jamais un wait 2 s sur le chemin heartbeat.

Le supervisor a un flag in-process (`tenders_tick_inflight` / `career_tick_inflight` / `trading_tick_inflight` / `marketing_tick_inflight`) : un tick lent n'en empile pas un second. Tenders relance le supervisor en 5 s s'il tombe.

## CLI non bloquant

`navin tenders stop` / `navin career stop` / `navin trading stop` / `navin marketing stop` sur un TTY : `_stdin_body()` voit `isatty()` et rend `{}`. Pas de `stdin.read()` qui pend.

Vite / Tauri / pipe : JSON sur stdin comme avant. `OSError` sur stdin → `{}` (pytest, captures).
