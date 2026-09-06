# CLI, HTTP et outil agent

Trois entrees, **un** `handle_leads_action` (`navin/webui/leads_api.py`), **un** store.

## Terminal

`navin leads` (Typer dans `navin/cli/commands.py`) = `python -m navin.leads.desk_cli`.

```bash
navin leads snapshot
navin leads start --kind daily --hour 9 --minute 0
navin leads start --kind weekdays --hour 8 --minute 30 --run-now
navin leads schedule --kind weekend --hour 10 --tz Europe/Paris
navin leads stop
navin leads tick --force
navin leads watch
```

Alias : `pause` → `stop`, `status` → `snapshot`.

JSON stdin (Vite / fallback gateway trop vieux, TTY-safe) :

```bash
echo '{"schedule":{"kind":"daily","hour":9}}' | navin leads start
echo '{"schedule":{"kind":"daily","hour":9}}' | python -m navin.leads.desk_cli start
```

Vite (`webui/vite.config.ts`) spawn `python -m navin.leads.desk_cli` si le gateway ne connait pas encore `/api/leads`.

## HTTP

Route `^/api/leads$` dans `navin/webui/ws_http.py`. Query `?action=` + corps JSON.

| Action | Role |
| --- | --- |
| `snapshot` / `status` | Etat + `loop` + `loop_brief` |
| `probe` | Snapshot + sondes providers |
| `profile` / `setup` | Sauver l'ICP |
| `keys` / `secrets` | BYOK (champs vides seulement a l'usage) |
| `hunt` / `search` / `discover` | Chasse ponctuelle |
| `enrich` | Enrichir un lead (`id`) |
| `start` | Armer la loop (`schedule`, `run_now`, `tz`) |
| `schedule` | Changer l'horaire |
| `stop` | Pause |
| `tick` | Un cycle (`force`) |
| `watch` / `follow` | Digest silencieux |
| `rescore` / `score` | Recalcul BANT-F |
| `sequence` / `cadence` | Preparer une cadence (`id`) |
| `lookalike` / `peers` | Lookalikes (`id`) |
| `stage` | Pipeline (`id`, `stage`) |
| `crm` / `push` | Export / MCP HubSpot ou Salesforce |
| `outreach` / `send` / `message` | Brouillon ou envoi humain (`send`) |

UI : `webui/src/lib/leads-api.ts` construit `/api/leads?action=`.

## Outil agent `leads`

Meme actions. Transmet `schedule`, `run_now`, `tz`, `force`. Sur heartbeat, le refus est le meme que l'API.

`status` rappelle : si la loop est ON, ne pas hunter. Le sandbox autorise `~/.navin/leads`.

Commande chat `/leads` : brief expert + skills (voir [Skills](./skills.md)). Ca prepare un CSV / rapport ; la loop du desk reste le calendrier autonome.

Voir aussi : [Start loop](./loop.md), [Heartbeat](./heartbeat.md), [Desktop](./desktop.md).
