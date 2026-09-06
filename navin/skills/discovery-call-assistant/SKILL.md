---
name: discovery-call-assistant
description: Prepare discovery call questions, live-call structure, and post-call summaries that feed qualification and proposals. Use before and after prospect meetings.
metadata: {"navin":{"emoji":"🎧","category":"sales"}}
---

# Discovery Call Assistant

## Overview

Discovery quality determines everything downstream: qualification accuracy, proposal relevance, close rate. Prepare hard, listen more than pitch, capture verbatim.

## Pre-call prep (the brief)

1. `account-research` sheet: company, news, tech, likely pains
2. Call goal: what must be true after 30 minutes to advance the deal?
3. Question set (pick 8-10, ordered):

| Theme | Example questions |
|-------|-------------------|
| Trigger | "Qu'est-ce qui fait que vous regardez ça maintenant ?" |
| Pain & cost | "Que se passe-t-il si rien ne change ?" - chiffrer |
| Current state | "Comment faites-vous aujourd'hui ? Qu'est-ce qui coince ?" |
| Decision | "Qui d'autre est impliqué ? Comment se décide un projet comme ça chez vous ?" |
| Budget/timing | "Une enveloppe est-elle définie ? Quelle échéance ?" |
| Success | "À quoi ressemble une réussite dans 6 mois ?" |

## Post-call summary (within 2 hours)

```markdown
## Discovery - <company> <date>
- Participants & roles
- Pain (their words, verbatim quotes)
- Cost of the problem: ...
- Decision process & players
- Budget/timing signals
- BANT-F scores → `lead-qualification`
- Objections heard
- Agreed next step + date
- Follow-up email draft (→ `meeting-followup`)
```

Store in `sales/accounts/<company>/`; sync to CRM (`crm-update-agent`).

## Rules

- 70/30 listening ratio: the question list is a map, not a script.
- Never leave a call without a dated next step agreed aloud.
- Verbatim beats paraphrase - proposals reuse their exact words.
