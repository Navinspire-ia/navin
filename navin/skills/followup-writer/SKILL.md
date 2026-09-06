---
name: followup-writer
description: Write J+3 and J+7 recruiter follow-ups from the Career inbox and pipeline. Use when an application is silent or the user asks for a relance.
metadata: {"navin":{"emoji":"📨","category":"careers","default_for":"career"}}
---

# Follow-up Writer

A follow-up is short, dated, and tied to one opportunity in the Career store.

## Workflow

1. `career action=status` then pick the opportunity id (stage applied or replied).
2. `career action=followup id=... wave=j3` or `wave=j7`.
3. Adapt the draft: one reminder of the role, one proof from the Master CV, one clear ask (call or next step).
4. User sends. Gmail/Outlook drafts only when official OAuth is on. Never send on LinkedIn.

## Tone

- J+3: polite bump, 6-8 lines.
- J+7: last ping, leave the door open, then mark next_action and move on.
- After two silences, stop. Update stage if the user agrees.

## Rules

- Do not invent a previous conversation.
- If the inbox already has a recruiter reply, classify it with `career action=inbox` instead of following up.
- Pair with `application-tracker` so every live row has a next action + date.
