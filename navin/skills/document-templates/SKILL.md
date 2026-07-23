---
name: document-templates
description: Visual theme catalog and template handling for generated documents (PPTX, DOCX, PDF, XLSX). Use whenever a document request names a theme, or provides a template file to adapt.
metadata: {"navin":{"emoji":"🎨","category":"documents"}}
---

# Document Templates & Themes

## Overview

Two ways to control the look of generated documents:

1. **Built-in themes** — the user picks a theme by name; apply its exact spec below.
2. **User templates** — the user provides a `.pptx` / `.docx` / `.xlsx` file; open it and build inside it so the result inherits its masters and styles.

Never mix themes. Pick one and apply it to every slide/page consistently.

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
- **DOCX (python-docx)**: define Heading 1-3 and Normal styles once with the palette and fonts, then use styles only — never inline-format paragraph by paragraph.
- **XLSX (openpyxl)**: header rows filled with accent 1 + white bold text; alternating row tint derived from the background; freeze panes on headers.
- **PDF (reportlab)**: mirror the same palette and font hierarchy.
- Charts and shapes use accent colors only; grayscale for everything else.

## User-provided templates

When the user supplies a template file (in the workspace, attached, or at a path they give):

1. **Open it, don't recreate it**: `Presentation('template.pptx')`, `Document('template.docx')`, `load_workbook('template.xlsx')`.
2. **Reuse its layouts/styles**: for PPTX use `prs.slide_layouts` from the template's master; for DOCX use its existing styles by name; do not inject a different palette on top.
3. **Replace placeholder text** if present ({{title}}-style or lorem ipsum); keep positioning intact.
4. Save as a **new file** — never overwrite the user's template.

Suggested convention: keep reusable templates in `templates/` inside the workspace; list them for the user when they ask "what templates do I have". Creating and versioning a branded template library is `template-manager`'s job — defer to it for that.

## Downloading templates

Only fetch a template from the web when the user gives an explicit URL or asks for it. Prefer openly licensed sources, note the license in your reply, and save the file under `templates/` before adapting it.

## Rules

- Theme named in the request → apply its exact hex values, not an approximation.
- Template file provided → template wins over any theme.
- Neither → pick the theme that fits the audience and say which one you used.
