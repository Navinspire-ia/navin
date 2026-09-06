---
name: pptx-generator
description: Create PowerPoint (.pptx) decks - layouts, text, tables, charts, images, and brand templates - using python-pptx. Use whenever the deliverable is a presentation file.
metadata: {"navin":{"emoji":"📙","category":"documents"}}
---

# PowerPoint Generator

## Overview

Build real .pptx files with `exec` + Python `python-pptx`, which Navin ships. Do not run import checks or install anything up front: write the script, and only if it actually fails on a missing dependency, report it and ask before touching the environment. Story and slide plan come from `presentation-designer`; this skill executes.

**The deck must always be editable.** Every title, bullet, figure and label is a real text box, every picture a real picture. A slide pasted as a full-bleed screenshot is a rejected deliverable: the user cannot fix a typo, translate a label or reuse a chart.

## Motion and 3D (mandatory)

Slides are not a web runtime. Motion on a slide is the theme tokens in `design-system.json` (`motion.duration`, `motion.easing`) and the engine classes `.nv-anim-fade`, `.nv-anim-rise`, `.nv-anim-stagger`, `.nv-anim-scale`. html2pptx freezes a frame. Three.js, framer-motion, GSAP, Lottie or a canvas FX on a slide become a screenshot. Reject that.

- Theme pack: `photos/background.jpg`, `photos/left.jpg`, `photos/right.jpg` are already in the selected theme. `ppt_design deck` places them. User attachments (logo, icons, screenshots) replace that slot. Do not ignore the pack and ship empty covers.
- Product UI: `visual.kind: screenshot` or `layout: product-hero` (browser chrome). Not a 3D canvas.
- A 3D product the user asked to spin: companion web page (`three` / R3F) or `product-visuals` video. Keep the PPTX as photos + editable type.
- Preview motion lives in `slides/index.html` (CSS only). Do not add a JS 3D engine there.

## Language and locale gate (mandatory)

- Before creating or exporting a PPTX, require the user to explicitly select the presentation language. Never infer it from the prompt, UI language, template, names, or location. If absent, ask and stop generation.
- A deck is a simple deliverable. Do not file a board plan and wait for Build. Produce the PPTX in this turn. If a Document Template Attachment is present, that theme is locked. Materialize with its name. Do not ask magazine / swiss / corporate and do not change theme.
- If nothing is attached and `presentation-designer` has not locked a style (magazine / swiss / corporate), call `ask_user` and stop. Do not pick a freestyle palette.
- Preserve that language in every title, label, chart, legend, note and file metadata. Translate all source-template text, including small labels and example data.
- Apply locale-appropriate dates, decimal/grouping separators and currencies consistently, including native chart data labels.
- For Arabic and other RTL languages, set RTL/bidi paragraphs and right alignment, mirror layouts where appropriate while retaining logical reading order, and use fonts with complete glyph coverage (for example Noto Naskh Arabic, Noto Sans Arabic or Amiri). Verify shaping in the exported preview and avoid silent font fallback.

## Typography & icon rules (mandatory)

- Never use em or en dashes (U+2014 / U+2013) in slide text. Use commas, colons, periods or parentheses instead.
- Never use emoji or generic AI-style icons on slides. Use numbers (01, 02), plain monochrome glyphs (✓ ✗ + −) or simple shapes consistent with the deck design.

## Visual fidelity (mandatory)

- A Document Template Attachment is the visual source of truth. Keep its CSS, fonts, spacing, colors and slide layouts. Replace text, data and images only. Add photos when you have them. Theme motion tokens are the only animation on the PPTX. If the user asked for Three.js, ship a companion HTML page. Do not restyle the deck. Still run today's pipeline on that theme: `ppt_design deck` (outline, stagecraft, rhythm, preview, notes, critique), then `ppt_qa.py`, then `html2pptx`, then stamp notes. Do not skip QA because a pretty template is attached. The engine accepts `features`, `columns`, `steps`, `phases`, `description`. Prefer `items` + `body`.
- Tokens in `design-system.json` / `theme.css` are the only legal colors. Components in `_engine/navin-ppt.css` inherit them.
- Do not invent a freestyle deck (Inter + blue/purple gradients, glassmorphism, glow, generic startup dark mode). That look is a rejected deliverable.
- If no template is attached: pick a recommended pitch template from the workspace library (`textbook`, `black_and_white_clean`, `startup`, `premium_black`) via `document-templates`, materialize it, then fill it. Do not hand-author a new design system.
- Never restyle the template to "make it nicer". Adapt content length to the existing blocks; if a block does not fit, choose another slide layout from the same template or delete the block and rebalance.

## Copy quality before conversion (mandatory)

- Titles are assertions, not labels ("Les équipes perdent 6 h/semaine", not "Problème").
- ≤6 bullets per slide, ≤12 words each. Cut filler and repeated claims.
- Every invented number stays consistent across slides and has a unit/timeframe.
- Scan for leftover sample copy, source-language strings, empty cards, and template captions that are instructions to the agent.

## Core recipes

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE

prs = Presentation("brand-template.pptx")   # inherit masters; or Presentation() for blank (13.33x7.5")

# Title + content slide via layout placeholders
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = "Titre du slide"
body = slide.placeholders[1].text_frame
body.text = "Point 1"
p = body.add_paragraph(); p.text = "Sous-point"; p.level = 1

# Free textbox
tb = slide.shapes.add_textbox(Inches(0.5), Inches(1.5), Inches(9), Inches(1))
run = tb.text_frame.paragraphs[0].add_run(); run.text = "42%"
run.font.size = Pt(54); run.font.bold = True; run.font.color.rgb = RGBColor(0x03,0x69,0xFF)

# Image, table, native chart
slide.shapes.add_picture("chart.png", Inches(1), Inches(2), width=Inches(6))
shape = slide.shapes.add_table(3, 4, Inches(0.5), Inches(2), Inches(9), Inches(2))
data = CategoryChartData(); data.categories = ["T1","T2"]; data.add_series("CA", (120, 180))
slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(8), Inches(4.5), data)

prs.save("deck.pptx")
```

Inspect a template's layouts first: loop `prs.slide_layouts` and print placeholder names/indexes.

## HTML slide template library

Navin materializes the selected editable HTML deck inside the active workspace
(one folder per design: `slide_XX.html` samples at 1920x1080 +
`metadata.json` + `design-system.json` + `theme.css` + `image.png` preview).
The shared engine lives in `templates/ppt/_engine`.

When the runtime context contains a "Document Template Attachment":

1. `ls` the template folder; read `metadata.json` and `design-system.json`.
2. Write a semantic deck JSON. Then let the engine render every slide.
   The `slide_XX.html` files in the theme folder are the lookbook (picker
   preview). Do not copy them. Do not hand-write HTML. Do not paste
   `{ "x", "y", "width" }`. Hand-edited HTML is how text doubles and
   images paint over the copy.

   Image on the left: `"layout": "image-text"`. Image on the right:
   `"text-image"`. Full bleed: `"full-bleed-hero"`. Process, KPI,
   timeline, matrix: same ids.

   ```json
   {
     "theme": "startup",
     "language": "fr",
     "slides": [
       {
         "layout": "cover",
         "kicker": "Pitch",
         "title": "Les equipes perdent 6 h par semaine",
         "body": "Un agent operationnel en 14 jours.",
         "tone": "hero-dark",
         "notes": "Open on the wasted hours. Do not read the title."
       },
       {
         "layout": "text-image",
         "title": "Le produit tient la promesse",
         "body": "Un ecran, un resultat mesure.",
         "items": [
           { "label": "14 jours", "detail": "Premier agent en production." },
           { "label": "1 equipe", "detail": "Pas un projet IT de 9 mois." }
         ],
         "visual": { "type": "image", "kind": "screenshot", "position": "right", "fit": "contain" },
         "image": "product.png"
       }
     ]
   }
   ```

   ```bash
   <navin-python> -m navin.documents.ppt_design deck --slides deck.json -o slides/
   ```

   One slide only:

   ```bash
   <navin-python> -m navin.documents.ppt_design materialize --theme <name> --slide slide.json -o slides/slide_03.html
   ```

   The renderer builds the body from scratch. Sample phrases and
   `image.png` never survive. If there is no real image, a theme
   panel fills that column: no empty frame, no leftover placeholder.
   A content slide that is only a title and one sentence is a defect.
   Emit at least three `items`, a process, a KPI row, or a real photo.
   From three content slides up, the engine inserts an `agenda` sommaire
   after the cover, built from the real titles. Do not skip it. Set
   `language` on the deck JSON so the kicker reads Sommaire or Agenda.
   From eight content slides up it also inserts a `section-break` curtain
   and promotes a data hero if you forgot. A gallery is added when two or
   more real photos exist. Tones (light / dark / hero) are filled so the
   deck is not all-paper. `deck` also writes `index.html` (S = presenter:
   current, next, notes, timer; O = overview), `notes.json` and
   `critique.json`. Do not deliver when critique is under 85. `html2pptx.py`
   copies the speaker notes stamped on each slide into the PPTX; the
   separate `ppt_design notes --dir slides/ --pptx deck.pptx` command only
   serves a deck converted some other way. Layout names are tolerant
   (`bullets`, `title`, `thanks`, `kpis`... map to catalog ids; `ppt_design
   layouts` lists them) but write the real ids.

   The PPTX stays editable. The HTML preview is extra, never a screenshot
   substitute for the file.
   Colors come from `--nv-*` tokens. Never invent a new hex.

   Visuals follow `ppt_assets` (see `presentation-designer`). Never
   download from Google Images. Numbers become native charts. Simple
   process graphics may stay native SVG. Architecture, sequence,
   workflow and data-flow diagrams use `archify` (export PNG/SVG onto
   the slide). Photos come from Unsplash / Pexels
   / Pixabay only, cached with author metadata. Icons come from Lucide
   (then Tabler, Heroicons, Simple Icons) as SVG tinted with the theme
   accent. User files win. `cover` for photos, `contain` for screenshots
   and logos. Never stretch.
3. Pick `data-variant` from `_engine/catalog.json` (process, cycle, timeline,
   funnel, matrix, kpi, chart, table, …). Step counts stay between the
   catalog min and max (process: 2 to 10).
4. Convert with the bundled converter:

   ```bash
   <navin-python> .navin/resources/tools/html2pptx.py slides/ -o deck.pptx
   ```

   Chromium measures the rendered page, then every text becomes a text box,
   every solid block a shape, every local image a picture, and only the
   remaining decor (gradients, SVG, shadows) is flattened behind them. Roughly
   one second per slide. Useful flags: `--keep-fonts` to keep the CSS font
   names instead of their Office equivalents, `--chromium PATH` when no
   browser is found, `--keep-workdir DIR` to inspect the intermediate files.
5. Never restyle the template; adapt content length to the existing blocks.
   Keep the template fonts and palette even if you dislike them.
6. QA the slides before converting (see below), fix what it names, run it
   again. Then read what the converter prints: it reports the defects a reader
   notices first, and each one is a fix to make in the HTML before delivering:
   copy running past the 1920x1080 frame (shorten it), text that does not
   separate from the block behind it (darken the text or lighten the block),
   the same picture reused across slides (find distinct visuals or drop them).
7. After conversion, run `doc_check.py deck.pptx`, then `preview_document.py
   deck.pptx previews/` and read the PNGs. Reject the deck if titles are
   topic-labels, bullets are padded, or slides look like a different brand
   than the template.

Deliver a PDF instead (or as well) when the user asked for one:
`<navin-python> .navin/resources/tools/html2pdf.py slides/ -o deck.pdf` prints
the same slides as 16:9 pages with a real text layer.

Charts stay editable: put the numbers in `series` or `chart` in the deck JSON
(see "Charts stay editable" below) and the converter places a native chart,
never a matplotlib PNG, unless the user wants a pixel-exact figure.

## Visual QA (before converting)

Every slide is laid out in Chromium at 1920x1080 - the frame the converter
uses - and read the way the room will read it: what runs off the edge, what
prints over what, what cannot be read from the back, what carries no title,
what chart has no insight line. Each slide comes back scored out of 100
against `_engine/quality.json`, with the thing to change.

```bash
<navin-python> .navin/resources/tools/ppt_qa.py slides/
<navin-python> .navin/resources/tools/ppt_qa.py slides/ --json
```

It exits non-zero when the weakest slide falls under the declared threshold of
85: one weak slide is the one the room remembers, so an average is not enough.
Run it on the filled slides, before converting, because that is the stage where
a finding is still one line of HTML away from being fixed. Do not deliver a
deck that is still asking for rework.

Two findings fail the deck whatever it scores, because they mean the slide was
never written rather than written badly:

- `template-copy`: the text is still the template's, word for word. Every
  template declares `text_policy: replace_all_visible_text`, and QA now checks
  it by comparing the deck against the template it was copied from. Copying a
  template and adapting the first slides leaves the rest describing the
  template's imaginary company.
- `empty`: filler the template shipped with - lorem in any of its variants,
  `Your title here`, a `(+62) 000 0000 0000` contact block.
- `thin-slide`: a content slide that is only a title and one sentence. Cover
  and statement may do that. Cards, text, process and text-image may not.

Two more are worth knowing in advance, because they are invisible in the
preview and only bite once the deck is a PowerPoint file:

- `title-not-heading`: the slide title must be an `h1`/`h2` (the `.nv-title`
  class alone is not enough). The converter puts it in PowerPoint's title
  placeholder, which is what fills the Outline view and names the slide in the
  thumbnail panel. A `div` styled large arrives as an anonymous textbox.
- `theme-unlinked`: the slide names colours instead of reading tokens, so it
  cannot change theme. Materialize it with `ppt_design.py` rather than
  hand-rolling a palette.

## No empty blocks (mandatory)

A template slide holds a fixed number of cards, columns, quotes and figures.
Leaving some of them with sample text, a placeholder image or nothing at all
is what makes a deck look unfinished.

- Fill every block of a slide with real content, or delete the block from the
  HTML and rebalance the layout (a 4-card grid becomes a 3-card grid, not
  three cards and a hole).
- Never ship "Lorem ipsum", "Title here", "XX%", an untouched sample label or
  an empty image frame.
- Not enough material for a slide? Drop the slide instead of padding it.
- Title + one sentence on `text`, `cards`, `text-image` or `process` is
  unfinished. QA flags it as `thin-slide`. Rewrite with three claims or
  a visual, then render again.
- A block whose text is much shorter than the sample changes the balance:
  either write to the intended length or pick a slide layout that fits.

## Before delivering

1. Reopen the file and check what it actually contains, not what the script
   intended:

   ```python
   from pptx import Presentation
   prs = Presentation("deck.pptx")
   for i, slide in enumerate(prs.slides, 1):
       boxes = [s for s in slide.shapes if s.has_text_frame and s.text_frame.text.strip()]
       chars = sum(len(s.text_frame.text) for s in boxes)
       print(i, "text boxes:", len(boxes), "chars:", chars)
   ```

   A slide with zero text boxes is a rasterized slide: fix the conversion, do
   not deliver it.
2. Read the text back for leftover sample copy, source-language strings, em
   dashes and emoji.
3. Validate the file itself:

   ```bash
   <navin-python> .navin/resources/tools/doc_check.py deck.pptx
   ```

   It blocks on leftover sample copy, unresolved `{{placeholders}}`, em dashes,
   emoji, an empty slide, text off the canvas and a missing title, and warns
   on thin slides and tiny text. Fix the source and convert again.
4. Look at the deck, do not assume it. The bundled previewer renders the saved
   file into PNGs and a contact sheet, so read them back with vision:

   ```bash
   <navin-python> .navin/resources/tools/preview_document.py deck.pptx previews/
   ```

   With LibreOffice installed it is the real render (fonts, charts, pictures);
   without it, the geometry of every shape is drawn at its true place, which
   still shows a decor that stops short of the edge, text over text, an empty
   block or unreadable copy.
5. Every slide of the template you kept has real content. Template captions
   that explain the layout ("The 'what' defines the products a brand offers")
   are instructions to you, not copy: replace them with the client's content or
   delete them.

## Charts stay editable

Give the numbers in the deck JSON and the converter builds a native PowerPoint
chart (the client can restyle or edit the data in PowerPoint):

```json
{"layout": "chart", "title": "Revenue tripled in three years", "insight": "+43% year on year",
 "series": [{"label": "2024", "value": "1.9M"}, {"label": "2025", "value": "2.8M"}, {"label": "2026", "value": "3.6M"}]}
```

Several series or another type use `chart`:

```json
{"layout": "chart", "title": "EMEA leads on margin", "insight": "41% in Q4",
 "chart": {"type": "line", "categories": ["Q1", "Q2", "Q3", "Q4"],
           "series": [{"name": "EMEA", "values": [31, 34, 36, 41]}, {"name": "Americas", "values": [22, 25, 27, 29]}],
           "number_format": "0\"%\""}}
```

Types: column, bar, stacked-column, stacked-bar, line, area, stacked-area,
pie, doughnut, radar. The HTML preview draws the same chart (bars, polyline or
donut) so `ppt_qa` and the PDF print see what the PPTX will hold. A chart the
user wants pixel-exact stays an image.

## The user's own .pptx

When the user hands over their company deck, it is the design. Do not redraw it:

```bash
<navin-python> .navin/resources/tools/office_template.py inspect brand.pptx      # layouts, placeholders, theme fonts and colours
<navin-python> .navin/resources/tools/office_template.py build brand.pptx --slides deck.json -o deck.pptx
<navin-python> .navin/resources/tools/office_template.py fill brand.pptx --data values.json -o deck.pptx   # {{key}} placeholders
```

`build` adds slides on the template's own layouts (title, subtitle, bulleted
body with levels, picture, table, notes) and drops the sample slides unless
`--keep-slides` is passed. Then `doc_check` and `preview_document` as above.

## Workflow

1. Get the validated slide plan (one message per slide) from `presentation-designer`.
2. Use the brand template from `template-manager` when it exists.
3. Generate slide by slide from a structured dict/JSON - the script stays rerunnable under `build/`, and out of the reply.
4. QA the slides with `ppt_qa.py`, fix the findings, run it again until it passes.
5. Convert, stamp speaker notes, open `slides/index.html` to flip through the deck, then run the delivery checks above.

## Rules

- One idea per slide; ≤6 bullets, ≤10 words each - push data to charts.
- Numbers get big-figure treatment, not buried in sentences.
- Charts as native pptx charts when they need to stay editable; as images when they must be pixel-exact.
