---
name: account-research
description: Build a complete account sheet before a meeting — company, people, news, tech, pains, and talking angles. Use before any important prospect or client interaction.
metadata: {"navin":{"emoji":"🗺️","category":"sales"}}
---

# Account Research

## Overview

Walk into every meeting knowing more about their context than they expect. 30–45 minutes of research, one structured sheet.

## The account sheet

```markdown
## Account — <Company> (<date>)
### Company
- What they do (their own words) — size, geography, structure
- Business model & who THEIR clients are
- Recent news: funding, projects, expansions, incidents
### People (per attendee)
- Role, tenure, background, recent posts/statements
- Likely personal win from this project
### Signals
- Tech stack hints (site, job postings)
- Hiring patterns → priorities
- Public tenders / projects (DZ/Gulf: BAOSEM, official portals)
### Hypotheses
- Top 3 likely pains (with the evidence)
- Budget/timing signals
### Angles
- Openers & smart questions
- Relevant references/cases of ours
- Risks (competitor incumbent? bad history?)
```

## Sources

`web_fetch` their site (about, news, careers) · `web_search` news + "{company} + projet/contrat/recrutement" · LinkedIn (company + people) · reviews (their clients') · tender platforms · `entity-research` for deep corporate data.

## Workflow

1. Timebox by stakes: 15 min (routine) / 45 min (big meeting) / half-day (strategic account).
2. Fill the sheet; separate facts (sourced) from hypotheses (marked).
3. Store in `sales/accounts/<company>/research.md`; brief the user with the top 5 points.
4. After the meeting: `discovery-call-assistant` notes update the sheet — it lives.

## Rules

- Public sources only.
- Hypotheses are for the meeting to verify — never present them as facts to the prospect.
