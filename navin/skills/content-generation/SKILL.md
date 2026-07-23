---
name: content-generation
description: General-purpose content production — articles, reports, social posts, newsletters, product descriptions, and docs — with channel adaptation and quality control. Use as the entry point for "write me content" requests.
metadata: {"navin":{"emoji":"🧾","category":"writing"}}
---

# Content Generation

## Overview

Router + workhorse for content requests. Identify the content type, apply the right specialist method, and run quality control before delivery.

## Routing table

| Request | Specialist method |
|---------|-------------------|
| SEO article | `seo-content-writer` |
| Editorial/blog | `blog-writer` |
| Sales page / ad | `copywriting-agent` |
| Social posts | `social-media-manager` |
| Newsletter/email | `email-marketing`, `email-writer` |
| Case study | `case-study-writer` |
| Docs/procedures | `technical-writer` |
| Formal documents | `professional-writer` |
| Product descriptions | below |

## Product descriptions

Formula: outcome headline → 2–3 benefit bullets (each tied to a feature) → spec table → objection line (guarantee/compat) → CTA. Unique per product — no template stuffing for N products (use `exec` + data like `programmatic-seo` when generating in bulk).

## Quality control (every piece)

1. Voice check against `brand-voice-manager` card
2. Facts pass (`fact-checker`) for any claim/number
3. Language pass (`proofreader`)
4. Channel fit: length, format, register for where it will live

## Workflow

1. Clarify: type, audience, goal, language, length, deadline, where it will be published.
2. Route to the right method; produce a draft.
3. Run the four QC checks.
4. Deliver + offer derivatives (`content-recycler`) when the piece is long-form.

## Rules

- Never deliver first-draft quality; QC is part of the job.
- Batch requests get a shared style/terminology check across the whole batch.
