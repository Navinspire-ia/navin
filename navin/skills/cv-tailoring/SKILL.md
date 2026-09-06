---
name: cv-tailoring
description: Adapt an existing CV to a specific job offer - keyword alignment, reordering, and emphasis, without fabrication. Use for each serious application.
metadata: {"navin":{"emoji":"🎯","category":"careers","default_for":"career"}}
---

# CV Tailoring

## Overview

Same facts, targeted presentation. Mirror the offer's language and priorities so both the ATS and the recruiter see the match instantly.

## Tailoring levers (in order of impact)

1. **Title/summary** - echo the exact job title and top 2 requirements
2. **Bullet reordering** - most relevant achievements first in each role
3. **Keyword alignment** - use the offer's exact terms ("gestion de projet" vs "project management", tool names, methodologies)
4. **Emphasis shift** - expand relevant roles, compress irrelevant ones
5. **Skills section** - reorder to match the offer's requirements order
6. **Cut** - remove noise that dilutes the match

## Workflow

1. Ingest the offer from the Career store (`career action=status` / import paste). `web_fetch` only on open pages, never LinkedIn or another closed board. Use the Master CV as the only fact source.
2. Extract the offer's requirements: must-haves, nice-to-haves, repeated words, culture signals.
3. Run the match analysis (`ats-analyzer` scoring): what's covered, what's missing, what's hidden in the CV.
4. Apply the levers; keep every statement true.
5. Call `career action=prepare` (alias `write` / `cv`). The desk now tailors the Master CV to that mission, writes the cover, and exports a Word pack. `download` returns the `.docx`.
6. Log with `application-tracker` / `career action=apply`.

## Rules

- Fabricating skills or reframing lies is refused - hidden truths are surfaced instead.
- Keep a master CV untouched; each tailored version is a copy named per company.
- 20 minutes of tailoring beats 20 untargeted applications.
