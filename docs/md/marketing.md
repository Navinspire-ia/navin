# Marketing - loop et heartbeat

Store : `~/.navin/marketing`. Route : `#/marketing`.

Meme livre pour Studio, Tauri, `navin marketing`, `python -m navin.marketing.desk_cli` et l'outil `marketing`.

Doc produit (FR + EN) : [navin_marketing](../navin_marketing/README.md).

## Loop (mesure → learn)

Tant que `enabled` et que `next_due` est atteint, le supervisor `navin-marketing-loop` execute :

1. `watch` (gagnants + fingerprint concurrents, deja sous le lock)
2. `run_growth_cycle` (scoreboard, winners, variantes)

Jamais de publish. Jamais de spend ads. `harvest` remplit marque / SEO / contenus depuis une URL live. `produce` ecrit les images (packs `brand` / `posts` ou `creative_id`). Video / voix restent sautees sans provider. `launch` ecrit des Markdown sous `launch/`. Les classements SEO ne sont jamais inventes (`ingest_ranking` seulement).

Phases persistées : `unarmed` → `armed` / `paused` / `idle`. Live : `measure`, `learn`, `busy`. `busy` est aussi une reponse overlap.

Plafond : `MAX_CYCLE_S` = 8 min pour tout le corps. Heal disque : phase live plus vieille que 120 s (`heal_loop_state`). Recovery : `recover_stale_cycle` si plus de PID vivant.

## Heartbeat

Avant le tour LLM : `recover_stale_cycle`, puis si un cycle est **vivant** skip `busy`, puis grace 90 s, puis `watch` plafonne a `HEARTBEAT_WATCH_S` = 20 s.

Watch local : winners pas encore dans `alerts_sent`, concurrent si le fingerprint a change. Notify (webui, telegram, whatsapp, email). `alerts_sent` et `last_watch` seulement si un canal a accepte, ou si count=0.

Snapshot / status / watch : lectures. Pas de pipeline, pas de start.

## Arme

`brand_is_armed` : nom de produit, ou brand `company` / `product`. Sinon `maybe_tick` rend `unarmed` (sauf `force`) et le heartbeat ne tick pas.

## Start / stop / schedule

```text
navin marketing start
navin marketing stop
navin marketing schedule
navin marketing tick
navin marketing watch
```

`start` sans `run_now` arme seulement. `run_now` lance un cycle (force) puis applique l'intent (Stop pendant le cycle gagne).

HTTP : `force` defaut true sur `tick`.

## Interdits

- Publier sur LinkedIn / X / Meta / email depuis la loop ou le heartbeat
- Depenser un budget ads (le spend vit dans `#/ads`)
- Understand / pipeline / start / schedule / tick depuis le heartbeat
- Cron de chat qui tick ou relit les KPI
- Inventer du trafic, un CTR ou un post publie hors store
