# CLI et surfaces

Toutes les surfaces d'un desk parlent au meme handle (`handle_tenders_action` / `handle_career_action` / `handle_trading_action` / `handle_marketing_action`).

## Commandes

| Action | Tenders | Career | Trading | Marketing |
| --- | --- | --- | --- | --- |
| Snapshot | `navin tenders snapshot` | `navin career snapshot` | `navin trading snapshot` | `navin marketing snapshot` |
| Start | `navin tenders start --kind daily --hour 9` | `navin career start --kind daily --hour 9` | `navin trading start --kind daily --hour 9` | `navin marketing start --kind daily --hour 9` |
| Stop / pause | `navin tenders stop` (alias `pause`) | `navin career stop` (alias `pause`) | `navin trading stop` (alias `pause`) | `navin marketing stop` (alias `pause`) |
| Horaires | `navin tenders schedule --kind weekdays --hour 8` | `navin career schedule --kind weekdays --hour 8` | `navin trading schedule --kind weekdays --hour 8` | `navin marketing schedule --kind weekdays --hour 8` |
| Tick (force defaut HTTP) | `navin tenders tick` | `navin career tick` | `navin trading tick` | `navin marketing tick` |
| Watch | `navin tenders watch` | `navin career watch` | `navin trading watch` | `navin marketing watch` |
| Write | `tenders action=write id=tn-...` | `career action=prepare id=job-...` | - | - |
| Revise | `tenders action=revise id=tn-... remarks=...` | - | - | - |
| Download | `tenders action=download id=tn-... kind=docx` | `career action=download id=job-...` | - | - |

Equivalent : `python -m navin.tenders.desk_cli` / `python -m navin.career.desk_cli` / `python -m navin.trading.desk_cli` / `python -m navin.marketing.desk_cli`. Write : [tenders-write.md](./tenders-write.md) · [career-write.md](./career-write.md). Marketing produit : [marketing.md](./marketing.md).

Flags start/schedule : `--kind` `daily|weekdays|weekend|weekly|monthly`, `--hour`, `--minute`, `--weekday`, `--day`, `--tz`, `--run-now`, `--force`.

Pipe Vite / Tauri :

```text
echo '{"schedule":{"kind":"daily","hour":9},"run_now":false}' | navin tenders start
echo '{"schedule":{"kind":"daily","hour":9},"run_now":false}' | navin career start
echo '{"schedule":{"kind":"daily","hour":9},"run_now":false}' | navin trading start
echo '{"schedule":{"kind":"daily","hour":9},"run_now":false}' | navin marketing start
```

## HTTP

- `POST /api/tenders?action=...`
- `POST /api/career?action=...`
- `POST /api/trading?action=...`
- `POST /api/marketing?action=...`

`tick` sans body : `force=true`. Heartbeat : allow-list uniquement.

## Studio / Tauri

Boutons Start / Pause / Tick (`force: true`) dans `TendersWorkspace`, `CareerWorkspace`, `TradingWorkspace` et `MarketingWorkspace` (`marketing-start-loop`, `marketing-pause-loop`, `marketing-tick-loop`).

Hash interne : `#/tenders`, `#/career`, `#/trading`, `#/marketing`. Tauri reste sur le hash (Linux `http://tauri.localhost`, gateway `127.0.0.1:8766`, macOS `tauri://localhost`). Les boards Career officielles s'ouvrent via `openOfficialCareerUrl` (ouvreur OS), pas un `window.open` dans le webview.

## Outil agent

Outils `tenders`, `career`, `trading` et `marketing`. En tour heartbeat, les actions mutantes sont refusees avec un message qui cite heartbeat.

Seeds chat EN/FR : `thread.sessionInfo.createSeed.tenders` / `.career` / `.trading` / `.marketing` / `.montage`. Interdiction explicite de creer une cron de chat.

## Fichiers code

| Role | Tenders | Career | Trading | Marketing |
| --- | --- | --- | --- | --- |
| Loop | `navin/tenders/loop.py` | `navin/career/loop.py` | `navin/trading/loop.py` | `navin/marketing/loop.py` |
| Heartbeat | `navin/tenders/heartbeat.py` | `navin/career/heartbeat.py` | `navin/trading/heartbeat.py` | `navin/marketing/heartbeat.py` |
| Watch | `navin/tenders/watch.py` | `navin/career/watch.py` | `navin/trading/watch.py` | `navin/marketing/watch.py` |
| Write | `navin/tenders/writer.py` + `ai.py` | `navin/career/writer.py` + `export.py` | - | briefs + templates (`desk.py`) |
| Lock | `navin/tenders/lock.py` | `navin/career/lock.py` | `navin/trading/lock.py` | `navin/marketing/lock.py` |
| API | `navin/webui/tenders_api.py` | `navin/webui/career_api.py` | `navin/webui/trading_api.py` | `navin/webui/marketing_desk_api.py` |
| CLI | `navin/tenders/desk_cli.py` | `navin/career/desk_cli.py` | `navin/trading/desk_cli.py` | `navin/marketing/desk_cli.py` |
| Supervisor | `navin-tenders-loop` | `navin-career-loop` | `navin-trading-loop` | `navin-marketing-loop` |
| Tick desks | `navin/gateway/heartbeat_desks.py` | idem | idem | idem |
| Deadline | `navin/tenders/loop.py` | `navin/loop_runtime.py` | `navin/loop_runtime.py` | `navin/loop_runtime.py` |
| HEARTBEAT.md | section Tenders | section Career | section Trading | section Marketing winners |
