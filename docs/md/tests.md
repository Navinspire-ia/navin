# Tests loop / heartbeat

## Suites a lancer

Depuis la racine du depot :

```text
.venv/bin/python -m pytest \
  tests/test_loop_runtime.py \
  tests/test_tenders_loop.py \
  tests/test_tenders_module.py \
  tests/test_career_loop.py \
  tests/test_career_heartbeat.py \
  tests/test_career_wiring.py \
  tests/test_career_module.py \
  tests/test_career_integration.py \
  tests/test_trading_loop.py \
  tests/test_trading_heartbeat.py \
  tests/test_trading_wiring.py \
  tests/test_trading_module.py \
  tests/test_trading_schedule.py \
  tests/test_marketing_loop.py \
  tests/test_marketing_heartbeat.py \
  tests/test_marketing_desk.py \
  tests/test_marketing_integration.py \
  tests/test_marketing_api.py \
  -q --tb=short
```

Batterie elargie : `tests/test_tenders_*.py`, `tests/test_career_*.py`, `tests/test_trading_*.py` et `tests/test_marketing_*.py`.

WebUI (extrait Trading) :

```text
cd webui && npm test -- --run src/lib/trading-loop-schedule.test.ts
```

## Ce que les tests prouvent

- Stop pendant hunt/cycle : pause conservee, intent vide a la fin
- Start pendant un tick force : loop reste armed
- Horaires changes mid-cycle : schedule disque conserve, `next_due` recalcule
- Tick HTTP `{}` : un passage, `enabled` false
- Heartbeat refuse start/stop/schedule/tick
- Watch : notify + mark une fois ; crash notify → retry
- Watch ne recrase pas une pause (intent persisté)
- Snapshot heartbeat Trading : `fetch_quotes` non appele
- Recovery phase morte → idle/paused
- Hunt/cycle expire (plus de 8 min) recover meme si marque live
- Heartbeat skip `loop_hunting` / `loop_cycling` / Marketing `busy` pendant un travail live
- Marketing : winners vs reste, `last_watch` seulement si notify OK, outil heartbeat refuse pipeline/start
- Watch lock tenu → `busy` en moins de 0,4 s
- Watch heartbeat hung → `watch_error` en moins de 1 s (plafond 20 s en prod)
- Erreur cycle : `error_streak=1`, retry ~120 s
- CLI TTY stop : stdin non lu
- Seeds / skill / brief : pas de cron de chat

## Ce que ce n'est pas

Pas un run `navin gateway` live, pas Telegram/WhatsApp/email reels, pas un parcours clic Studio Start avec `run_now`, pas un lock entre deux process OS.

Ne pas lancer un hunt/cycle live (`run_now`, bouton Run cycle) pendant une verif navigateur de doc ou de layout.

## Relance process

Un gateway deja lance garde l'ancien code et l'ancien seed jusqu'au redemarrage.
