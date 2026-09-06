---
name: application-tracker
description: Track job applications - statuses, follow-ups, interviews, and reminders - in a structured pipeline. Use to keep a job search organized.
metadata: {"navin":{"emoji":"🗂️","category":"careers","default_for":"career"}}
---

# Application Tracker

## Overview

A job search is a pipeline. The live book is the Career store (`career` tool + `#/career`). Optional markdown notes are a backup, never a second source of truth.

## Pipeline stages

`discovered → matched → ready → applied → replied → interview → offer → won / rejected`

## Tracker format

Prefer `career action=status` and `career action=stage`. The desk already writes `applications.md` next to the store on every save; read it with `career action=read file=applications.md`. Shape:

```markdown
| Company | Role | Applied | Stage | Last contact | Next action | Due | Notes |
```

Per-application notes file for serious processes: contacts, interview notes, questions asked, salary discussed.

## Workflow

1. Log each application at submission (`career action=apply` or stage=applied).
2. Follow-up policy: `followup-writer` + `career action=followup` wave=j3 then j7; then mark rejected/ghosted and move on.
3. Schedule reminders with `cron` for: follow-ups due, interview prep (trigger `interview-coach` 2 days before), offer deadlines.
4. Weekly review: pipeline stats (applied/response/interview rates), what's stuck, this week's actions.
5. Post-decision: log the outcome and learnings (which channel/CV version converts best).

## Metrics that matter

- Response rate < 10% → fix targeting/CV (`ats-analyzer`)
- Interviews but no offers → fix prep (`interview-coach`)

## Rules

- Every live application has a "next action + date" - no passive rows.
- Keep interview notes verbatim where possible; they're gold for negotiation later.
