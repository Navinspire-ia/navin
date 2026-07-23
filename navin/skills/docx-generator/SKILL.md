---
name: docx-generator
description: Create, read, and edit Word (.docx) files — styles, tables, images, headers, and templates — using python-docx. Use whenever a deliverable must be a Word document.
metadata: {"navin":{"emoji":"📘","category":"documents"}}
---

# DOCX Generator

## Overview

Produce real Word documents programmatically with `exec` + Python `python-docx`. Install once per environment: `pip install python-docx`.

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
section.header.paragraphs[0].text = "Navinspire — Confidentiel"

doc.save("out.docx")
```

Reading/editing: iterate `doc.paragraphs` and `doc.tables`; modify `run.text` to preserve formatting. For find-replace across runs, join runs carefully (formatting lives at run level).

## Workflow

1. Content first: draft the document in Markdown, validate with the user.
2. Choose base: blank, or the brand template from `template-manager` (`Document("template.docx")` keeps styles, headers, logos).
3. Generate with a script; map Markdown structure → headings/paragraphs/tables.
4. Verify: reopen the file with python-docx and dump the text to confirm structure; deliver the path.
5. PDF version needed? Convert via LibreOffice: `soffice --headless --convert-to pdf out.docx`.

## Rules

- Use named styles from the template rather than manual formatting everywhere — consistency and editability.
- FR typography in FR documents (see `proofreader`).
- Complex layout (multi-column brochures) → consider `pdf-generator` instead.
