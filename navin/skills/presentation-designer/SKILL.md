---
name: presentation-designer
description: Design the story, structure, and slide-by-slide plan of presentations - pitch decks, client presentations, reports. Use before generating any deck.
metadata: {"navin":{"emoji":"🎬","category":"documents"}}
---

# Presentation Designer

## Overview

Design the argument before the slides. Output: a validated slide plan that `pptx-generator` turns into a file. Architecture, sequence, workflow and data-flow slides are planned for `archify` (HTML + export), not hand-drawn Mermaid.

**Chosen template + today's bar (both, always).** The PPT theme the user picked in Templates de documents is the look. Fill that folder: replace text, keep CSS/fonts/colors, add photos when you can, keep theme motion. Today's rules are how you fill it: Hook → Context → Core → Shift → Takeaway, tones, stagecraft, poster type, notes, `critique.json` ≥ 85, QA, presenter S/O. Never drop the attached theme. Never invent Inter / purple glow / another kit. If the user wants Three.js, ship a companion page. Never bake WebGL into a slide.

## Clarify before any plan (mandatory)

Do not invent a deck from a fuzzy brief.

1. **Theme (already chosen wins):**
   - A **Document Template Attachment** in the runtime context is the design. Lock that folder's `theme` / `design-system.json`. Do not call `ask_user` for magazine / swiss / corporate. Do not remap Aurora Glass, Architect, Startup, … to `editorial_luxe`.
   - Else if the user already named a PPT theme, lock that name.
   - Else call `ask_user` for style only:
     - `a` Magazine (recommended): `editorial_luxe`
     - `b` Swiss: `black_and_white_clean`
     - `c` Corporate: `textbook`
     Skip takes magazine.

   Lock one theme. Do not invent hex values.

2. **Duration** if unknown: `a` 10 min / 8-10 slides (recommended), `b` 20 min / 12-16, `c` 30 min / 18-22.

3. Photos: user files win. If none, say you will pull Unsplash scenes or skip the gallery.

Language is still a hard gate: if the presentation language is missing, ask and stop.

MUI / Fluent / Carbon are **Code** (web apps). They are not PPT themes. A deck uses `templates/ppt/<theme>`, never `@mui/material`.

## Narrative arc (mandatory)

Map every deck onto this spine, then onto a named structure below:

| Beat | Job | Typical layouts |
|------|-----|-----------------|
| Hook | Stop the room | cover, statement, big-numbers |
| Context | Why this, why now | agenda, text-image, kpi |
| Core | Proof | process, chart, gallery, comparison |
| Shift | What changes | section-break, statement |
| Takeaway | Ask / next step | closing, quote |

A 10 minute deck is Hook (1) + Context (2) + Core (4-5) + Shift (1) + Takeaway (1).
Do not ship a list of topics. Read only the message column: if it does not convince, rewrite.

## Theme rhythm (mandatory)

Every slide has a `tone`: `light`, `dark`, `hero-light`, or `hero-dark`.
Cover and curtains are hero. Content flips family every two slides.
Never three consecutive slides in the same light or dark family.
From 8 content slides up: at least one `hero-dark` and one `hero-light`.
The engine fills missing tones. Do not leave a deck all-light.

## Stagecraft (mandatory, 8+ content slides)

The engine inserts what you forget, but write them in the plan:

1. One `section-break` curtain between chapters.
2. One data hero (`big-numbers` or `kpi`) with a unit and a frame.
3. One `gallery` when there are two or more real photos.

Cover, statement and section-break are posters: title 8-12 words, huge, almost no body.

## Motion and 3D (the right stack)

Pick the framework from the livrable, not from a demo you liked.

| Livrable | Stack | Forbidden |
|----------|-------|-----------|
| Editable PPTX | Theme `motion` tokens only (`--nv-duration`, `--nv-ease`, `.nv-anim-fade` / `rise` / `stagger` / `scale`). Cover and curtains rise. Cards stagger. | Three.js, framer-motion, GSAP, Lottie, canvas FX, WebGL. They flatten to a screenshot. That file is rejected. |
| `slides/index.html` preview | CSS transitions already in the presenter (S / O). No CDN. | A second animation runtime. |
| Marketing site / landing / product page | Super render stack in `ui-ux-pro-max`: `framer-motion` + `lenis` + `embla-carousel-react` + `lucide-react`. | CSS-only heroes. GSAP / particles / Swiper. |
| Real 3D (product turntable, spatial hero that IS the product) | `three` + `@react-three/fiber` + `@react-three/drei`. `@react-three/postprocessing` only on that hero. Still fallback. | Three.js on a slide. WebGL wallpaper. |

If the user wants the 3D wow AND a deck: ship both. Companion HTML/page with Three.js + the editable PPTX. Never bake the WebGL into a slide.

Open Design wins the browser demo because they screenshot WebGL. We beat them on the file the room edits. Do not copy their stack onto slides.

## Theme photos (already on disk)

Each PPT theme ships three stills in `photos/`: `background.jpg`, `left.jpg`, `right.jpg`. The engine places them. Do not leave a cover or a 50/50 slide empty.

| Slot | Layouts |
|------|---------|
| background | cover, statement, section-break, closing, full-bleed-hero |
| left | image-text |
| right | text-image, case-study, quote, team |

A file the user joined (logo, icon, screenshot, product UI) always wins. To change a still: set `"image": "their-file.png"` or `"visual": { "slot": "left" }`. Do not download a fourth stock photo unless the user asked to replace one.

Charts, process, KPI and `product-hero` stay native. Do not drop a lifestyle photo into a browser chrome.

## Product shots

A UI capture is not a lifestyle photo. Set `"visual": { "kind": "screenshot", "fit": "contain" }` or use `layout: product-hero`. The engine frames it in a browser chrome. Photos stay full-bleed. Phone UI uses `"kind": "phone"`. Swiss data decks may set `"visual": { "variant": "tower" }` on `kpi`.

## Five-dimension critique (mandatory)

`ppt_design deck` writes `slides/critique.json`: story, type, rhythm, evidence, stage. Each is 0-20. Do not deliver under 85. Topic-label titles ("Overview", "Solution") fail story. Fix the JSON and render again.

## Speaker notes (mandatory for a talk)

Each slide JSON carries `notes`: purpose of the page, what to say that is not on the slide, why the next slide follows. 3-5 spoken lines, not a script, unless the user asked for a script. Facts without a source stay out.

## Typography & icon rules (mandatory)

- Never use em or en dashes (U+2014 / U+2013) in slide copy. Use commas, colons, periods or parentheses instead.
- Never plan emoji or generic AI-style icons (📊 ✅ 💡 🚀 …) as visual elements. Prefer numbers (01, 02), plain monochrome glyphs (✓ ✗ + −) or simple geometric shapes consistent with the deck design.

## Story structures

| Deck type | Structure |
|-----------|-----------|
| Project pitch | couverture → sommaire → vision → problème chiffré → solution → approche → feuille de route → équipe → risques → prochaines étapes |
| Investor pitch | couverture → sommaire → problème → solution → produit → marché (TAM/SAM/SOM) → modèle → traction → concurrence → équipe → projections → ask |
| Sales | couverture → sommaire → leur situation → problème chiffré → vision du résultat → notre approche → preuve (cas) → offre → next step |
| Executive report | couverture → sommaire → conclusion d'abord → 3 messages clés → données à l'appui → décisions demandées |
| Project update | couverture → sommaire → statut en 1 slide → réalisations → risques → décisions nécessaires → plan |
| Training | couverture → sommaire → pourquoi ça compte → concept → démo/exemple → pratique → récap |

## Slide plan format (the deliverable)

```markdown
## Deck: <titre> - <audience>, <durée>, <objectif>
| # | Message du slide (phrase complète) | Contenu (texte exact prévu) | Layout template | Visuel |
|---|------------------------------------|-----------------------------|-----------------|--------|
| 1 | "Chaque équipe perd 6 h/semaine dans des outils disparates" | titre + 1 chiffre géant + 1 phrase | slide_03 (problem) | big number |
| 2 | ... | 3 bullets max, ≤12 mots chacun | slide_05 (solution) | photo/chart |
```

The message column is the test: each is a full assertion (not a topic). Read only that column top to bottom - if the story convinces, the deck will.

When a Document Template Attachment is present, the Layout column must name
either a real `slide_XX.html` from that template or a master id from
`templates/ppt/_engine/catalog.json` (cover, kpi, process, timeline, …).
Do not invent layouts outside the template or the engine.

## Visual Director (mandatory)

The plan describes **what** to tell. It does not place boxes.

For each slide pick one content kind, then one layout:

| Kind | Layouts |
|------|---------|
| Numbers | kpi, chart, table, dashboard, data-story |
| Chronology | timeline, roadmap |
| Steps | process |
| Loop | cycle |
| Organization | hierarchy |
| Comparison | comparison, matrix |
| Architecture | architecture |
| Geography | map |
| Product | product-hero, full-image |
| Testimonial | quote |
| People | team |

Never emit `{ "x", "y", "width" }`. Emit a semantic slide:

```json
{
  "layout": "text-image",
  "title": "Enterprise AI is accelerating",
  "visual": { "type": "image", "position": "right", "importance": "primary" }
}
```

Variation: never three consecutive slides with the same layout. Alternate
text / visual / data. Use section-break between chapters.

A chart slide always carries an insight line (`+43% YoY` or a full sentence).
No chart without a reason.

## Visual assets (mandatory)

Type first. Then a trusted source. Never Google Images, never a Maps
screenshot, never a watermarked stock preview.

| Meaning | Visual |
|---------|--------|
| Numbers | Native chart / KPI. No photo. |
| Steps, loop, architecture, org | Native diagram + Lucide SVG icons. |
| Geography | Native map. Not a Google Maps capture. |
| Real-world scene | Unsplash, then Pexels, then Pixabay. |
| Product UI | User screenshot, `contain`. |
| Brand / tech mark | Official kit or Simple Icons. |
| Abstract concept | Internal illustration, then unDraw, then generate. |

Search the *scene*, not the slide title. "AI Transformation Strategy" becomes
`modern enterprise team digital technology`, not the title itself.

```bash
<navin-python> -m navin.documents.ppt_assets intent --title "AI Transformation Strategy" --theme startup
<navin-python> -m navin.documents.ppt_assets photo --query "modern enterprise team digital technology" --theme startup -o slides/hero.jpg
<navin-python> -m navin.documents.ppt_assets icon --name building-2 --color "#0066FF" -o slides/icon.svg
```

User-provided files win. Do not add a photo just to fill a hole. Alternate
photo / diagram / chart / type across the deck. Photography uses `cover`.
Screenshots and logos use `contain`. Never stretch.

## Copy quality bar (mandatory)

Weak decks fail because of vague titles and padded bullets. Enforce this before handing off to `pptx-generator`:

1. **Title = assertion.** Reject topic titles ("Vision", "Problème", "Notre solution", "Roadmap"). Prefer "Les équipes perdent 6 h/semaine" or "Navin livre un agent opérationnel en 14 jours".
2. **One idea per slide.** If you need two ideas, split into two slides or cut one.
3. **A content slide is a composition, not a title.** Cover, statement, section-break and closing may hold one sentence. Every other slide must carry at least three real items (cards, steps, KPIs) or a real photo beside the copy. Title + kicker + one line is a rejected slide: it looks clean and empty.
4. **Short copy.** Title ≤12 words. Body: ≤6 bullets, ≤12 words each. Prefer 3 sharp lines over 8 soft ones.
5. **Concrete over abstract.** Prefer named actors, numbers, timelines, deliverables. Ban filler: "innovant", "solution complète", "synergies", "écosystème", "meilleure expérience", "au cœur de", "permet de facilement".
6. **Numbers earn their place.** Every figure needs a unit and a frame (per week, YoY, of budget). Invented metrics must stay plausible and internally consistent across the deck.
7. **Locale.** Match the selected language's typography, date/number formats, and register (vous/tu, ton board vs ton opérationnel). French: thin spaces before `;:!?` when the template allows plain text; no anglicismes inutiles when a clear French term exists.
8. **Invented details.** When the user asks to invent facts, invent a coherent product story (name, ICP, pain, offer, proof, ask) and keep names/numbers stable on every slide. Do not paste generic startup clichés.

## Design rules

- 1 idée/slide; le titre EST le message ("CA +18%" pas "Résultats financiers")
- 10/20/30 discipline for pitches: ~10 slides, 20 min, ≥30pt fonts in the template's hierarchy
- Data → chart with the takeaway in the title; details → annexe slides
- Assertion-evidence beats bullet lists
- Never plan a freestyle Inter / purple-gradient / glow deck. Visual system comes from the selected HTML template or a named theme in `document-templates`.

## Recommended template mapping (when none attached yet)

| Deck | Prefer |
|------|--------|
| Magazine / story / brand | `editorial_luxe` |
| Swiss / data / engineering | `black_and_white_clean`, `numbers_clean` |
| Corporate / investor / board | `textbook`, `startup`, `premium_black` |
| Product launch | `aurora_glass`, `neon_impact`, `startup` |
| Training / report | `textbook`, `numbers_clean` |
| Tender / AO | `reponse_appel_offre` |
| ESG / nature | `premium_green` |
| Creative / agency | `portfolio` |

If no Document Template Attachment is in the runtime context, stop and ask the user to pick one (or pick the first recommended that exists in the workspace template library) before writing HTML.

## Workflow

1. A deck is a simple job: start the pipeline in this turn, do not wait for Build. Lock the attached PPT template if present. Only call `ask_user` for style (and duration if unknown) when nothing is attached. Then apply today's bar on that theme: Hook → Takeaway, tones, stagecraft, notes, critique ≥ 85.
2. Confirm audience, decision sought, projected vs emailed. Emailed decks may carry more text.
3. Draft the message column on the Hook → Context → Core → Shift → Takeaway spine. Validate the story when the stakes are high. For Studio one-shot cards, self-validate against the copy quality bar, then proceed.
4. Run the Visual Director: map each message to a layout id from `_engine/catalog.json`.
   Image left = `image-text`. Image right = `text-image`. Steps = `process`.
   Numbers = `kpi` or `big-numbers`. Emit one semantic JSON object per slide
   (`layout`, `title`, `items`, `visual`, `image`, `tone`, `notes`). Hand that JSON to
   `ppt_design deck` or `materialize --slide`. Never copy a lookbook
   `slide_XX.html`. Never write HTML by hand. That is how titles double
   and pictures glue themselves on top of the text.
5. Complete exact content + layout + visual specs per slide.
6. Hand to `pptx-generator`; review the rendered deck against the plan. Any slide that overflows, repeats the previous composition, or scores under 85 is redone. That score is not a judgement call: `ppt_qa.py slides/` computes it against `_engine/quality.json` and names what to change. Also read `slides/critique.json`. Open `slides/index.html`: S is presenter (current, next, notes, timer), O is overview.

## Rules

- Every deck of 3+ content slides starts with cover then sommaire (`layout: agenda`). The engine inserts the slide from the following titles if you forget. Do not ship a deck without it.
- No deck generation before the plan exists (even a short internal plan).
- Annexes catch everything cut from the main flow.
- If content is too thin for a template slide's blocks, drop the slide or switch to a denser layout from the same template. Never pad with lorem or empty cards.
- Never plan a content slide as title + one sentence. The engine will try to split the body or paint a side panel, but that is a fallback, not a design. Write three claims, a process, or attach a real photo.
