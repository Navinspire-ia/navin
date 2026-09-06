# Boucle de croissance Marketing

Studio `#/marketing` execute le cycle autonome : **mesure → apprentissage → amelioration**. Le heartbeat n'est pas cette boucle. Une cron de chat n'est pas cette boucle.

Tant que le gateway tourne, le supervisor `navin-marketing-loop` appelle `maybe_tick` environ toutes les 20 secondes. Un tick du est un cycle. Un tick trop tot renvoie `sleep`. Un desk en pause renvoie `paused`. Une marque vide renvoie `unarmed`.

## Controles operateur

| Action | Effet |
| --- | --- |
| `start` | Arme la boucle. `schedule` JSON optionnel (`kind`, `hour`, `minute`, `weekday`, `day`, `tz`). `run_now=true` lance un cycle tout de suite. |
| `stop` | Pause. Survive a un cycle deja en cours. |
| `schedule` | Change les horaires. N'active pas une boucle en pause. |
| `tick` | Lance maintenant si du, ou si `force=true`. L'API tick force par defaut. |

Pause gagne toujours. Start / stop / planning ecrits pendant un cycle vont dans `loop.intent.json`. Le cycle applique cet intent a la fin. L'UI lit `peek_loop` : l'ecran montre l'intent que vous venez d'ecrire.

## Kinds de planning

Memes kinds muraux que Career / Trading / Tenders :

- `daily`
- `weekdays`
- `weekend`
- `weekly` (`weekday` 1-7, lundi = 1)
- `monthly` (jour 1-28 ou dernier jour)

Fuseau IANA (`Europe/Paris`, `UTC`, ...). Le prochain creneau est calcule apres chaque cycle reussi.

## Isolation (pas de blocage)

| Garde | Comportement |
| --- | --- |
| `desk.lock` | Un seul cycle a la fois. `wait_s=0` : un chevauchement renvoie `busy` tout de suite. |
| Supervisor inflight | Le gateway n'empile pas deux `maybe_tick`. |
| Lock watch | Le watch heartbeat sort en `skipped=busy` si un cycle tient le lock. |

## Continuite et auto-reparation

| Evenement | Reparation |
| --- | --- |
| Crash process en `measure` / `learn` / `busy` | `heal_loop_state` apres 120 s, ou `recover_stale_cycle` s'il n'y a plus de PID vivant |
| `next_due` / `last_watch` / `cycle` corrompus | Recales en nombres et persistes |
| Exception de cycle | `error_streak` + backoff `retry_due_after` (2 min, 5 min, 15 min, 30 min), plafonne par le prochain creneau |
| Cycle plus long que 8 minutes | `call_with_deadline` echoue le cycle, lock relache, prochain creneau avance |
| Pause pendant un cycle | Intent conserve, applique a la fin, phase `paused` |

Les cycles live stockent `cycle_pid` et `cycle_started_at`. `peek_loop` lance d'abord `recover_stale_cycle`.

## Corps du cycle

1. Phase `measure`. `run_watch` silencieux (lock deja pris).
2. Phase `learn`. `run_growth_cycle` : scoreboard, gagnants, variantes sur le hook gagnant.
3. Phase `idle` (ou `paused` si Stop a gagne). Ligne journal : gagnants, variantes, alertes, prochain creneau.
4. `error_streak` remis a zero si succes.

Un tick force sur un desk en pause reste **paused** apres le cycle. Il n'active pas la boucle en secret.

## Fichiers du store

Sous `~/.navin/marketing/` (ou le runtime de l'instance) :

| Fichier | Role |
| --- | --- |
| `loop.json` | enabled, phase, schedule, next_due, last_tick, last_watch, cycle, last_result |
| `loop.intent.json` | Start / stop / planning ecrits pendant le lock |
| `desk.lock` | Lock fichier |
| `brand.json`, `product.json`, `campaigns.json`, `content.json`, `creatives.json`, `analytics.json`, `competitors.json`, `journal.json` | Le livre |
| `harvest.json`, `seo.json` | Snapshot du site live et mots-cles / classements mesures |
| `assets/`, `launch/` | Fichiers produits / moissonnes et Markdown de lancement |

## CLI et supervisor

```
navin marketing
python -m navin.marketing.desk_cli start
python -m navin.marketing.desk_cli tick
python -m navin.marketing.desk_cli stop
```

Nom de tache gateway : `navin-marketing-loop`. Les echecs sont logues et isoles. Ils ne sautent jamais Career, Trading, Tenders ou Leads.

Voir aussi [Heartbeat](./heartbeat.md).
