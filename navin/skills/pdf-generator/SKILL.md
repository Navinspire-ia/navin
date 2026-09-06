---
name: pdf-generator
description: Generate PDF documents - reports, one-pagers, certificates, agreements, product sheets, quotes - from HTML laid out as A4 pages (Chromium) or from a finished DOCX/PPTX/XLSX (LibreOffice). Use when the deliverable must be a polished PDF.
metadata: {"navin":{"emoji":"📕","category":"documents"}}
---

# PDF Generator

## Overview

One bundled command prints the PDF and checks it: `html2pdf`. It runs headless
Chromium on HTML laid out as A4 `.page` sections (what `word_design render` and
every PDF/Word template produce), refuses a page under the quality threshold,
merges a slides folder into a 16:9 deck PDF, and converts a finished
`.docx` / `.pptx` / `.xlsx` through LibreOffice when it is installed. Navin
ships the Python libraries; do not check imports or install anything up front.

Text in the PDF must be real text, selectable and searchable, never a page
rendered as a bitmap. `html2pdf` reads the PDF back and says so when a page has
no text layer.

## Language and locale gate (mandatory)

- Before creating or exporting a PDF, require the user to explicitly select the document language. Never infer it from the prompt, UI language, template, names, or location. If absent, ask and stop generation.
- Preserve that language in all visible text and document metadata, including headings, labels, captions, headers/footers, dates, numbers and currencies. Translate every source-template label.
- Format dates, decimal/grouping separators and currency according to the selected locale, consistently across the source and rendered PDF.
- For Arabic and other RTL languages, set `lang` and `dir="rtl"` on the completed HTML, use CSS logical properties and RTL-aware alignment, preserve logical reading order, and use embedded fonts with full glyph coverage (for example Noto Naskh Arabic, Noto Sans Arabic or Amiri). Visually verify shaping and page flow.

## Typography & icon rules (mandatory)

- Never use Unicode em dash (U+2014) or en dash (U+2013) in PDF text. Use commas, colons, periods or parentheses instead.
- Never use emoji or generic AI-style icons. Use numbers, plain monochrome glyphs or simple CSS shapes consistent with the design.

## HTML document template library

Navin materializes the selected PDF template inside the active workspace
(`document.html` with A4 `.page` sections + `metadata.json` + `image.png`
preview). When the runtime context contains a "Document Template Attachment",
the user picked one in the WebUI - it is mandatory: copy `document.html` into
the workspace, keep the design (the `<style id="navin-theme">` tokens, the CSS),
replace every visible sample string, add or remove `.page` sections as the
content needs, then print with `html2pdf`. Never restyle the template.

Bundled PDF templates: `attestation_officielle` (certificate, double frame and
seal), `contrat_prestation` (two-page agreement with payment table and
signatures), `fiche_produit` (product sheet with banner, benefits, specs and
pricing). Word templates print to PDF with the same command.

## Route picker

| Need | Do |
|------|----|
| Report, letter, proposal, brief | `word_design render --document doc.json -o document.html`, then `html2pdf document.html -o out.pdf` |
| Certificate, agreement, product sheet | Fill the PDF template's `document.html`, then `html2pdf` |
| Deck as PDF | `ppt_design deck ...` into `slides/`, `ppt_qa slides/`, then `html2pdf slides/ -o deck.pdf` |
| PDF of a finished `.docx` / `.pptx` / `.xlsx` | `html2pdf file.docx -o file.pdf` (LibreOffice; the command names the install when it is missing) |
| Batch (N invoices, N certificates) | One script writes N HTML files from one data file and calls `html2pdf` on each |

The tools live in the workspace under `.navin/resources/tools/` and run with
the interpreter the runtime context names (`navin python` in a packaged build).

## Workflow

1. Draft the content; validate facts, numbers and the selected language.
2. Lay it out: semantic JSON through `word_design render`, or the template's `document.html` filled by hand. Colours and fonts stay tokens (`var(--nv-accent)`); never a hex in the body.
3. Score the HTML: `word_qa document.html` marks each page out of 100 and names what to change (overflow, contrast, tiny text, headings in grids).
4. Print: `html2pdf document.html -o out.pdf` (`--paper letter`, `--landscape` on request). It refuses a failing QA unless the user explicitly accepts the document as is (`--force`), and it reports pages that spill past A4, empty pages, a missing text layer or a missing `<title>`.
5. Validate the file: `doc_check out.pdf` blocks on leftover sample copy, unresolved placeholders, em dashes and emoji.
6. Look at it: `preview_document out.pdf previews/` renders the pages and a contact sheet; read the sheet before handing the file over.
7. Deliver the PDF path; keep the HTML source beside it so the document stays editable.

## Rules

- A4 by default (Letter only on request); embed fonts for AR text (RTL: add `direction: rtl`).
- Reading existing PDFs is `pdf-ocr-extractor`'s job, not this skill's.
- Batch generation: one script + data file, never N manual runs.
- For a legal contract, require configurable governing law and dispute forum fields. Check defined terms, parties, dates, obligations, cross-references, annexes and signatures for internal consistency.
- Keep the final, fully translated HTML source beside every contract PDF so the agreement remains editable and auditable.
- Every generated contract must prominently warn that qualified local counsel must review it before signature (the `contrat_prestation` template ships that notice; keep it).
- ReportLab stays available for dense programmatic output (`from reportlab.pdfgen import canvas`), but a designed document goes through HTML so the same source can become a DOCX.
