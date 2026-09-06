# Heartbeat Leads

Le heartbeat **alerte seulement**. Il ne chasse pas, n'arme pas la loop, et n'envoie jamais une sequence.

`tick_heartbeat_desks` appelle `navin.leads.heartbeat.tick_watch` **avant** le tour LLM. La section **Lead scores** de `.navin/HEARTBEAT.md` (et `navin/templates/HEARTBEAT.md`) le dit : « The Leads desk loop (Studio Start loop) hunts on its own saved schedule. That is not this heartbeat. »

## Actions autorisees

Sur un tour heartbeat, seules ces actions passent :

`status`, `snapshot`, `watch`, `follow`, `rescore`, `score`

Refuse (400) : `start`, `stop`, `schedule`, `tick`, `hunt`, `enrich`, `sequence`, `lookalike`, `keys`, `crm`, `outreach`.

Outils deja bloques sur heartbeat : `cron`, `web_search`, `scrape`.

## Politique watch

| Condition | Resultat |
| --- | --- |
| Hunt live (`hunt_is_live`) | skip immediat `loop_hunting` |
| Watch recent (`max(loop.last_watch, profile.last_watch)` < 90 s) | skip `loop_just_watched` |
| Profile pas arme (`wizard_ready` + ICP) | `None` (pas de check) |
| Watch OK | digest ; si `count` est 0, le tour repond `HEARTBEAT_OK` |
| Watch plante | `skipped_reason=watch_error`, pas de tampon `last_watch` |

Deadline watch heartbeat : **20 s** (`HEARTBEAT_WATCH_S`). Jamais `wait_s=8` sur le lock. Grace 90 s pour eviter un double digest loop + heartbeat.

## Ce que le LLM peut faire

1. Rappeler `leads action=watch` (idempotent).
2. Si le prompt a deja un digest : rapporter le compte, les comptes tier A, les signaux, les relances dues.
3. Si `watch.count` est 0 et pas de digest : `HEARTBEAT_OK`.

Jamais : hunt, start, stop, schedule, tick, scrape LinkedIn, envoyer une sequence, depenser des credits Apollo / Hunter / Pappers.

Voir aussi : [Start loop](./loop.md), [Bureau](./desk.md).
