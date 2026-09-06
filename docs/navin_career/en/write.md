# Write quality per mission

The Master CV is the only source of truth. `prepare` (aliases `write` / `cv` / `tailor`) delivers a **ready-to-review pack for this offer**: reordered text, cover, ATS notes, Word file. You study it, fix gaps in the master if they are real, then apply on the **employer page**. Nothing is invented. It is not Easy Apply.

Action: `career action=prepare id=job-...` (Studio `#/career`, Tauri, CLI, chat). The loop and the heartbeat **never write** and **never apply**.

## What prepare delivers

| Block | Source | Model polish? |
| --- | --- | --- |
| CV text | Master CV + experiences + stack + education | Only if `ai_assist` is **true** |
| Aligned skills | Intersection of offer keywords and the file | No |
| Other on-file skills | Profile stack not in the offer | No |
| Experiences | Profile cards, reordered toward the posting | No (order only) |
| Cover / message | Title + company + facts already on the CV | Only if `ai_assist` is **true** |
| ATS notes | Matched keywords + missing terms (left out) | No |
| Word pack | `python-docx`: CV + letter + notes | No |
| Apply | Opens the official URL; you submit | No |

Language: **FR** if the first profile language starts with `fr`, or the offer / residence country is FR, BE, LU, MC, CH. Otherwise **EN**.

## What you must put on file

| Field | Role in prepare |
| --- | --- |
| Name, email, phone | CV header |
| Titles, headline | Banner |
| Stack, strengths, highlights | Alignment and opening lines |
| Experiences (title, company, period, facts) | Body, scored against the offer |
| Education | Education block, never invented |
| Master CV (text) | Paragraphs reordered toward the posting |
| Countries, languages | Pack locale |

Without a master **and** without experiences **and** without a stack, the pack says `Master CV is not on file`. `apply` refuses until `cv_text` is present.

The desk writes a `.docx` under `~/.navin/career/files/`. Download: `career action=download id=job-...`. There is no company Word template (unlike Tenders). Each prepare generates a clean file.

## How the model works

1. `build_pack` builds a **deterministic** draft. Offer terms missing from the file stay in `keywords_missing` and are **not added** to the CV.
2. AI polish runs only when `ai_assist` is **true** and `NAVIN_CAREER_AI` / `NAVIN_TENDERS_AI` is not `off`. Default: template only.
3. Guard: no invented employer, date, tool or diploma. Otherwise the desk **keeps the template**.
4. A Word pack is written. Stage `ready`. Next action: review, then open the original URL.
5. `apply` opens the official page. LinkedIn + autopilot is refused.

## What prepare does not do

- No employer submit. You paste / upload on their page.
- No Easy Apply, no LinkedIn scrape.
- No stack, role or certificate missing from the Master CV.
- No write or apply from the loop or the heartbeat.
- No chat cron that prepares or applies.

The deliverable is **ready to review for this mission**. You do not rewrite from scratch.

## How to get a good pack

1. Finish the profile wizard (title + countries + stack).
2. A readable master CV **and** 3-8 dated experiences.
3. A real stack, not a wishlist.
4. Route the **docs** role only if you opt in to `ai_assist`.
5. `career action=status` then `prepare id=job-...`. Check `keywords_matched`, `keywords_missing`, `pack_ready`.
6. Studio: offer card → Adapt the CV to this mission → review → download Word → Apply (employer page).

## Agent

```
/career
career action=status
career action=get id=job-...
career action=prepare id=job-...
career action=download id=job-...
career action=apply id=job-...
```

Skills `career-cv` / `cv-tailoring`: reorder and echo the posting. Never add an employer or tool that is not on the master.

Opt in to AI: profile `ai_assist: true`. Turn off: leave it false, or set `NAVIN_CAREER_AI=off`.
