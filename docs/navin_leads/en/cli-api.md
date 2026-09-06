# CLI, HTTP, and the agent tool

Three entries, **one** `handle_leads_action` (`navin/webui/leads_api.py`), **one** store.

## Terminal

`navin leads` (Typer in `navin/cli/commands.py`) is `python -m navin.leads.desk_cli`.

```bash
navin leads snapshot
navin leads start --kind daily --hour 9 --minute 0
navin leads start --kind weekdays --hour 8 --minute 30 --run-now
navin leads schedule --kind weekend --hour 10 --tz Europe/Paris
navin leads stop
navin leads tick --force
navin leads watch
```

Aliases: `pause` → `stop`, `status` → `snapshot`.

JSON stdin (Vite / older gateway fallback, TTY-safe):

```bash
echo '{"schedule":{"kind":"daily","hour":9}}' | navin leads start
echo '{"schedule":{"kind":"daily","hour":9}}' | python -m navin.leads.desk_cli start
```

Vite (`webui/vite.config.ts`) spawns `python -m navin.leads.desk_cli` when the gateway does not know `/api/leads` yet.

## HTTP

Route `^/api/leads$` in `navin/webui/ws_http.py`. Query `?action=` plus a JSON body.

| Action | Role |
| --- | --- |
| `snapshot` / `status` | State + `loop` + `loop_brief` |
| `probe` | Snapshot + provider probes |
| `profile` / `setup` | Save the ICP |
| `keys` / `secrets` | BYOK (empty fields only at use time) |
| `hunt` / `search` / `discover` | One-shot hunt |
| `enrich` | Enrich one lead (`id`) |
| `start` | Arm the loop (`schedule`, `run_now`, `tz`) |
| `schedule` | Change the hours |
| `stop` | Pause |
| `tick` | One cycle (`force`) |
| `watch` / `follow` | Silent digest |
| `rescore` / `score` | Recompute BANT-F |
| `sequence` / `cadence` | Prepare a cadence (`id`) |
| `lookalike` / `peers` | Lookalikes (`id`) |
| `stage` | Pipeline (`id`, `stage`) |
| `crm` / `push` | Export / HubSpot or Salesforce MCP |
| `outreach` / `send` / `message` | Draft or human send (`send`) |

UI: `webui/src/lib/leads-api.ts` builds `/api/leads?action=`.

## Agent tool `leads`

Same actions. Forwards `schedule`, `run_now`, `tz`, `force`. On heartbeat, the refusal matches the API.

`status` reminds: if the loop is ON, do not hunt again. The sandbox allows `~/.navin/leads`.

Chat command `/leads`: expert brief + skills (see [Skills](./skills.md)). That prepares a CSV / report; the desk loop stays the autonomous calendar.

See also: [Start loop](./loop.md), [Heartbeat](./heartbeat.md), [Desktop](./desktop.md).
