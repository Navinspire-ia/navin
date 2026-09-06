# Career - loop et heartbeat

Store : `~/.navin/career`. Route : `#/career`.

## Loop (hunt)

Tant que `enabled` et que `next_due` est atteint, le supervisor execute :

1. `collect` (Remotive, ATS, APIs officielles, web ouvert)
2. `watch` (alertes Perfect/Good et follow-ups J3/J7)

Jamais d'apply. Jamais de scrape LinkedIn.

Phases persistées : `unarmed` → `armed` / `paused` / `idle` / `hunt`. `busy` est une reponse, pas une phase disque.

Plafond : `MAX_HUNT_S` = 8 min (collect). Watch de fin de hunt : `WATCH_S` = 45 s, coupe au budget restant.

## Heartbeat

Avant le tour LLM : `recover_stale_hunt`, puis si un hunt est **vivant** skip `loop_hunting`, puis retention, puis `watch` plafonne a `HEARTBEAT_WATCH_S` = 20 s.

Watch local seulement : scores >= 80 en stage ouvert, follow-ups J3/J7. Notify (webui, telegram, whatsapp, email, teams). `alerts_sent` seulement si au moins un canal a accepte (`digest_was_delivered`).

Snapshot / status / dossier / read / book : lectures. Pas de collect.

## Arme

`profile_is_armed` : au moins un titre, ou `wizard_complete`. Sinon `maybe_tick` rend `unarmed` (sauf `force`) et le heartbeat ne tick pas.

## Start / stop / schedule

```text
navin career start --kind daily --hour 9
navin career stop
navin career schedule --kind weekdays --hour 8 --minute 30
navin career tick
navin career watch
```

`start` sans `--run-now` arme seulement. `--run-now` chasse une fois (force) puis applique l'intent (Stop pendant le hunt gagne).

HTTP : `force` defaut true sur `tick`.

## Write (CV par mission)

`career action=prepare` / `write` / `cv` / `tailor` adapte le Master CV a **cette** offre avant tout envoi : mots-cles de l'annonce, experiences reordonnees, lettre, notes ATS, fichier Word. Rien n'est invente. Le polish IA est opt-in (`ai_assist: true`). `download` rend le `.docx`. `apply` ouvre le site employeur ; la loop et le heartbeat ne redigent ni ne postulent.

Qualite, checklist et agent : [career-write.md](./career-write.md).

## Interdits

- Scrape LinkedIn, Easy Apply automatique
- Apply depuis la loop ou le heartbeat
- Search / collect / start / tick depuis le heartbeat
- Cron de chat qui search ou tick
- Pane `discover` : le desk officiel est `home` / `offers` (un hash `pane=discover` mappe vers `offers`)
