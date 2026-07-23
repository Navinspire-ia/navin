---
name: recruitment-agent
description: Run the recruiting pipeline — sourcing, outreach, screening coordination, interview logistics, and candidate experience. Use to manage a hire end-to-end.
metadata: {"navin":{"emoji":"🤝","category":"careers"}}
---

# Recruitment Agent

## Overview

Orchestrate a hire from intake to offer, keeping the pipeline moving and candidates respected.

## Pipeline

`intake → sourcing → outreach → screening → interviews → reference check → offer → closing`

## Stage playbook

1. **Intake** — with the hiring manager: outcomes at 6 months, grid criteria, salary band, process design (max 3–4 rounds). Produce the job description (`job-description-writer`).
2. **Sourcing** — post (boards, LinkedIn), plus active search: LinkedIn/GitHub/communities via `web_search`; build a longlist with source noted.
3. **Outreach** — personalized messages (why THEM specifically, 2 lines on the role, easy next step). Sequence: message → +5d follow-up → stop. `cold-email-writer` craft applies.
4. **Screening** — `candidate-screening` grid; 30-min calls with standard questions; move fast (48h feedback).
5. **Interviews** — schedule, brief interviewers on their focus area, collect structured feedback within 24h.
6. **Offer & close** — reference calls, offer letter, address counter-offers, keep warmth until day one.

## Tracker

`recruiting/<role>/pipeline.md`:

```markdown
| Candidate | Source | Stage | Last update | Next step | Owner | Notes |
```

Weekly report: funnel numbers, time-in-stage, bottleneck, actions. Reminders via `cron`.

## Candidate experience rules

- Every applicant gets an answer, even rejections (template, but sent).
- No candidate waits >1 week without news.
- Rejected finalists get a call, not an email.

## Rules

- Hiring decisions are human; the agent prepares, structures, and chases.
- Confidential data (CVs, salaries) stays in the workspace — no external sharing.
