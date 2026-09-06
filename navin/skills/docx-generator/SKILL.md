---
name: docx-generator
description: Create, read, and edit Word (.docx) files - styles, tables, images, headers, and templates - using python-docx. Use whenever a deliverable must be a Word document.
metadata: {"navin":{"emoji":"📘","category":"documents"}}
---

# DOCX Generator

## Overview

Produce real Word documents programmatically with `exec` + Python `python-docx`, which Navin ships. Do not run import checks or install anything up front: write the script, and only if it actually fails on a missing dependency, report it and ask before touching the environment.

The document must stay editable: headings, paragraphs, tables and styles, never a page pasted as an image.

## Language and locale gate (mandatory)

- Before creating or exporting a DOCX, require the user to explicitly select the document language. Do not infer it from the prompt, UI language, template, names, or location. If it is absent, ask and stop generation.
- Carry that language through every heading, label, caption, table, header/footer, filename metadata, date, number and currency. Do not leave source-template text untranslated.
- Use locale-appropriate date, decimal, grouping and currency conventions consistently. Record the selected language/locale in the rerunnable generator data.
- For Arabic and other RTL languages, set paragraph bidi/RTL and right alignment, mirror layout where appropriate, use logical reading order, and choose fonts with the required glyph coverage (for example Noto Naskh Arabic, Noto Sans Arabic or Amiri). Embed or package fonts when the export route permits it; never silently substitute a font lacking glyphs.

## Typography & icon rules (mandatory)

- Never use em or en dashes (U+2014 / U+2013) in document text. Use commas, colons, periods or parentheses instead.
- Never use emoji or generic AI-style icons (📊 ✅ 💡 …) in documents. Use numbers, plain monochrome glyphs (✓ ✗) or clean layout elements instead.

## HTML document template library

Navin materializes the selected editable Word-style template inside the active
workspace (`document.html` with A4 `.page` sections + `metadata.json` + `image.png`
preview). When the runtime context contains a "Document Template Attachment",
the user picked one in the WebUI - it is mandatory. Fill that theme: replace
every sample string, keep the CSS, add images when you have them. Do not invent
another look. Three.js does not belong in a Word page:

1. For a report, letter, proposal, minutes or brief: write semantic JSON
   and let the engine render the HTML. Do not copy the lookbook
   `document.html`. That is how titles double, sample KPIs survive, and
   `image.png` sticks on the page.

   ```json
   {
     "kind": "report",
     "theme": "executive",
     "brand": "Navin",
     "kicker": "Executive report",
     "title": "Revue strategique T3 2026",
     "subtitle": "Traction, risques, decisions demandees.",
     "meta": {
       "prepared_by": "Direction",
       "recipients": "Comite de direction",
       "date": "15 aout 2026"
     },
     "sections": [
       {
         "heading": "1. Synthese",
         "body": "Le trimestre confirme la trajectoire.",
         "kpis": [
           { "label": "ARR", "value": "4,2 M€", "meta": "+18% YoY" }
         ]
       }
     ]
   }
   ```

   ```bash
   <navin-python> -m navin.documents.word_design render --document doc.json -o document.html
   ```

   Section keys: `body` (string or list of paragraphs), `items` (bullets;
   `list`: `steps` / `check`), `kpis`, `table` (`headers`, `rows`, optional
   `total` row), `callout` (string or `{title, text, variant}`), `quote` +
   `author`, `insight`, `image` + `caption`, `subsections`. Common
   variants (`paragraphs`, `text`, `bullets`, `points`, `steps`, a nested
   `cover` object, `meta` as a list of lines) are understood too, but write
   the canonical keys.
   A missing image is omitted. Never leave `image.png`. Never invent a hex.
   A report or proposal with two or more headed sections gets a Sommaire
   page after the cover: a visible list plus `data-doc-toc="2-3"` so Word
   can refresh it. Letters and briefs skip it. A report without that
   field fails quality. Set `language` on the JSON.
2. For a specialized contract template the user attached: read
   `metadata.json`, then replace **every** visible string. Keep the CSS.
   If any lookbook sentence remains, the document is rejected.
3. Convert the filled file with the bundled converter, whose command is given
   in the runtime context:

   ```bash
   <navin-python> .navin/resources/tools/html2docx.py document.html -o report.docx
   ```

   Chromium measures the laid-out page, then headings become Word headings,
   lists become real lists, tables become real tables, and typography, colors,
   shading and borders follow. Banners repeated on every `.page` land in the
   running header, a "Page 3 / 8" footer becomes PAGE and NUMPAGES fields.
   Headings stay with the following paragraph. Table rows do not split across
   pages. Around a second per document. Useful flags: `--keep-fonts` to keep
   the CSS font names instead of their Office equivalents, `--chromium PATH`
   when no browser is found, `--keep-workdir DIR` to inspect intermediate files.

   Word behaviour that CSS cannot express is requested with `data-doc-*` on
   the HTML, never with pixel coordinates:

   - `data-doc-toc="1-3"` on a nav or heading: a real TOC field the reader
     can refresh in Word.
   - `data-doc-section="landscape"` on a `.page`: a new Word section in
     landscape, for wide tables and diagrams. Portrait is the default.
   - `data-doc-columns="2"` on a page or section: newspaper columns.
   - `data-doc-break="page"` on a heading: page break before.
   - `data-doc-keep="next"` or `"together"`: keep with next / keep lines.
   - `data-doc-caption="Figure"` plus `data-doc-bookmark="fig:name"`: a
     numbered caption Word can renumber. Point at it with
     `data-doc-ref="fig:name"`.
   - `data-doc-numbering="1-4"` on a page: Heading 1..4 numbered 1 / 1.1 / ...
   - `data-doc-cover="true"` on the first page: a different first-page header.
   - `data-doc-row="stack"` on a side-by-side row: read it top to bottom
     instead of as a table. Use it on a header band that holds the page
     title, because a heading buried in a table cell gets no outline level
     and never reaches the table of contents. A heading that is the row's own
     child is already handled; this is for one nested deeper.

   Do not invent `{ "x": 12, "y": 40 }` layouts. Describe the document in
   HTML semantics and let html2docx decide the Word constructs.

   Titles must be real `h1`-`h6` tags. A `div` styled large and bold looks
   right in the preview and arrives in Word as plain text: no navigation
   pane, no TOC line, no outline. Keep the class, change the tag.
4. Reopen the result and fix what needs it with python-docx. Never rebuild the
   whole design by hand, and never paste a rendered page as an image.
5. If the user asked for a PDF instead, render the same filled HTML (headless
   Chromium `--print-to-pdf` or ReportLab) rather than converting the DOCX.

## Theme and design tokens

Every Word template carries its palette and its fonts in one place, a
`<style id="navin-theme">` block of `--nv-*` custom properties, and the rest of
its CSS only reads them through `var()`. That is what makes a finished document
re-themable without touching a word of its content.

```bash
<navin-python> .navin/resources/tools/word_design.py themes
<navin-python> .navin/resources/tools/word_design.py apply --theme luxury document.html
<navin-python> .navin/resources/tools/word_design.py audit document.html
```

The 15 themes: `executive`, `consulting`, `corporate`, `modern`, `minimal`,
`technology`, `ai`, `financial`, `annual-report`, `startup`, `proposal`,
`research`, `technical`, `government`, `luxury`.

When you write or extend CSS in a document, use the tokens instead of colours:

- `--nv-surface` the paper, `--nv-bg` the canvas around it
- `--nv-text` running text, `--nv-heading` titles, `--nv-muted` secondary text
- `--nv-accent` the fill behind table headers and bands, `--nv-on-accent` the
  text that sits on it
- `--nv-accent-2` the secondary accent for rules and markers, and
  `--nv-accent-2-ink` when that accent has to be read as small text
- `--nv-border` hairlines, `--nv-subtle` zebra rows, `--nv-soft` callout tints
- `--nv-status-high|mid|low|good` (+ `-soft`) for risk, priority and status,
  which keep their meaning across themes
- `--nv-font-heading`, `--nv-font-body`, `--nv-font-mono`, `--nv-radius`

On a dark panel, dim with `rgba(255,255,255,.85)` rather than a named grey, so
the panel still works when the theme changes its accent.

`audit` fails when a literal colour is left outside the theme block, which is
exactly the thing that survives a theme change and ruins it. Run it before
delivering. `_engine/navin-word.css` holds ready-made token-driven components:
callouts (info, important, warning, recommendation, key finding), insight
blocks, KPI rows, big numbers, figures with captions, checklists, numbered
steps, pros/cons, signature blocks.

## Core recipes

```python
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()                      # or Document("template.docx") to inherit styles
doc.add_heading("Titre", level=1)
p = doc.add_paragraph("Texte avec ")
p.add_run("gras").bold = True

# Table with header
table = doc.add_table(rows=1, cols=3)
table.style = "Light Grid Accent 1"
hdr = table.rows[0].cells
hdr[0].text, hdr[1].text, hdr[2].text = "Col A", "Col B", "Col C"
row = table.add_row().cells

doc.add_picture("logo.png", width=Cm(4))
doc.add_page_break()

# Header/footer
section = doc.sections[0]
section.header.paragraphs[0].text = "Navinspire - Confidentiel"

doc.save("out.docx")
```

Reading/editing: iterate `doc.paragraphs` and `doc.tables`; modify `run.text` to preserve formatting. For find-replace across runs, join runs carefully (formatting lives at run level).

## No empty blocks (mandatory)

Every block of a template gets real content, or it is removed and the layout
rebalanced. No sample text, no placeholder figures, no empty table row, no
orphan image frame. Before delivering, reopen the file and read it through:
leftover sample copy, untranslated labels and empty cells are failures.

## Visual QA (before converting)

The filled HTML is laid out in Chromium and read the way a reader would: what
spills past the margin, what is unreadable, what page is half empty, what
heading Word will never see. Every page comes back scored out of 100 with the
thing to change.

```bash
<navin-python> .navin/resources/tools/word_qa.py document.html
<navin-python> .navin/resources/tools/word_qa.py document.html --json
```

Run it on the filled HTML before converting, because that is the stage where a
finding is still fixable by editing the source. It exits non-zero when the
weakest page falls under 85: the weakest page is what the reader notices, so an
average is not enough. Fix what it names and run it again - do not deliver a
document that is still asking for rework.

It also writes `qa.json` beside the document, and `html2docx` refuses to
convert while that report says fail. Fix the pages and re-run `word_qa.py`
until it passes; `--force` exists only for a user who explicitly accepts the
document as is.

It covers layout (overflow, orphan headings, near-empty pages), readability
(contrast under 4.5:1, unreadably small text), typography, hierarchy (missing,
skipped, or Word-invisible headings), spacing, tables, images and theme
consistency.

## Workflow

1. Content first: draft the document in Markdown, validate with the user.
2. Choose base: the attached HTML template (converter route above), the brand
   template from `template-manager` (`Document("template.docx")` keeps styles,
   headers, logos), or blank.
3. Generate: fill the HTML and convert, or write the python-docx script when
   there is no template.
4. QA the filled HTML with `word_qa.py`, fix the findings, run it again.
5. Verify the file itself: `<navin-python> .navin/resources/tools/doc_check.py report.docx`
   blocks on leftover sample copy, unresolved `{{placeholders}}`, em dashes,
   emoji, empty sections, missing headings and a missing title. Then look at
   it: `<navin-python> .navin/resources/tools/preview_document.py report.docx previews/`
   renders the pages (LibreOffice) into PNGs and a contact sheet.
6. PDF version needed? Print the HTML source:
   `<navin-python> .navin/resources/tools/html2pdf.py document.html -o report.pdf`
   (same pages, real text layer, QA gate). Converting the DOCX is the fallback
   when only the DOCX exists: `html2pdf.py report.docx -o report.pdf` uses
   LibreOffice.
7. The user's own `.docx` (letterhead, contract template): keep it. Run
   `office_template.py inspect letter.docx` to read its styles, sections,
   header and footer and `{{placeholders}}`, then
   `office_template.py fill letter.docx --data values.json -o letter_out.docx`
   (`{{key}}`, lists, `{{image:logo|4cm}}`, `{{rows.field}}` repeated table
   rows). Formatting of the run that carried the placeholder is kept.

## Rules

- Use named styles from the template rather than manual formatting everywhere - consistency and editability.
- FR typography in FR documents (see `proofreader`).
- Complex layout (multi-column brochures) → consider `pdf-generator` instead.
- For a legal contract, make governing law and dispute forum explicit configurable fields. Verify defined terms, party names, dates, obligations, clause cross-references, annexes and signature blocks as one coherent agreement.
- Contract templates are HTML sources: preserve the fully translated and completed HTML beside the exported DOCX (and any PDF), rather than treating HTML as disposable.
- Every generated contract must prominently state that it is a template and must be reviewed by qualified local counsel before signature.
