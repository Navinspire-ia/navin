---
name: document-templates
description: Visual theme catalog and template handling for generated documents (PPTX, DOCX, PDF, XLSX). Use whenever a document request names a theme, or provides a template file to adapt.
metadata: {"navin":{"emoji":"🎨","category":"documents"}}
---

# Document Templates & Themes

## Order of work (mandatory)

0. **Start now.** A document request is a simple job, template or not. Do not file a board plan and wait for Build. Write the JSON, render, convert, deliver the PPTX/DOCX in this turn.
1. **Lock the chosen template.** PPT or Word: the Document Template Attachment, or the theme the user named, is the look. Read `metadata.json` + `design-system.json` + `theme.css`. Do not invent hex values, fonts, or a second design system.
2. **Fill it.** Replace every sample string. Keep the blocks. Each PPT theme already has `photos/background.jpg`, `photos/left.jpg`, `photos/right.jpg`. The engine places them. A file the user joined (logo, icon, screenshot) replaces that slot. Delete a block only if it has no content, then rebalance.
3. **Polish on that template.** `presentation-designer` / `professional-writer` / QA. Theme motion tokens (`--nv-duration`, `--nv-ease`, `.nv-anim-fade` / `rise` / `stagger`) are the animation on PPT/Word.
4. **Three.js / framer-motion / Lenis** only on a web page, and only if the user asked for that wow or the product is 3D. Never put them on a slide or a Word page. A WebGL slide is a screenshot. That ignores the template.

Ignoring the selected template is a failed deliverable.

## Overview

Two ways to control the look of generated documents:

1. **Built-in themes** - the user picks a theme by name; apply its exact spec below.
2. **User templates** - the user provides a `.pptx` / `.docx` / `.xlsx` file; open it and build inside it so the result inherits its masters and styles.

Never mix themes. Pick one and apply it to every slide/page consistently.

When the user already selected a PPT in Templates de documents, that attachment is the theme. Fill it with today's presentation bar (arc, stagecraft, critique, notes, QA). Do not replace Aurora Glass / Architect / Startup with magazine/swiss/corporate. Those three are only the fallback when nothing is attached.

Each PPT theme is a design system: `design-system.json` (tokens), `theme.css`
(`--nv-*` variables), sample `slide_XX.html` (lookbook, do not rewrite), and
35 master layouts in `templates/ppt/_engine`. New decks are generated from
those masters. Read `_engine/README.md` before writing HTML.

Motion on documents is the theme `motion` block (duration, easing, fade/rise).
Do not drop Three.js, framer-motion, GSAP or Lottie into a PPT/DOCX/XLSX
template. Those belong on a marketing site (`ui-ux-pro-max` + Motion; `three`
only when the product is actually 3D). A WebGL slide exports as a picture.

## Language, locale and direction (mandatory)

Visual template selection never selects the content language. Before any PPTX,
DOCX, PDF, XLSX or CSV generation, require the user to explicitly choose the
output language. Do not infer it from the interface, prompt, template, party
names or location; ask and stop if it is missing.

- Replace and translate every sample string, including headers, footers, chart
  labels, table headings, notes and metadata. Keep the selected language from
  source HTML/data through every export.
- Use locale-correct dates, decimal/grouping separators, currencies and
  typographic conventions consistently.
- For Arabic and other RTL languages, set the final HTML `lang` and `dir`,
  support `html[dir="rtl"]`, prefer CSS logical properties, mirror layouts
  where useful, retain logical reading order, and use fonts covering both Latin
  and Arabic scripts. Apply equivalent bidi/RTL settings in Office outputs.
- CSV exports are UTF-8. Select and announce the locale-appropriate delimiter
  and document it with the output.

## Typography & icon rules (mandatory, all documents)

- Never use em or en dashes (U+2014 / U+2013) in generated content. Use commas, colons, periods or parentheses instead. Audit every text before delivery.
- Never use emoji or generic AI-style icons (📊 ✅ 💡 🚀 😊 …) in slides, documents or sheets. Use clean typographic elements instead: numbers (01, 02, 03), plain monochrome glyphs (✓ ✗ + −), or simple geometric shapes (bars, dots, squares) consistent with the design.

## Dynamic language contract (mandatory)

- Every built-in HTML template uses `lang="und"` intentionally. Its visible text is sample content, never final copy. Replace every text node, including small labels, charts, legends, tables, headers and footers, with content in the language explicitly selected by the user.
- Set the completed HTML/document language and writing direction. For Arabic and other RTL languages, localize the layout as well as the words: use bidi-aware editable text, logical reading order, mirrored alignment where appropriate and fonts with full glyph coverage.
- Raster images and CSS backgrounds must be language-neutral. Before reuse, verify that they contain no words, numbers tied to a locale, captions or UI screenshots with source-language text. Replace any text-bearing image or rebuild its labels as editable text in the selected language.
- Never export while source-language sample text remains. Perform a final visual and textual scan before delivery.

## Editable and complete (mandatory, all documents)

- Deliverables are working files, not pictures of files. Text is text, tables
  are tables, charts are charts. A PPTX made of full-slide screenshots, a DOCX
  wrapping a rendered page or an XLSX holding an image of a table is a failed
  deliverable, however good it looks.
- HTML templates are a design source, not the output format. Fill the HTML with
  the real content, then convert it with the bundled converter for the format:
  `html2pptx.py` for decks, `html2docx.py` for Word, `html2xlsx.py` for
  spreadsheets. The runtime context gives the exact command, interpreter
  included. Only PDF is rendered from the HTML directly. Refine the converted
  file with python-pptx, python-docx or openpyxl; never rebuild a design by
  hand from scratch.
- Every block of a template gets real content, or it is removed and the layout
  rebalanced. No sample text, no placeholder figures, no empty cards, no
  orphan image frames.
- Scan the finished file before delivering: leftover sample copy, untranslated
  labels, empty blocks, em dashes, emoji.

## Built-in theme catalog

| Theme | Palette (bg / text / accent 1 / accent 2) | Headings | Body | Character |
|-------|-------------------------------------------|----------|------|-----------|
| `executive` | #FFFFFF / #1A2333 / #1F3A5F / #C9A227 | Georgia bold | Calibri | Sober, bank-grade; thin gold rules under titles |
| `minimal` | #FFFFFF / #111111 / #111111 / #9CA3AF | Helvetica/Arial light, large | Helvetica/Arial | Extreme whitespace, no decoration, one idea per slide |
| `tech` | #0F172A / #E2E8F0 / #3B82F6 / #22D3EE | Segoe UI/Arial bold | Segoe UI/Arial | Dark mode, thin blue accents, monospace for figures |
| `bold` | #FFFFFF / #000000 / #FFD400 / #000000 | Arial Black, oversized | Arial | Statement typography, yellow highlight blocks |
| `warm` | #FAF3E0 / #3F2A1E / #C1553D / #8A6F5C | Georgia | Calibri | Cream paper feel, terracotta touches, friendly |
| `nature` | #F7F7F2 / #1E3325 / #1E4D2B / #9CAF88 | Calibri bold | Calibri | Green tones, generous margins, organic |
| `elegant` | #FCFAF9 / #2B2B2B / #2B2B2B / #D9A5A5 | Didot/Georgia italic titles | Calibri light | Editorial, blush accents, hairline dividers |
| `corporate` | #FFFFFF / #1F2937 / #0B5394 / #6B7280 | Arial bold | Arial | Classic business blue, safe for any audience |

### Applying a theme

- **PPTX (python-pptx)**: set slide background fill, title/body fonts and RGBColor per the palette; title 32-40pt, body 16-20pt; consistent margins (≥0.5in); accent 1 for titles/dividers, accent 2 sparingly (highlights, chart series).
- **DOCX (python-docx)**: define Heading 1-3 and Normal styles once with the palette and fonts, then use styles only - never inline-format paragraph by paragraph.
- **XLSX (openpyxl)**: header rows filled with accent 1 + white bold text; alternating row tint derived from the background; freeze panes on headers.
- **PDF (reportlab)**: mirror the same palette and font hierarchy.
- Charts and shapes use accent colors only; grayscale for everything else.

## User-provided templates

When the user supplies a template file (in the workspace, attached, or at a path they give):

1. **Open it, don't recreate it**: `office_template.py inspect <file>` lists a PPTX's layouts with their placeholders and boxes, theme fonts and colours, and a DOCX's styles, sections, header, footer, tables and `{{placeholders}}`.
2. **Reuse its layouts/styles**: `office_template.py build brand.pptx --slides deck.json -o out.pptx` adds slides on the template's own layouts (title, subtitle, bullets with levels, picture, table, notes); `office_template.py fill <file> --data values.json -o out` replaces `{{key}}` everywhere (body, tables, headers, footers, notes, groups), one paragraph per list item, `{{image:key}}` pictures and `{{rows.field}}` repeated table rows, keeping the run formatting. Do not inject a different palette on top.
3. Fall back to `Presentation('template.pptx')`, `Document('template.docx')`, `load_workbook('template.xlsx')` only for what those commands do not cover.
4. Save as a **new file** - never overwrite the user's template. Then `doc_check.py` the result and `preview_document.py` it.

Suggested convention: keep reusable templates in `templates/` inside the workspace; list them for the user when they ask "what templates do I have". Creating and versioning a branded template library is `template-manager`'s job - defer to it for that.

## Downloading templates

Only fetch a template from the web when the user gives an explicit URL or asks for it. Prefer openly licensed sources, note the license in your reply, and save the file under `templates/` before adapting it.

## Official PPT catalog (14)

The picker shows only these folders. Missing folders are hidden. Retired names still resolve to a living theme.

| Id | Job |
|----|-----|
| `startup` | Pitch produit, leve de fonds |
| `aurora_glass` | Lancement SaaS, keynote verre |
| `neon_impact` | Reveal nuit, gaming |
| `premium_black` | Luxe sombre, mode |
| `editorial_luxe` | Recit magazine, marque |
| `textbook` | Formation, pedagogie |
| `reponse_appel_offre` | AO public, memoire |
| `competitor_analysis_blue` | War room, concurrentiel |
| `architect` | Immobilier, spatial |
| `premium_green` | ESG, climat |
| `numbers_clean` | Donnees, finance |
| `black_and_white_clean` | Suisse, board sobre |
| `minimalist_2` | Une idee, page claire |
| `portfolio` | Agence, book creatif |

## Recommended HTML templates by document type

When the user did not attach a template, pick one of these (first match that exists in the library) instead of inventing a design:

| Request | Prefer (PPT HTML library names) |
|---------|----------------------------------|
| Magazine / story (ask_user style a) | `editorial_luxe` |
| Swiss / data (ask_user style b) | `black_and_white_clean`, `numbers_clean` |
| Corporate / board (ask_user style c) | `textbook`, `startup`, `premium_black` |
| Project / investor pitch deck | `textbook`, `black_and_white_clean`, `startup`, `premium_black` |
| Brand deck / narrative | `editorial_luxe`, `minimalist_2`, `black_and_white_clean` |
| Product launch / SaaS keynote | `aurora_glass`, `startup`, `neon_impact` |
| Training / internal report | `textbook`, `numbers_clean`, `minimalist_2` |
| Competitive / analysis | `competitor_analysis_blue`, `numbers_clean` |

Never fall back to a freestyle Inter + gradient deck. Template HTML beats a hand-made design every time.

## Rules

- Theme named in the request → apply its exact hex values, not an approximation.
- Template file provided → template wins over any theme.
- Neither → pick a recommended HTML template from the table above (PPT) or the theme that fits the audience, and say which one you used.
- Legal templates keep governing law and dispute forum configurable. Validate
  parties, definitions, dates, duties, cross-references, annexes and signatures
  for contract-wide consistency.
- For every contract DOCX or PDF, preserve the completed HTML source and include
  a clear warning that qualified local counsel must review it before signature.
