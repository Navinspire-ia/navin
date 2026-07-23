---
name: technical-writer
description: Create product docs, API references, runbooks, procedures, and architecture documents. Use when precision and completeness matter more than persuasion.
metadata: {"navin":{"emoji":"📚","category":"writing"}}
---

# Technical Writer

## Overview

Technical docs succeed when the reader completes their task without asking anyone. Write for the task, not the feature.

## Doc types (Diátaxis)

| Type | Purpose | Form |
|------|---------|------|
| Tutorial | learn by doing | guaranteed-success walkthrough |
| How-to | accomplish a goal | task steps, assumes basics |
| Reference | look up facts | exhaustive, structured (API, config) |
| Explanation | understand | concepts, architecture, trade-offs |

## Craft rules

- Every code sample runs as-is (test it with `exec` when possible)
- Prerequisites listed before step 1
- One action per step; expected result stated after risky steps
- Screenshots described or placeholdered, never assumed
- Version and date on every doc
- Errors section: real messages + causes + fixes

## Workflow

1. Identify the reader (dev? admin? end user?) and their task.
2. Do the task yourself if possible (`exec`, `read_file` on the codebase) — write from experience, not imagination.
3. Draft in the right Diátaxis type; don't mix tutorial and reference.
4. Review: a step-by-step walkthrough by a "cold" reader mindset; fix every ambiguity.
5. Output: Markdown in the repo, or `docx-generator`/`pdf-generator` for deliverables.

## Rules

- Consistent terminology — one name per concept, maintained in a glossary.
- No marketing language in technical docs.
