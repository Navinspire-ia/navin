---
name: application-tracker
description: Track job applications — statuses, follow-ups, interviews, and reminders — in a structured pipeline. Use to keep a job search organized.
metadata: {"navin":{"emoji":"🗂️","category":"careers"}}
---

# Application Tracker

## Overview

A job search is a pipeline. Track every application's stage, next action, and deadline so nothing dies of silence.

## Pipeline stages

`identified → applied → screening → interview 1..n → offer → accepted/declined/rejected/ghosted`

## Tracker format

Store in `career/applications.md` (or CSV for `spreadsheet-analyst` analysis):

```markdown
| Company | Role | Applied | Stage | Last contact | Next action | Due | Notes |
```

Per-application notes file for serious processes: contacts, interview notes, questions asked, salary discussed.

## Workflow

1. Log each application at submission (auto when created via `job-search-agent` flow).
2. Follow-up policy: no response after 7–10 business days → polite follow-up (`email-writer` pattern); one more at +7; then mark ghosted and move on.
3. Schedule reminders with `cron` for: follow-ups due, interview prep (trigger `interview-coach` 2 days before), offer deadlines.
4. Weekly review: pipeline stats (applied/response/interview rates), what's stuck, this week's actions.
5. Post-decision: log the outcome and learnings (which channel/CV version converts best).

## Metrics that matter

- Response rate < 10% → fix targeting/CV (`ats-analyzer`)
- Interviews but no offers → fix prep (`interview-coach`)

## Rules

- Every live application has a "next action + date" — no passive rows.
- Keep interview notes verbatim where possible; they're gold for negotiation later.
