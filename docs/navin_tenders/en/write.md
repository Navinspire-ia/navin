# Write quality per notice

If the company file holds **references**, **reuse slides** and **examples** (Word/PPT models), `write` delivers a **ready-to-review reply**: text + Word/PPT pack + matrix + architecture + planning. You study it, leave remarks if needed, then approve. It is still not an automatic portal filing. Nothing is invented.

Action: `tenders action=write id=tn-...` (Studio, Tauri, CLI, chat). The loop and the heartbeat **never write**.

## What write delivers

| Block | Source | Model polish? |
| --- | --- | --- |
| Cover letter | Profile + notice + model extracts | Yes (`write` route → Settings role **docs**) |
| Executive summary | Profile + notice + award reading | Yes (same call) |
| References | Cards and/or extracts from `reference` files | No (list + extracts) |
| Methodology | `methodology` field + Word/PPT/slide extracts | No (concatenated) |
| Staffing | Team on file | No |
| Architecture | Crafts + method + extracts | Yes (second pass) |
| Planning | Deadline + effort + team | Yes (second pass) |
| Compliance matrix | One row per extracted requirement | No (file mapping) |
| Word / PPT pack | Filled model or generated dossier | No |
| Review | `revise` + remarks, stage `validating` | Yes if docs routed |
| Price schedule | A sentence if `price_book` is set, else `not on file` | No |

Language: **FR** for a francophone notice country, **EN** otherwise, else the profile locale.

## What you must put on file

### Config / wizard / `knowledge`

| Field | Role in write |
| --- | --- |
| Name, specialty, strengths | Opening lines |
| Crafts, tender types, project types | Scope and matrix |
| Methodology | Technical body when present |
| Team | Staffing |
| Price book | Financial mention (no invented unit prices) |
| Certifications | Score / gaps; never invented in the letter |
| Typed references (title, client, year, country, amount) | Reference list even without a file |

### Uploaded files (Studio wizard)

| Kind | Formats | Role |
| --- | --- | --- |
| `word_template` | `.docx` | Answer model. **Text** is extracted and reused. |
| `ppt_template` | `.pptx` | Same for a slide deck model. |
| `reuse_slide` | `.docx`, `.pptx`, `.pdf` | Typical slides / excerpts to reuse. |
| `reference` | `.docx`, `.pptx`, `.pdf` | Case studies, attestations, past bids. |

Actions: `tenders action=upload kind=...` or `add-reference` (card, no file).

Extract limits:

- 8 MB per file
- 40 PDF pages or 40 PPT slides
- excerpt stored up to **12 000** characters
- the polish pass sees up to **1 500** characters per file
- a scan with no text layer yields an empty excerpt (write says so; it does not invent the page)

## How the model works

1. `build_response` builds a **deterministic** draft (profile + notice + extracts). Gaps stay `not on file` / `non renseigne`.
2. If AI is on (`ai_assist` not false, `NAVIN_TENDERS_AI` not `off`) **and** a preset is routed for the **docs** role, `polish_response` rewrites letter + summary, then architecture / planning / method.
3. Guard: every figure in the rewrite must already be in the material. Gap markers must stay visible. Otherwise the desk **keeps the template**. Nothing is invented.
4. A Word/PPT pack is written. `revise` applies your remarks. You review a ready draft.

GO/NO-GO is not a model opinion: scoring rules decide. The **deep** role (`qualify`) only explains the verdict.

## What write does not do

- No buyer send. `send` stays under approval. You file on the portal.
- No invented figure, client, certificate or extra date.
- A title-only notice is enriched from `source_url`. If the page is empty, the dossier stays structured with visible gaps.

The deliverable is **ready to review**: you study it, leave remarks if needed, then approve.

## How to get a good draft

1. Finish the company wizard (specialty + crafts + countries).
2. File 3-8 precise references (client, year, public amount) **and** an extractable PDF/DOCX for each strong one.
3. One Word model and 2-5 reuse slides (method, org, typical references).
4. Team + methodology + price book if you want those blocks filled.
5. Settings → Models → Task routing: **docs** for write/mail, **deep** for qualify.
6. `tenders action=status` then `knowledge` then `write id=tn-...`. Check `used_files` and `from_file` on the draft.
7. You complete prices, planning, regulation exhibits, then file on the portal.

The more readable the extracts, the more the letter is **specific to that notice**. Empty scans = honest, dry template.

## Agent in chat

```
/tenders
tenders action=status
tenders action=knowledge
tenders action=get id=tn-...
tenders action=write id=tn-...
```

`tender-agent` requires: if models or references are on file, write **must** reuse their extracts. `rfp-writer` may expand the reply in chat; it must not add a client or certificate that is missing.

Heartbeat: 403 on start / stop / schedule / tick / collect / write / send.

## Turn AI off (template only)

- Profile: `ai_assist: false`
- Env: `NAVIN_TENDERS_AI=off`

Write still runs. The letter is not rewritten.
