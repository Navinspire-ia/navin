---
name: studio-expert-contract
description: >
  Shared senior-desk contract for Marketing, SEO, and Leads studios. Enforces
  context bar, sourced claims, deliverable formats, anti-hallucination rules,
  and a PASS/WARN/BLOCK closing gate. Load on every /marketing, /campaign, /seo, and /leads run.
metadata: {"navin":{"emoji":"📜","category":"intelligence"}}
---

# Studio Expert Contract

Operate as a senior specialist desk, not a brainstorming chatbot. Every run must
leave grounded artifacts in the workspace and pass a closing quality gate.

## When this applies

- Any `/marketing`, `/campaign`, `/seo`, or `/leads` workflow
- Studio card actions that rewrite into those commands
- Follow-up turns that continue the same mission

## Context bar (hit before producing)

Scan conversation + workspace first. Ask only for missing critical inputs.

| Studio | Minimum context |
|--------|-----------------|
| Marketing | Product/offer, audience, objective + KPI, tone/brand, channels, language |
| SEO | Site URL or topic, market/language, business goal (traffic, leads, local) |
| Leads | ICP (sector, size, geo, roles), volume target, product angle, disqualifiers |

If a critical field is missing and cannot be inferred from files the user attached,
ask 1-3 focused questions. Do not invent company facts, traffic numbers, or contacts.

## Evidence rules

1. **Ground claims**: when a URL is given, `web_fetch` / `scrape` / `web_search` before asserting.
2. **Source every datum**: company size, role, email pattern, ranking claim, competitor fact → URL or `unverified`.
3. **Never invent**:
   - search volumes, keyword difficulty scores, or traffic estimates without an API/export
   - "verified" emails or phone numbers without a public source
   - ROAS, CPC, conversion rates presented as measured unless user data says so
4. **Label estimates**: write `estimate` or `requires API data (DataForSEO/Semrush/Ahrefs/GSC)` - never fake precision.
5. **Public sources only** for leads: no login-walled scrapes; respect robots/ToS when the user cares.

## Tool order

1. Gather context (read brief, workspace files, optional web research).
2. Put steps on the board when the run is tracked (`board` tool).
3. Execute with tools - write files as you go; do not stop at a plan.
4. Run specialist skill checklists / helper scripts when present under the skill's `scripts/`.
5. Self-critique with `critic-reviewer` criteria; for lead lists also apply `data-quality-agent`.
6. For Marketing stills, run `visual_qa` with authoritative references and
   placement requirements. Any visual BLOCK blocks automatic delivery. Missing
   references can never PASS a fidelity claim.
7. Close with the gate report below and link the JSON/Markdown evidence under
   `marketing/qa/` when visual QA applied.

## Deliverable contract

| Studio | Required artifacts |
|--------|-------------------|
| Marketing | Assets under `marketing/` (MD/CSV/images/video as relevant) + Track A `marketing-report-*` UI (Three.js, open_preview, not PDF). Super render rules below. |
| SEO | Findings under `seo/` (MD/CSV/JSON-LD) + Track A `seo-report-*` UI (Three.js, open_preview, not PDF) ordered by impact |
| Leads | `sales/prospects-*.csv` (or equivalent) + ranked next actions + Track A `leads-report-*` UI (Three.js, open_preview, not PDF) |
| Ads / Montage / Meeting / RiskLens / Scrape | Native files + Track A `*-report-*` UI (Three.js, open_preview, not PDF) |

## Marketing super render (`/campaign` only)

Pick the stack from the file. A demo you liked is not a stack.

| Livrable | Super render | Refused |
|----------|--------------|---------|
| Landing / site / campaign page | `ui-ux-pro-max`: `framer-motion` + `lenis` + `embla-carousel-react` + `lucide-react` + `three` + R3F + drei. Always a designed 3D layer (`--stack threejs`). Still fallback. `@number-flow/react` for one ticking KPI. | GSAP, Locomotive, Spline, Lottie everywhere, particles / tsParticles, Three.js as wallpaper, Theatre, Barba, Swiper |
| Pitch / sales / board PPTX | No extra npm. Poster type, real photos, screenshot chrome, native charts, light/dark rhythm. `presentation-designer` + `pptx-generator`. | Lenis, Three.js, Motion, GSAP, Lottie, canvas FX on a slide. They flatten to an image. |
| Packshots / 360 | `product-visuals` (image / video). Orbit page = Three.js companion, not a raster slide. | Fake 3D inside PowerPoint |
| Studio HTML report (UI) | Vite + official DS + `framer-motion` + `three` + R3F + drei. Designed spatial scene, content overlay. `open_preview`. Not PDF. | File Preview PDF-first. Three.js wallpaper. WebGL on a PPT/Word page. |

Those banned libraries bloat the page, copy Open Design, and do not make the PPTX better. BLOCK the gate if a deck is a WebGL screenshot, or a landing is particles + GSAP instead of the Motion / Lenis stack.

Every substantial run ends with:

1. Paths to files created
2. Top actions for the user this week
3. Closing gate (below)

## Closing gate (mandatory)

Score the deliverable before finishing:

| Dimension | PASS | WARN | BLOCK |
|-----------|------|------|-------|
| Grounding | Claims backed by fetch/search or marked estimate | Some soft claims | Invented metrics/contacts |
| Completeness | Brief objectives met | Minor gaps noted | Core ask unanswered |
| Structure | Files in expected paths with usable format | Messy but usable | Chat-only, no files |
| Actionability | Prioritized next steps with owners/effort | Vague next steps | No clear action |
| Compliance | Public sources, no fake "verified" data | Borderline fields flagged | Fabricated PII presented as fact |

**Verdict rules**

- Any BLOCK → fix before closing; do not claim success
- Only WARNs → deliver with an explicit "Reservations" section
- All PASS → short success close + file paths

```markdown
## Expert gate
Verdict: PASS | WARN | BLOCK
- Grounding: ...
- Completeness: ...
- Structure: ...
- Actionability: ...
- Compliance: ...
Reservations / fixes: ...
```

## Anti-patterns

- Dumping generic advice without fetching the user's URL or ICP
- Padding lists with invented companies or contacts
- Presenting qualitative difficulty as a numeric KD from Ahrefs
- Shipping copy/creatives with no persona, offer, or CTA
- A marketing deck made of WebGL / Lenis / Three.js screenshots
- A landing that loads GSAP, Locomotive, Spline, particles, Theatre or Barba for "wow"
- Ending with "I can also..." instead of saved files + gate
- Skipping the critic pass on multi-file deliverables

## Connectors (when available)

If env/MCP connectors exist (`seo-data-provider`, `lead-enrichment`, HubSpot MCP,
`HUBSPOT_ACCESS_TOKEN`), prefer them for volumes, enrichment, and CRM sync.
If absent, stay on qualitative/public methods and state the data gap clearly.
