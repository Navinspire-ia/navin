---
name: cv-builder
description: Generate professional, ATS-compatible CVs from career information — structure, achievement phrasing, and clean formatting. Use to create or rebuild a CV.
metadata: {"navin":{"emoji":"📋","category":"careers"}}
---

# CV Builder

## Overview

Build CVs that pass ATS parsing and convince humans in the 30-second scan.

## ATS-safe rules

- Single column, standard fonts, no tables/text boxes/headers-footers for key info
- Standard section names: Experience, Education, Skills (or FR equivalents)
- File: DOCX or text-layer PDF, named `Prenom-Nom-CV.pdf`
- Keywords from the target job family appear naturally in experience bullets

## Structure

1. **Header** — name, title targeted, city, phone, email, LinkedIn
2. **Summary** — 3 lines: profile + top strengths + target (skip for juniors)
3. **Experience** — reverse chronological; per role: 3–6 achievement bullets
4. **Education / certifications**
5. **Skills** — grouped (technical, languages, tools); honest levels
6. Optional: projects, publications, volunteering

## Achievement bullets (the core craft)

Formula: **action verb + what + measurable result**
- ❌ "Responsable de la migration des données"
- ✅ "Migré 12 systèmes sources vers Supabase (4M lignes) avec 0 perte, réduisant les coûts d'infrastructure de 30%"
No number available? Use scope: team size, budget, users, frequency.

## Workflow

1. Collect raw material: old CV (`pdf-ocr-extractor` if PDF), LinkedIn, or interview the user role by role.
2. Clarify the target role — a CV without a target is a biography.
3. Draft; rewrite every duty into an achievement.
4. Length: 1 page < 8 years experience, 2 pages max otherwise.
5. Produce DOCX via `docx-generator` + PDF export; run `proofreader`.
6. For a specific offer, hand to `cv-tailoring` + `ats-analyzer`.

## Rules

- Never invent experience, dates, or diplomas.
- Gaps: address honestly (formation, freelance, family) rather than hide.
