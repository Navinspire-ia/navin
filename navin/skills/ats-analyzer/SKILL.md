---
name: ats-analyzer
description: Score CV-to-job-offer match, identify missing keywords, and check ATS parseability. Use before submitting any application.
metadata: {"navin":{"emoji":"🧮","category":"careers","default_for":"career"}}
---

# ATS Analyzer

## Overview

Simulate what an ATS and a keyword-scanning recruiter see: match score, gaps, and parsing risks.

## Analysis dimensions

### 1. Keyword match
- Extract from the offer: hard skills, tools, certifications, methodologies, soft skills, degree requirements
- Classify each: ✅ present in CV / 🟡 present but different wording / ❌ absent
- Score = weighted coverage (must-haves count triple)

### 2. Wording alignment
Flag synonym mismatches the ATS may miss: "gestion de projet"≠"project management", "JS"≠"JavaScript", acronyms without expansion (write both: "Search Engine Optimization (SEO)").

### 3. Parseability
- Single column? standard section headers? no text boxes/images for text?
- Dates in consistent format? contact info in body (not header/footer)?
- File type: DOCX or text-layer PDF (test: can you select the text?)

## Output format

```markdown
## ATS analysis - <CV> vs <offer>
Match score: X% (must-haves: Y/Z)

| Requirement | Weight | Status | Fix |
|-------------|--------|--------|-----|

### Parsing risks
### Top 5 actions before applying
```

## Workflow

1. Ingest the Career opportunity + Master CV (`career action=status` / import). `pdf-ocr-extractor` only for a user-provided PDF.
2. Build the requirement table; score honestly.
3. For each ❌: is it hidden in the candidate's real experience? → surface it (via `cv-tailoring`). Truly missing? → say so; suggest addressing in the cover letter or skipping the application.

## Rules

- The score guides effort; below ~50% must-have coverage, recommend not applying.
- Never advise keyword-stuffing invisible text or lying - both backfire.
