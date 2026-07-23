---
name: cv-tailoring
description: Adapt an existing CV to a specific job offer — keyword alignment, reordering, and emphasis, without fabrication. Use for each serious application.
metadata: {"navin":{"emoji":"🎯","category":"careers"}}
---

# CV Tailoring

## Overview

Same facts, targeted presentation. Mirror the offer's language and priorities so both the ATS and the recruiter see the match instantly.

## Tailoring levers (in order of impact)

1. **Title/summary** — echo the exact job title and top 2 requirements
2. **Bullet reordering** — most relevant achievements first in each role
3. **Keyword alignment** — use the offer's exact terms ("gestion de projet" vs "project management", tool names, methodologies)
4. **Emphasis shift** — expand relevant roles, compress irrelevant ones
5. **Skills section** — reorder to match the offer's requirements order
6. **Cut** — remove noise that dilutes the match

## Workflow

1. Ingest the offer (URL via `web_fetch` or pasted text) and the base CV.
2. Extract the offer's requirements: must-haves, nice-to-haves, repeated words, culture signals.
3. Run the match analysis (`ats-analyzer` scoring): what's covered, what's missing, what's hidden in the CV.
4. Apply the levers; keep every statement true.
5. Deliver: tailored CV (via `docx-generator`) + a 5-line match summary the user can reuse in the application.
6. Log the application in `application-tracker` if the user tracks their search.

## Rules

- Fabricating skills or reframing lies is refused — hidden truths are surfaced instead.
- Keep a master CV untouched; each tailored version is a copy named per company.
- 20 minutes of tailoring beats 20 untargeted applications.
