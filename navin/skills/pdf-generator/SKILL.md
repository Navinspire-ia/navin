---
name: pdf-generator
description: Generate PDF documents — reports, one-pagers, quotes, branded deliverables — via HTML/CSS, ReportLab, or DOCX conversion. Use when the deliverable must be a polished PDF.
metadata: {"navin":{"emoji":"📕","category":"documents"}}
---

# PDF Generator

## Overview

Three production routes; pick by layout complexity. All run with `exec`.

## Route picker

| Route | Best for | Setup |
|-------|----------|-------|
| HTML/CSS → PDF (WeasyPrint) | branded docs, invoices, one-pagers — full CSS control | `pip install weasyprint` |
| DOCX → PDF (LibreOffice) | documents already produced by `docx-generator` | `soffice --headless --convert-to pdf file.docx` |
| ReportLab | programmatic/dense output (tables, charts, batch) | `pip install reportlab` |

## HTML route (recommended default)

```python
from weasyprint import HTML
html = f"""
<html><head><style>
  @page {{ size: A4; margin: 2cm; @bottom-center {{ content: counter(page) "/" counter(pages); }} }}
  body {{ font-family: 'DejaVu Sans', sans-serif; color: #1a1a2e; }}
  h1 {{ color: #0369ff; border-bottom: 2px solid #0369ff; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td, th {{ border: 1px solid #ddd; padding: 6px 10px; }}
</style></head><body>{content}</body></html>"""
HTML(string=html).write_pdf("out.pdf")
```

Charts: generate with matplotlib → embed as `<img src="chart.png">`.

## Workflow

1. Draft content in Markdown; validate.
2. Pick the route; apply brand tokens (colors, logo path) from `template-manager`.
3. Generate; visually verify page breaks (render page count, check no orphan headers).
4. Deliver the file path; keep the generator script in the workspace for reruns.

## Rules

- A4 by default (Letter only on request); embed fonts for AR text (RTL: add `direction: rtl`).
- Reading existing PDFs is `pdf-ocr-extractor`'s job, not this skill's.
- Batch generation (N invoices/certificates): one script + data file, never N manual runs.
