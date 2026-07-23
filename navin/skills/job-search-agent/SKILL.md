---
name: job-search-agent
description: Search, filter, and rank job opportunities across boards and company pages, with scheduled watches. Use to run a structured job search.
metadata: {"navin":{"emoji":"🔎","category":"careers"}}
---

# Job Search Agent

## Overview

Run the search systematically: right sources, tight criteria, ranked results, and a recurring watch — quality over volume.

## Search setup

1. **Criteria card** (with the user): target titles (+synonyms FR/EN), sectors, locations/remote, salary floor, deal-breakers
2. **Sources**: LinkedIn Jobs, Indeed, Welcome to the Jungle, regional boards (Emploitic for DZ, Bayt/GulfTalent for Gulf), plus target companies' career pages directly (less competition)
3. **Ranking rubric**: match to criteria (40%), company quality signals (30%), growth potential (20%), process friction (10%)

## Workflow

1. Build the criteria card; store in `career/search-criteria.md`.
2. Search via `web_search` + `web_fetch` on boards and career pages; collect: title, company, location, posted date, link, key requirements.
3. Deduplicate and rank; present the top 5–10 with a one-line "why it fits / watch out" each.
4. For selected offers: run `ats-analyzer` → `cv-tailoring` → `cover-letter-writer`.
5. **Watch mode**: `cron` job (e.g. every 2 days) re-runs the search, reports only NEW postings; log everything in `application-tracker`.

## Ranked output format

```markdown
| # | Role — Company | Location | Fit | Red flags | Deadline | Link |
```

## Rules

- Freshness matters: postings >30 days old get flagged.
- Never auto-apply — preparation yes, submission requires the user (see `human-approval`).
- Watch for scam signals: no company name, pay-to-apply, generic gmail contacts.
