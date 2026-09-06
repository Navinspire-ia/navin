# Tenders - loop, heartbeat, write

Store : `~/.navin/tenders`. Route : `#/tenders`.

Meme store pour Studio, Tauri (Linux / Windows / macOS), `navin tenders`, `python -m navin.tenders.desk_cli` et l'outil `tenders`.

Redaction par avis (references, slides types, exemples) : [tenders-write.md](./tenders-write.md).

## Loop (chasse)

Tant que `enabled` et que `next_due` est atteint, le supervisor `navin-tenders-loop` execute :

1. `collect` (APIs officielles puis `web_search` + `scrape` sur hotes publics)
2. `watch` (digest GO / delais, `send=True` sur les canaux desk)

Jamais d'envoi d'offre / mail acheteur depuis la loop. Jamais Easy Apply. Un portail 404 n'arrete pas les autres sources.

Phases : `unarmed` → `armed` / `paused` / `idle` / `hunt`. `busy` est une reponse, pas une phase disque.

Start exige le wizard complet (`wizard_ready`). Heartbeat follow s'arme des qu'un **nom** societe est au dossier (delais / GO avant la fin du wizard).

Plafonds (dans `navin/tenders/loop.py`, pas `loop_runtime.py`) :

- `MAX_HUNT_S` = 8 min (collect)
- `WATCH_S` = 45 s en fin de cycle (coupe au budget restant)
- fetchers officiels en parallele (jusqu'a 8)
- scrape net : budget `NET_BUDGET_S` = 75 s

## Heartbeat

Avant le tour LLM : `recover_stale_hunt`, puis si une chasse est **vivante** skip `loop_hunting`, grace 90 s si `last_watch` recent, lock tenu → `loop_busy`, sinon `run_watch` plafonne a `HEARTBEAT_WATCH_S` = 20 s.

Allow-list : lectures + `follow` / `watch` / `follow-up`. Refus 403 sur start, stop, schedule, tick, collect, write, send.

Si `count > 0`, digest injecte dans le prompt. Si 0 : silence (`HEARTBEAT_OK` possible).

Tenders **tamponne** `last_watch` apres un watch heartbeat reussi (`_stamp_watch`), sauf chasse vivante. Ca active la grace 90 s. Si le watch timeout / echoue : pas de tampon, le prochain heartbeat reessaie.

## Start / stop / schedule

```text
navin tenders start --kind daily --hour 9
navin tenders stop
navin tenders schedule --kind weekdays --hour 8 --minute 30
navin tenders tick
navin tenders watch
```

`start` sans `--run-now` arme seulement. `--run-now` chasse une fois (force) puis applique l'intent (Stop pendant le collect gagne).

HTTP : `force` defaut true sur `tick`.

Stop ecrit `loop.intent.json` meme si le lock est tenu. `_finish_cycle_state` : Stop gagne toujours.

## Write (resume)

`tenders action=write id=tn-...` assemble un dossier pret (lettre, resume, architecture, planning, methode, matrice exigence par exigence) puis un pack Word/PPT. `revise` applique tes remarques. `download` rend le fichier. Le role Settings **docs** peut reformuler. Aucun chiffre invente. La loop et le heartbeat ne redigent pas. Detail : [tenders-write.md](./tenders-write.md).

## Interdits

- Cron de chat qui collect ou tick
- Write / collect / start / send depuis le heartbeat
- Mail acheteur depuis la loop ou le heartbeat
- Inventer un avis, une reference ou une certif absente du dossier
- Contourner un mur login / captcha / Cloudflare
