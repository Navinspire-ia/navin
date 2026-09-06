---
name: ui-ux-pro-max
description: Default web UI/UX design intelligence for Navin. Design-system generator (84 styles, 192 palettes, typography, landing patterns, UX rules) plus mandatory framer-motion for all web sites. Use when building, designing, scaffolding, or reviewing any website, landing page, dashboard, or frontend UI.
metadata: {"navin":{"emoji":"🎨","category":"development","default_for":"web"}}
---

# UI/UX Pro Max (Navin default for web)

Bundled design intelligence from [ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) (MIT). This is the **default** skill for every website / frontend UI task in Navin.

## Hard defaults (non-negotiable for web)

0. **Lock one official design system (Navin Code, non-negotiable):** Google Material (`@mui/material` + `@emotion/react` + `@emotion/styled`), Microsoft Fluent (`@fluentui/react`), or IBM Carbon (`@carbon/react` + `@carbon/styles`). If the user did not choose, call `ask_user` (`a` Google recommended, `b` Microsoft, `c` IBM). Skip takes Google. Never default to Tailwind, shadcn, Chakra, Ant, or a homemade kit. Map MASTER.md tokens onto that vendor `ThemeProvider`. Scaffold with Vite (or Next if named), never `create-react-app`.
1. **Install and use `framer-motion` on every web project** (React / Next / Vite / Astro-with-React, etc.):
   ```bash
   npm install framer-motion
   # or: pnpm add framer-motion / yarn add framer-motion / bun add framer-motion
   ```
   - If `package.json` has no `framer-motion` (and no `motion` package), install it **before** writing UI animation code.
   - Prefer `framer-motion` (`motion` / `AnimatePresence`) for enter/exit, stagger, page transitions, and micro-interactions - not ad-hoc CSS-only for hero/section motion.
   - Always respect `prefers-reduced-motion` (disable or simplify motion when set).
   - Spring defaults: `transition: { type: "spring", duration: 0.3, bounce: 0 }` unless the design system says otherwise.
2. **Install and use Three.js on every Dev web UI, every Marketing / Montage page, every 3D request, and every studio HTML report UI:**
   ```bash
   npm install three @react-three/fiber @react-three/drei
   ```
   Run `python3 "$SEARCH" "<theme>" --stack threejs` before the scene. Use drei helpers. The scene must look designed (PBR, lights, shadows, framed camera), never wallpaper. Still fallback when `prefers-reduced-motion`. Never put Three.js on a PPT or Word page.
3. **Generate a design system first** for new pages/sites (Step 2 below) before inventing colors/fonts.
4. **Icons**: vendor set of the locked DS (`@mui/icons-material`, Fluent icons, `@carbon/icons-react`). Lucide / Heroicons SVG only as a fallback. Never emoji as icons.
5. **No em dashes / en dashes in ANY UI copy** - characters U+2014 and U+2013 are forbidden. Use a plain hyphen `-` or rephrase. This is enforced by `verify` / lint (`no-em-dash`).
6. **No cardboard apps** - every visible control must work or be removed. No "Coming soon", empty `onClick`, `alert()` stubs, or lorem. Dashboards must load real data or a real empty state.
7. **Functional Preview gate** - after `open_preview`, click the main nav and the primary CTA. If the dashboard is blank or errors, you are not done.

Also load `make-interfaces-feel-better` for polish details (radius, shadows, stagger).

## Super render stack (marketing, launch, portfolio)

A pretty page is not 12 runtimes. Install what the surface needs. Stop there.

**Every marketing / launch / editorial site** (on top of `framer-motion`):

```bash
npm install framer-motion lenis embla-carousel-react lucide-react three @react-three/fiber @react-three/drei
```

| Package | Job |
|---------|-----|
| `framer-motion` | Hero presence, section reveal, stagger, page transition, press. Already mandatory. |
| `lenis` | Smooth scroll. Wire it once at the root. Disable when `prefers-reduced-motion`. |
| `embla-carousel-react` | Lookbook, product shots, proof strip. Not Swiper. |
| `lucide-react` | Icons. SVG only. Never emoji. |
| `three` + `@react-three/fiber` + `@react-three/drei` | Designed 3D layer on every Marketing / Montage page. Same quality bar as Dev. |

**Dev, Marketing, Montage, any 3D page, and studio HTML report UIs (mandatory):**

```bash
npm install three @react-three/fiber @react-three/drei
```

Before writing the scene, run the Three.js stack search and follow every hit:

```bash
python3 "$SEARCH" "<product or report theme> spatial hero" --stack threejs
```

| Package | Job |
|---------|-----|
| `three` + `@react-three/fiber` + `@react-three/drei` | Designed 3D layer on every Dev, Marketing, and Montage web surface, every 3D request, and every studio HTML report UI. Hero, product, or spatial chrome. Still fallback required. |
| `@react-three/postprocessing` | That 3D hero needs bloom / grain. Never as the only "design". |
| `@number-flow/react` | A giant KPI that ticks. One per viewport max. |
| `recharts` | A dashboard or a real data section. Not a landing decoration. |

**Scene quality (non-negotiable):** one Canvas / one renderer; `pixelRatio` capped at 2; `antialias` at construction; PBR (`meshStandardMaterial`) plus Ambient + Directional lights (objects must not render black); `shadowMap` enabled before cast/receive; FOV 45-75; explicit camera position + lookAt; drei `OrbitControls` (damping) or a constrained camera; `Environment` + `ContactShadows` on the hero; `useFrame` / `Clock.getDelta()` once per frame; pause the loop when the tab is hidden; dispose geometries/materials/textures on teardown; canvas `role="img"` + `aria-label`; `prefers-reduced-motion` shows the still. Production = npm + Vite, not a floating CDN `latest`.

The 3D is a designed object or environment the user can read. Never Three.js as wallpaper, never a particle field as the page, never a blank `<Canvas />`.

**Do not install by default:** GSAP, ScrollTrigger, Locomotive, Spline runtime, Rive, Lottie, tsParticles / particles.js, Three.js as wallpaper, Swiper, Barba, Theatre.js. GSAP only if the user names it. One Rive mark is allowed when the brand already has a `.riv` file. Never put Three.js on a PPT or Word page (it becomes a screenshot).

Reduced motion: Lenis off, Motion snaps, 3D shows the still, Number Flow shows the final figure.

## When to use

Any UI that **looks, feels, moves, or is interacted with**: landings, marketing sites, SaaS app shells, dashboards, CRMs, portfolios, e-commerce, forms, component libraries.

Skip for pure backend/API/DB/infra with no UI.

## Search tool path

The bundled script is `<skill folder>/scripts/search.py` (no network, stdlib only). The skill folder's absolute path is printed when this skill is loaded - the `[Skill folder: ...]` header above, or the `(Skill folder: ...)` line under the skill title. Use that path as `SEARCH` below; never hunt for it with imports or a filesystem-wide find. Use `python3` on Linux/macOS and `python` (or `py -3`) on Windows.

## Workflow

### 1. Analyze

Extract: product type, industry, audience, tone, stack (from `package.json` / framework files - never assume). Default stack for greenfield web: React + Vite (or Next if the user named it) **plus one official DS**: MUI, Fluent, or Carbon. Never Tailwind as the default system.

### 2. Design system (required for new pages/projects)

```bash
python3 "$SEARCH" "<product> <industry> <keywords>" --design-system -p "Project Name"
```

Persist into the **user** workspace:

```bash
python3 "$SEARCH" "<query>" --design-system --persist -p "Project Name" --output-dir "<project-root>"
```

Creates `design-system/<slug>/MASTER.md` (+ optional `--page "dashboard"` overrides). If MASTER already exists, read it first; only regenerate with `--force` when the user wants a reset.

### 3. Optional dials

`--variance` / `--motion` / `--density` (1-10) on the same `--design-system` command.

For web sites, prefer `--motion` in the 5-8 range and implement those motions with **framer-motion** (not GSAP unless the user asks).

### 4. Domain / stack deep-dives

```bash
python3 "$SEARCH" "<keyword>" --domain style|color|typography|landing|ux|chart|icons|react|gsap|...
python3 "$SEARCH" "<keyword>" --stack react|nextjs|vue|html-tailwind|shadcn|...
```

### 5. Implement

- Apply MASTER.md tokens through the locked vendor theme (MUI / Fluent / Carbon), not as a parallel homemade CSS kit.
- Install `framer-motion` if missing.
- On Dev / Marketing / Montage / 3D / studio report UIs: install `three` + `@react-three/fiber` + `@react-three/drei` if missing, run `--stack threejs`, then implement the scene with drei helpers (OrbitControls, Environment, ContactShadows, PresentationControls as needed).
- Ship 2-3 intentional motions (hero presence, section reveal / stagger, CTA hover/press) - not noise.
- Follow Navin frontend rules when they apply: one composition in the first viewport, brand-first, expressive fonts (not Inter/Roboto/Arial defaults), atmospheric backgrounds, full-bleed heroes on landings, no card clutter in heroes, avoid purple-on-white / cream+terracotta / broadsheet clichés unless the design system explicitly requires them.

### 6. Pre-delivery checklist

- [ ] Official DS locked: `@mui/material` or `@fluentui/react` or `@carbon/react` (ask_user if none)
- [ ] `framer-motion` in package.json and used for primary animations
- [ ] Dev / Marketing / Montage / 3D / studio report UI: `three` + `@react-three/fiber` + `@react-three/drei` installed and used; `--stack threejs` was run
- [ ] 3D scene meets the quality bar (lights, shadows, camera, one renderer, reduced-motion still)
- [ ] Marketing/launch pages also have `lenis`, `embla-carousel-react`, `lucide-react` when those surfaces exist
- [ ] No GSAP / particles / Three-as-wallpaper unless the user asked
- [ ] No emoji icons (SVG only)
- [ ] `cursor-pointer` on clickable elements
- [ ] Hover/focus transitions 150-300ms
- [ ] Text contrast ≥ 4.5:1 (light mode)
- [ ] Visible keyboard focus
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive: 375 / 768 / 1024 / 1440
- [ ] Design system MASTER.md present for new sites
- [ ] No em-dash / en-dash characters in UI strings (`verify` must be clean)
- [ ] Every primary button/nav item routes or mutates for real (no stubs)
- [ ] Dashboard / home view loads without blank screen (data or empty state)
- [ ] Preview happy path clicked by you before "done"

## If search returns 0 results

Retry with broader keywords once; then fall back to the priority table in `references/quick-reference.md` and say the fallback is not a DB match. Never invent fake search hits.

## References (read on demand)

- `references/quick-reference.md` - full UX guideline index
- `references/pro-rules.md` - app/native checklist extras
- Upstream: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
