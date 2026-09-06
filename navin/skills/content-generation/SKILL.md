---
name: content-generation
description: General-purpose content production - articles, reports, social posts, newsletters, product descriptions, and docs - with channel adaptation and quality control. Use as the entry point for "write me content" requests.
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

Formula: outcome headline → 2-3 benefit bullets (each tied to a feature) → spec table → objection line (guarantee/compat) → CTA. Unique per product - no template stuffing for N products (use `exec` + data like `programmatic-seo` when generating in bulk).

## Visual stack (when the piece is not only words)

| Livrable | Framework | Do not |
|----------|-----------|--------|
| Sales page / landing / site | Super render stack (`ui-ux-pro-max`): Motion + Lenis + Embla + Lucide + `three` + R3F + drei. Always a designed 3D layer. `@number-flow/react` if one KPI ticks. | CSS-only heroes. GSAP. Particles. Swiper. Three.js wallpaper. |
| 3D product / spatial hero | Same Three.js stack. Postprocessing only on that hero. Still fallback. | Three.js as wallpaper. |
| PPT / pitch / board deck | `presentation-designer` + `pptx-generator`. Architecture diagrams via `archify`. CSS motion tokens only. | Three.js or Motion on a slide (becomes a screenshot). |
| Architecture / sequence / plan | `archify` (checked HTML + SVG). | Raw Mermaid as the deliverable. |
| Packshots / 360 | `product-visuals` (`generate_image` / `generate_video`). | Fake 3D in PowerPoint. |
| Studio HTML report | Track A UI: official DS + Motion + Three.js, `open_preview`, not PDF. | File Preview PDF-first. |

Words still follow the routing table. A landing is copy (`copywriting-agent`) plus a real page (Motion). A deck is copy plus the PPT engine. Do not mix the two stacks.

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
