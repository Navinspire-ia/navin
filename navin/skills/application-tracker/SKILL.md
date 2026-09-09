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

1. Prepare a factual CV and cover for the selected job (`career action=prepare`).
2. `career action=apply` opens the manual employer submission flow and leaves the application ready. Mark stage=applied only after the user confirms a completed portal submission.
3. For professional email, configure and test the account using `mail_config` and `mail_test`. `mail_draft` previews the existing CV and cover attachments; `send_email` requires that draft's revision. Report an email application sent only when its receipt has status=accepted and a Message-ID. SMTP acceptance is not a delivery or read confirmation.
4. Automatic email and reply sync have separate persistent opt-ins. Auto-send uses the scheduled Career loop, an explicit published application address, a match threshold and daily limit. Never enable either opt-in from a legacy autopilot setting alone. `sync_mail` attaches actual replies to recorded applications; do not invent responses or send follow-ups automatically.
5. Follow-up policy: `followup-writer` + `career action=followup` wave=j3 then j7. Update the final outcome from the recruiter's response or the user's decision.
6. Schedule reminders with `cron` when requested: follow-ups due, interview prep (trigger `interview-coach` 2 days before), offer deadlines.
7. Weekly review: pipeline stats (applied/response/interview rates), what's stuck, this week's actions.
8. Post-decision: log the outcome and learnings (which channel/CV version converts best).

## Metrics that matter

- Response rate < 10% → fix targeting/CV (`ats-analyzer`)
- Interviews but no offers → fix prep (`interview-coach`)

## Rules

- Every live application has a "next action + date" - no passive rows.
- Keep interview notes verbatim where possible; they're gold for negotiation later.
