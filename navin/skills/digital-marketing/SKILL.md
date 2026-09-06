---
name: digital-marketing
description: Full-funnel marketing orchestrator - strategy, content, SEO, social, email, paid, and measurement. Use as the entry point for any broad marketing request, then delegate to specialist skills.
metadata: {"navin":{"emoji":"🎯","category":"marketing"}}
---

# Digital Marketing (Orchestrator)

## Overview

Entry point for marketing work. Diagnose the funnel stage, pick the right specialist skills, and keep everything coherent with strategy and brand voice.

## The funnel map

| Stage | Goal | Specialist skills |
|-------|------|-------------------|
| Strategy | positioning, ICP, plan | `marketing-strategist`, `customer-persona-builder`, `market-research` |
| Awareness | reach the ICP | `seo-content-writer`, `social-media-manager`, `paid-ads-manager` |
| Consideration | educate, compare | `blog-writer`, `case-study-writer`, `email-marketing` |
| Conversion | turn interest into pipeline | `conversion-rate-optimization`, `copywriting-agent`, `sales-proposal-writer` |
| Retention | expand and retain | `email-marketing`, `kpi-reporter` |
| Measurement | learn and reallocate | `marketing-analytics`, `campaign-manager` |

## Workflow

1. Clarify: objective (leads? awareness? launch?), audience, budget, timeline, existing assets.
2. Diagnose the weakest funnel stage - that's where effort goes first.
3. Draft a one-page plan: objective, 2-3 channels max, key messages, calendar, KPIs.
4. Execute through specialist skills; keep `brand-voice-manager` rules on all output.
5. Review results with `marketing-analytics`; double down or kill.

## Super render (mandatory in the Marketing module)

This is the visual bar for `/campaign`. Load `ui-ux-pro-max` for pages and `presentation-designer` for decks.

**Landing / site / campaign page.** Install and use:

```bash
npm install framer-motion lenis embla-carousel-react lucide-react three @react-three/fiber @react-three/drei
```

Always install Three.js (`three` + `@react-three/fiber` + `@react-three/drei`). Run ui-ux-pro-max `--stack threejs`. Designed 3D layer (hero / product / spatial chrome), still fallback, never wallpaper. `@number-flow/react` for one ticking KPI. `recharts` for a real data section.

**Pitch / sales / board PPTX.** Nothing more in npm. Super render is poster type, real photos, screenshot chrome, native charts, light/dark rhythm. A Lenis or Three.js canvas on a slide becomes an image. That file is rejected.

**Do not install for wow:** GSAP, Locomotive, Spline, Lottie everywhere, particles / tsParticles, Three.js as wallpaper, Theatre, Barba, Swiper. They bloat the page, copy Open Design, and do not make the file better.

**Product imagery.** `product-visuals`. A 360 is a short video or a Three.js page, not a rasterized slide.

**Image / video / sound / clips.** Marketing stays on the product and the brand. Generation uses the Montage toolbelt: `generate_image`, `generate_video`, `generate_music`, `generate_speech`, `montage assemble/package/render`. Attached library templates (Stock, Style, Character, Element, Location, Structure, Color, Effects, Camera × Portraits, Expressions, Photoshoots, Food, Backgrounds, Environments, Nature, Atmosphere) are mandatory references when present.

## Rules

- Never propose 7 channels at once - focus wins.
- Every action gets a measurable KPI before launch.
- B2B (Navinspire-style): LinkedIn + SEO + email usually beat everything else.
