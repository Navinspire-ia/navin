# Tenders actions

Studio `#/tenders`, Tauri, `navin tenders`, `python -m navin.tenders.desk_cli` and the `tenders` tool share the same store.

## Read

| Action | Delivers |
| --- | --- |
| `status` / `snapshot` | Full book: profile, pipeline, loop, book. Call first. |
| `get` | One notice `id=tn-...`. |
| `search` / `list` | Text, country, stage, GO filters. |
| `index` / `file` / `read-file` | Local index and extracts (`dossier.md`, `knowledge.md`, files). |

## Company file

| Action | Delivers |
| --- | --- |
| `profile` | Wizard: name, country, currency, specialty, crafts, sources, thresholds. |
| `knowledge` | Team, price book, methodology, clauses. |
| `upload` | `kind=word_template` / `ppt_template` / `reuse_slide` / `reference`. |
| `add-reference` | Reference card without a file. |
| `remove-file` | Drop a model or reference. |
| `custom-source` / `secret` / `notify` | HTML/API source, SAM key, alert channels. |

Write usage: [Write quality](./write.md).

## Pipeline

| Action | Delivers |
| --- | --- |
| `collect` | Official APIs then search+scrape on public hosts. One portal 404 does not stop the others. |
| `qualify` / `score` / `gonogo` | Score 0-100 + GO/NO-GO. `id` required. |
| `write` / `draft` | Ready dossier: letter, summary, architecture, planning, matrix, Word/PPT pack. |
| `revise` / `review` | Apply your remarks. Stage `validating`. |
| `download` / `export` | Download the generated `.docx` or `.pptx`. |
| `stage` | `discovered` → … → `go` / `no-go` → `drafting` → `submitted` → `won` / `lost`. |
| `mail` | Draft `clarification`, `ack` or `relance`. Does not send. |
| `send` | Desk-channel send only if `approved=true` (default approval). |
| `follow` / `watch` | GO / deadline digest. Heartbeat may only read + follow. |
| `crm-sync` | Push GO notices to the project CRM. |
| `rescore` | Recompute the book after a profile change. |

## Loop (no chat cron)

| Action | Delivers |
| --- | --- |
| `start` | Arm the loop. Needs a finished wizard. `run_now` runs a cycle. |
| `stop` / `pause` | Pause. Always wins, including mid-collect. |
| `schedule` | Change hours (daily, weekdays, weekend, weekly, monthly). |
| `tick` | Forced cycle (collect + watch). Never from heartbeat. |

```
/tenders write the dossier for tn-... using only references in the profile
navin tenders start --kind weekdays --hour 8 --minute 30
navin tenders pause
```

Public filing is on the buyer portal. The loop never sends a buyer mail.
