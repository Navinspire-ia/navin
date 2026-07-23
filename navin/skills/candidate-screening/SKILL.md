---
name: candidate-screening
description: Analyze, score, and compare CVs against a role's criteria grid — consistently and fairly. Use when reviewing applications for a position.
metadata: {"navin":{"emoji":"⚖️","category":"careers"}}
---

# Candidate Screening

## Overview

Screen every CV against the same explicit grid, derived from the job description — consistent, auditable, bias-aware.

## Screening grid (built once per role)

From the job description (`job-description-writer` output):

```markdown
| Criterion | Type | Weight | Evidence to look for |
|-----------|------|--------|----------------------|
| <must-have 1> | eliminatory | — | ... |
| <skill> | scored /5 | 3 | ... |
```

## Scoring process

1. Parse each CV (`pdf-ocr-extractor` for PDFs; batch via `exec` when many).
2. Eliminatory pass first: missing must-haves → out (with reason logged).
3. Score the rest per criterion, citing the CV evidence for each score — no gut numbers.
4. Flags (not eliminatory, to probe in interview): unexplained gaps, job hopping pattern, title inflation, vague achievements.
5. Rank and produce the comparison table.

## Output format

```markdown
## Screening — <role> (<N> candidates)
| Candidate | Score | Must-haves | Strengths | Probe in interview | Verdict |

### Recommended shortlist (3–5)
```

## Bias guards

- Score only job-relevant criteria; ignore name, age, photo, address, école prestige beyond the requirement
- Same grid, same order for every candidate
- "Culture fit" must be defined in observable terms or not used

## Rules

- Every rejection has a stated criterion-based reason.
- The grid can't change mid-screening; if it must, re-screen everyone.
- Final decisions are human — this produces a ranked recommendation (`recruitment-agent` for the full pipeline).
