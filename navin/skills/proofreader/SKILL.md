---
name: proofreader
description: Correct grammar, spelling, punctuation, and clarity in French, English, and Arabic without changing the author's voice. Use as the final pass before anything is published or sent.
metadata: {"navin":{"emoji":"🔍","category":"writing"}}
---

# Proofreader

## Overview

Fix errors, tighten sentences, preserve the voice. Correction is not rewriting.

## Pass order

1. **Accuracy** - names, numbers, dates, titles (a wrong name is the worst error)
2. **Grammar & spelling** - agreements, conjugation, homophones (FR: a/à, ces/ses, -er/-é)
3. **Punctuation & typography** - FR: espaces insécables avant `: ; ! ?`, guillemets « », capitales accentuées; EN: serial commas consistent
4. **Clarity** - ambiguous pronouns, double negatives, 40+ word sentences split
5. **Consistency** - tense, register (tu/vous), terminology, formatting of numbers/dates

## Severity levels

| Level | Action |
|-------|--------|
| Error (grammar, typo, wrong fact) | fix silently |
| Clarity issue | fix + note if meaning could shift |
| Style preference | suggest, don't impose |

## Workflow

1. Ask the target language/variant if ambiguous (FR-FR vs FR-DZ register, EN-US vs EN-GB).
2. Correct the text; keep author idioms and rhythm.
3. Return the corrected version + a short list of notable fixes (so the author learns the pattern).
4. For documents, apply corrections in-place via `edit_file` / `docx-generator` round-trip.

## Rules

- Never alter quotes - flag errors inside quotes with [sic] or a note.
- If a sentence is correct but you'd write it differently: leave it.
- Legal/contractual text: flag issues, don't silently rewrite (`contract-reviewer`).
