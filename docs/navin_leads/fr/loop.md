# Start loop Leads

La **Start loop** du Studio chasse puis surveille sur un calendrier mural. Ce n'est **pas** le heartbeat, et ce n'est **pas** une cron de chat.

Le gateway lance un superviseur `navin-leads-loop` (toutes les 20 s) qui appelle `maybe_tick`. Tant que Navin tourne, la loop honore l'horaire sauve. Il n'y a pas de job cron `leads-loop`.

## Start / stop / horaire

| Action | Effet |
| --- | --- |
| `start` | Arme la loop. **Sans `run_now`, ne chasse pas.** |
| `stop` (`pause`) | `enabled=false`. Le superviseur dort. Stop gagne toujours, meme pendant un hunt. |
| `schedule` | Change `kind` / heure / fuseau. Persiste pendant un hunt via `loop.intent.json`. |
| `tick` | Un cycle maintenant (`force` pour ignorer la pause). |

Horaires (`kind`) :

- `daily` - tous les jours
- `weekdays` - lundi-vendredi
- `weekend` - samedi-dimanche
- `weekly` - un jour (`weekday` 1-7)
- `monthly` - un jour du mois (`day` 1-28 ou `last`)

Champs : `--hour` 0-23, `--minute` 0-59, `--tz` IANA.

UI Studio : **Start loop**, **Pause**, horaire, **Run cycle**. La carte affiche phase, cycle, prochaine echeance (`formatNextDue`), `last_result`, stats. Poll UI toutes les 8 s si la loop est on, en phase hunt, ou busy.

## Contrat hunt

1. Le superviseur appelle `maybe_tick`. Si un hunt est deja live : reponse `busy`, pas d'attente.
2. Hunt plafonne a **8 minutes** (`MAX_HUNT_S`). Un hunt stale est recupere et le lock est rendu.
3. Apres la chasse : watch (signaux, tier A, relances dues). Watch dans le hunt : `run_watch(..., already_locked=True)`.
4. Watch en erreur : `skipped_reason=watch_error`, `last_watch` n'est pas tamponne (le heartbeat peut encore alerter).
5. Jamais d'envoi de sequence depuis la loop. Jamais de scrape LinkedIn.

Phases typiques : `armed`, `hunt`, `watch`, `paused`, `busy`.

## Agent et seeds

Si la loop est ON, l'agent ne relance pas un hunt. `status` / `snapshot` exposent `loop` et `loop_brief`. L'outil transmet `schedule`, `run_now`, `tz`, `force`.

Seeds chat et prompt `/leads` : « Start the Leads desk loop » / « Demarre la loop Leads ». **Ne creez pas** une cron de chat qui hunt ou tick.

Voir aussi : [Heartbeat](./heartbeat.md), [Bureau](./desk.md), [CLI et API](./cli-api.md).
