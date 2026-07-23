---
name: pptx-generator
description: Create PowerPoint (.pptx) decks — layouts, text, tables, charts, images, and brand templates — using python-pptx. Use whenever the deliverable is a presentation file.
metadata: {"navin":{"emoji":"📙","category":"documents"}}
---

# PowerPoint Generator

## Overview

Build real .pptx files with `exec` + Python `python-pptx` (`pip install python-pptx`). Story and slide plan come from `presentation-designer`; this skill executes.

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

## Workflow

1. Get the validated slide plan (one message per slide) from `presentation-designer`.
2. Use the brand template from `template-manager` when it exists.
3. Generate slide by slide from a structured dict/JSON — script stays rerunnable.
4. Verify: reopen and dump slide titles; export preview via `soffice --headless --convert-to pdf deck.pptx` to eyeball.

## Rules

- One idea per slide; ≤6 bullets, ≤10 words each — push data to charts.
- Numbers get big-figure treatment, not buried in sentences.
- Charts as native pptx charts when they need to stay editable; as images when they must be pixel-exact.
