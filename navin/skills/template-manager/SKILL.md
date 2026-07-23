---
name: template-manager
description: Maintain the library of branded document templates — DOCX, PPTX, PDF styles, email blocks — versioned and reusable by all document skills. Use to create or update templates.
metadata: {"navin":{"emoji":"🧩","category":"documents"}}
---

# Template Manager

## Overview

One source of truth for branded output. Every document skill (`docx-generator`, `pptx-generator`, `pdf-generator`, `report-generator`) pulls its base from here.

## Library layout

```
templates/
  brand-tokens.md          # colors (hex), fonts, logo paths, spacing rules — per brand
  docx/  proposal.docx  report.docx  letter.docx  cv.docx
  pptx/  pitch.pptx  report.pptx
  pdf/   base.css  invoice.html
  email/ signature.html  blocks.md
  CHANGELOG.md
```

## Brand tokens format

```markdown
# Brand — <Navinspire|Guidia|Lynara>
- Primary: #0369FF · Secondary: ... · Text: ...
- Fonts: headings <font>, body <font> (+ fallback installed on this machine)
- Logo: templates/assets/<brand>-logo.png (+ white variant)
- Rules: marges 2cm, pied de page "<brand> — confidentiel", numérotation X/Y
```

## Workflow

**Creating a template**
1. Gather brand assets from the user (or `brand/` files from `brand-voice-manager`).
2. Build the file: DOCX with named styles (Heading 1-3, Body, Quote, table style); PPTX with proper slide masters/layouts; CSS for the PDF route.
3. Test: generate a sample document through the consuming skill; fix styles until clean.
4. Log in CHANGELOG.md with version + what changed.

**Using**
- Document skills load the template path; tokens come from `brand-tokens.md` — never hardcode a hex in a generator script.

**Updating**
- Change the template once → regenerate affected recurring documents (`report-generator` pipelines) to confirm nothing breaks.

## Rules

- One template change per commit-like changelog entry; old versions kept (`proposal-v2.docx` → archive, not delete).
- Multi-brand: no shared template silently serving two brands — duplicate and re-skin.
- New document types start from the closest existing template, not from blank.
