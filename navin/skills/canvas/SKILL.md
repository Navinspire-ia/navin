---
name: canvas
description: Present standalone analytical results (audits, code review findings, benchmarks, metrics, token usage, PR/CI status, project boards, video QA, investigations, data-heavy comparisons) as a Navin canvas in the Artifact Canvas panel, built from typed JSON blocks with present_artifact type=canvas. Not for architecture diagrams (archify), specific deliverables, or short answers.
metadata: {"navin":{"category":"documents"}}
---

# Canvas

A canvas is a self-contained analytical page beside the chat: stat rows, sortable tables, labelled charts, timelines, findings and diffs, themed like the app. You send **data** (blocks); Navin validates it and renders it with a bundled kit. No HTML to write, no network at view time.

## When to use

Use a canvas when the result is **standalone and analytical**, and the user will scan, sort or compare it:

- audits and code review / security findings (severity, file:line, evidence, fix status)
- performance benchmarks, before/after comparisons
- metrics and usage (token usage, costs, counts over time)
- investigations with several data points, architecture *reviews* with scored findings
- status boards (PR checks, CI, project board, milestones)
- repeatable reports the user will ask for again (same template, new data)

## When not to use

- The user asked for a **specific deliverable** (a docx, a deck, an app, a file in the repo): build that.
- The work happens **in a specific tool** (editor, terminal, browser, board UI): stay there.
- **Active debugging** loops: keep it in the transcript, fast.
- **Short answers**: a sentence or a small Markdown table beats a canvas.
- **Architecture diagrams, PPT visuals, tender/RFP technical answers, Markdown plans**: `archify` stays the default (AGENTS.md). Canvas is for data and analysis; it can sit next to an archify diagram, not replace it.
- Outside the WebUI / desktop (Telegram, CLI): there is no canvas panel; answer in text.

## How to call it

Free-form blocks:

```json
present_artifact({
  "type": "canvas",
  "title": "API latency audit",
  "subtitle": "staging, 2026-10-12 to 2026-10-19",
  "blocks": [
    {"type": "stats", "items": [
      {"label": "Endpoints", "value": 42},
      {"label": "p95 over budget", "value": 3, "tone": "bad"}
    ]},
    {"type": "bar_chart", "title": "p95 by endpoint", "x_label": "Endpoint",
     "y_label": "p95 latency", "unit": "ms", "categories": ["/chat", "/files"],
     "series": [{"name": "Before", "values": [180, 95], "role": "baseline"},
                {"name": "After", "values": [120, 90]}],
     "caption": "Source: k6 run, 5 min at 50 rps. Lower is better."}
  ]
})
```

Templates (Navin data in, blocks out; `navin/artifacts/canvas/templates.py`):

| template | data | source |
|---|---|---|
| `benchmark` | `{"before": report, "after": report, "label_before"?, "label_after"?}` or `data_path` | `scripts/bench_tui.py --json`, `scripts/bench_agent_hotpath.py` |
| `findings` | findings list, or the `security_scan` / `code_review filter` result | review + security tools |
| `pr_status` | `{"pr": forge pr_view, "ci": forge ci_status, "threads"?: [{path, line, resolved, body}]}` | `forge` tool |
| `board` | omit to read the project board | `.navin/board/` |
| `token_usage` | omit to read the usage log; `{"prices": {model: {prompt, completion, cached}}}` (USD per 1M tokens) adds estimated cost | `/api/settings/usage` data |
| `video_qa` | `data_path` to `video-qa.json` | `navin/marketing/video_qa.py` |

```json
present_artifact({"type": "canvas", "template": "video_qa", "data_path": "out/promo-qa/video-qa.json"})
```

Pass the same `id` to update a canvas in place instead of opening a new tab.

## Block types

`section` (title, blocks, description?) | `stats` (items: label, value, unit?, delta?, tone?, hint?) | `table` (columns: key, label, unit?, align?, format? delta/delta_pct, better? lower/higher, mono?; rows; sort?; caption?) | `bar_chart` / `line_chart` (title, x_label, y_label, unit, caption, categories / x, series: name, values, role? baseline, color?; bar orientation? horizontal) | `timeline` (items: date, label, end?, status?, detail?, milestone?, critical?) | `findings` (items: severity critical/high/medium/low/info, title, file?, line?, location?, evidence?, detail?, status?, fix?) | `diff` (before/after: label, text) | `badges` (items: label, tone?) | `text` (text).

Table cells may be `{"badge": "pass", "tone": "good", "sort": 2}`. Tones: good, bad, warn, info, accent, neutral.

Validation rejects: unknown block types, empty lists (a canvas never shows an empty state), charts missing title, axis labels, unit, caption or series names, duplicate series names, mismatched lengths, non-finite numbers.

## Design rules

- Lead with a `stats` row: the 3 to 5 numbers that answer the question.
- One idea per chart. Bars for categories, lines for time. Horizontal bars for long labels (models, file paths).
- Every chart: title says what, axes say units, caption says source and time range. Legend appears automatically for multi-series.
- Compare with `role: "baseline"` (grey) for before/previous and the accent for after/current. No more than 4 series.
- Tables for detail, sorted by what matters (severity, p95, tokens). Numbers are right-aligned automatically.
- Flat and calm: the kit forbids gradients, shadows and emojis; do not try to restyle it. Color means status, not decoration.
- Aggregate before sending: top 12 plus "others", daily not per-request. Max 5000 table rows.
- No em or en dashes in labels; use a hyphen.

## Pre-delivery self-check

1. Would a sentence answer it? Then do not make a canvas.
2. Does the first row of stats answer the user's question on its own?
3. Every chart has title, x_label, y_label with a unit, caption with source and range?
4. Numbers come from tool output or files you read, not estimates? Say "estimated" where they are.
5. Nothing empty, nothing duplicated between a chart and the table beside it without a reason.
6. The tool returned "Presented 1 artifact(s)". If it returned a validation error, fix the listed blocks and call again.

## Introducing it in the reply

One sentence pointing at the canvas plus the key takeaway, then stop: the canvas carries the detail. The first time in a conversation, say what it is: "I put the results in a canvas on the right: a sortable, themed report you can keep open while we work." Do not repeat the canvas content in the transcript.
