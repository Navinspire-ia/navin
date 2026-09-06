# Trading - loop et heartbeat

Store : `~/.navin/trading`. Route : `#/trading`. Paper only.

## Loop (cycle)

Tant que `enabled` et que `next_due` est atteint, le supervisor execute :

scan → screen → analyze / debat → risk → execute papier → journal → watch silencieux du book.

Jamais inventer un fill broker. Le moteur de risque est deterministe : un blocage n'est pas contourne.

Phases live (recovery si figees) : `scan`, `screen`, `analyze`, `debate`, `risk`, `execute`, `journal`, `busy`, `hunt`.

Plafond : `MAX_CYCLE_S` = 8 min pour tout le corps du cycle. Watch de fin : local, deja sous ce plafond.

Fingerprint : si le tape n'a pas bouge et aucun stop, skip recherche, puis watch quand meme.

## Heartbeat

Avant le tour LLM : `recover_stale_cycle`, puis si un cycle est **vivant** skip `loop_cycling`.

`desk_is_armed` : loop enabled, ou `last_tick > 0`, ou phase live, ou position, ou ordre pending. Un desk jamais utilise → `tick_watch` rend `None`.

Watch local : ordres papier `pending` / `approval` / `awaiting` pas encore marques. Notify (webui, telegram, whatsapp, email). `alerts_sent` seulement si un canal a accepte.

Snapshot / journal / status en heartbeat : `desk_snapshot(live=False)`. **Aucune** requete quotes, pas de mark-to-market.

## Tick force et cycle

`was_enabled` est capture au debut. A la fin, `enabled` reprend cette valeur puis l'intent (Stop gagne). Un tick Studio en pause travaille une fois et **reste en pause**.

HTTP `action=tick` avec `{}` : `force` defaut **true**.

## Start / stop / schedule

```text
navin trading start --kind daily --hour 9
navin trading stop
navin trading schedule --kind weekdays --hour 8 --minute 30
navin trading tick
navin trading watch
```

`start` sans `--run-now` arme seulement.

## Interdits

- Fill invente, ordre broker live
- start / stop / schedule / tick / research depuis le heartbeat
- Cron de chat qui tick
- Snapshot heartbeat qui fetch des quotes (ce serait un mini-scan)
