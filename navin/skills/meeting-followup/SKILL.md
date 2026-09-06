---
name: meeting-followup
description: Produce meeting minutes, action lists, and follow-up emails within hours of a meeting. Use after every client, prospect, or internal meeting that matters.
metadata: {"navin":{"emoji":"📬","category":"sales"}}
---

# Meeting Follow-up

## Overview

The follow-up IS the meeting's output. Same-day minutes, owned actions, and an email that keeps momentum.

## The three deliverables

### 1. Minutes (internal)
```markdown
## CR - <meeting> <date>
Participants: ...
### Décisions
### Points discutés (facts, verbatim for commitments)
### Actions
| Action | Owner | Deadline |
### Points ouverts / risques
```

### 2. Follow-up email (external, within 24h)
- Thanks (one line, specific)
- "Ce que nous avons retenu" - 3-5 bullets confirming their needs (mirrors their words)
- Agreed next steps with dates and owners
- The ONE attachment/link promised (not five)
- Confirm the next meeting date

### 3. System updates
- CRM activity + stage/next-step (`crm-update-agent`)
- Account sheet update (`account-research`)
- Reminders for each action deadline (`cron`)

## Workflow

1. Input: raw notes, transcript, or voice memo transcription; plus the meeting context.
2. Extract: decisions ≠ discussions ≠ actions - keep them separated.
3. Draft minutes + email; the email is shorter and warmer than the minutes.
4. User validates the external email before sending (`human-approval`); log everything.
5. J+3: if a counterpart's action is pending silently, draft the gentle nudge.

## Rules

- Ambiguous commitments get clarified in the email ("sauf erreur, vous revenez vers nous sur X d'ici vendredi").
- Never put internal commentary in external documents.
- Actions without owner+date don't exist.
