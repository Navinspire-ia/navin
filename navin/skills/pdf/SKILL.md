---
name: pdf
description: Reads and manipulates PDF files with pdfplumber, pypdf, PyMuPDF and reportlab. Extracts text and tables per page, lists and fills AcroForm fields with optional flattening, overlays text at measured coordinates on non-fillable forms, merges, splits, rotates, extracts and renders pages to PNG, reads metadata. Use when the user asks to "extract the text from this PDF", "fill this PDF form", "merge these PDFs", "split the PDF into chapters", "get the tables out of the report" or "rotate the scanned pages". In French "remplis ce formulaire PDF", "fusionne ces PDF", "extrais le texte et les tableaux de ce PDF" or "fais pivoter les pages scannées". New PDFs rendered from HTML belong to pdf-generator.
metadata: {"navin":{"emoji":"📄","category":"documents"}}
---

# PDF Toolkit

## Overview

This skill handles PDFs the user already has: contracts to read, forms to fill, reports to split, scans to rotate, pages to render for review. Four libraries ship with Navin and each has a job: pdfplumber for text and tables with positions, pypdf for page-level operations and form fields, PyMuPDF (`import pymupdf`) for rendering, flattening and fast text, reportlab for drawing new content. `references/libraries.md` explains when to use which.

Pick the sibling skill when the job is different:

- `pdf-generator`: produce a new PDF from HTML laid out as A4 pages, or print a finished DOCX, PPTX or XLSX.
- `pdf-ocr-extractor`: the PDF is a scan without a text layer and needs OCR.
- `docx` / `pptx` / `xlsx`: the source is an office file.

## Workflow

### 1. Look first

```
python3 navin/skills/pdf/scripts/pdf_pages.py info file.pdf
python3 navin/skills/pdf/scripts/pdf_text.py file.pdf --pages 1-3 --format markdown
```

`info` prints page count, page sizes, metadata, encryption flag and whether the file has form fields. `pdf_text.py` returns the text per page (`--format json|markdown|text`), with `--tables` to add every detected table as rows of cells and `--layout` to keep columns aligned. Exit code 1 means no text layer: the file is a scan, route it to `pdf-ocr-extractor` instead of guessing.

For a visual check, render pages: `pdf_pages.py render file.pdf --pages 1,2 --dpi 110 --out-dir out/` writes one PNG per page; open them with `read_file`.

### 2. Extract

- Text and tables: `pdf_text.py report.pdf --tables --format json --out report.json`, then process the JSON with Python or hand the tables to `spreadsheet-analyst`.
- Specific pages into a new PDF: `pdf_pages.py extract file.pdf --pages 4-9 --out chapter2.pdf`.
- Images or exact coordinates: use PyMuPDF directly in a short script (`page.get_text("dict")`, `page.get_images()`), see the snippets in `references/libraries.md`.

### 3. Forms

1. `python3 navin/skills/pdf/scripts/pdf_forms.py fields form.pdf --json` lists every field with its type (text, checkbox, radio, choice), current value and allowed options (export values for checkboxes and radios).
2. Write a JSON mapping `{"field name": "value"}` with `write_file`, using the exact field names and, for checkboxes, the export value shown by `fields` (often `/Yes` or `/On`).
3. `pdf_forms.py fill form.pdf --data values.json --out filled.pdf` writes the values with pypdf and sets NeedAppearances so viewers regenerate the widgets. Add `--flatten` when the result must not be editable (PyMuPDF bakes the appearance streams into the page).
4. Re-run `fields` on the output, or render page 1, to confirm the values appear.

When the PDF has no fields (a printed form), overlay text instead:

1. `python3 navin/skills/pdf/scripts/pdf_overlay.py form.pdf --grid --page 1 --out-dir grid/` renders the page with a labelled coordinate grid (PDF points, origin bottom-left). Read the PNG and note the x and y of each blank.
2. Write an items list `[{"page": 1, "x": 120, "y": 640, "text": "Jane Doe", "size": 10}]` and run `pdf_overlay.py form.pdf --items items.json --out filled.pdf`; `--text --x --y --page` handles a single value.
3. Render the result and compare against the grid.

`references/forms.md` covers field types, export values, appearance streams and the overlay method in detail.

### 4. Page operations

| Need | Command |
|------|---------|
| Merge | `pdf_pages.py merge a.pdf b.pdf c.pdf --out merged.pdf` |
| Split by ranges | `pdf_pages.py split file.pdf --ranges 1-3,4-10,11-12 --out-dir parts/` |
| Split every N pages | `pdf_pages.py split file.pdf --every 1 --out-dir pages/` |
| Rotate | `pdf_pages.py rotate file.pdf --degrees 90 --pages 2,5 --out fixed.pdf` |
| Extract | `pdf_pages.py extract file.pdf --pages 1,3-4 --out subset.pdf` |
| Render | `pdf_pages.py render file.pdf --dpi 150 --out-dir png/` |

Pages are 1-based everywhere in the arguments and the output. Rotation is clockwise and stored on the page object, so the text layer stays intact.

### 5. Deliver

State what was produced (file, pages, fields filled, tables found), what could not be done (no text layer, encrypted file, missing field) and show one rendered page when the result is visual. Keep the original untouched; outputs go to new files.

## Scripts

| Script | Command |
|--------|---------|
| `scripts/pdf_text.py` | `FILE [--pages 1-3,7] [--format json\|markdown\|text] [--tables] [--layout] [--out F]` (exit 1 when no text) |
| `scripts/pdf_forms.py` | `fields FILE [--json]`; `fill FILE --data values.json --out F [--flatten]` |
| `scripts/pdf_pages.py` | `info FILE`; `merge FILES... --out F`; `split FILE [--ranges R] [--every N] --out-dir D`; `extract FILE --pages P --out F`; `rotate FILE --degrees D [--pages P] --out F`; `render FILE [--pages P] [--dpi N] --out-dir D` |
| `scripts/pdf_overlay.py` | `FILE --items items.json --out F [--font Helvetica] [--size N]`; `FILE --text T --x X --y Y [--page N] --out F`; `FILE --grid [--page N] [--step 50] [--dpi N] --out-dir D` |

Exit codes: 0 success, 1 nothing found or no text layer, 2 usage or I/O error.

## Rules

- Language gate: when text is written into a PDF (form values, overlays, stamps), keep the language of the form; ask when a free-text answer must be composed and the language was not stated.
- Never write em dashes (U+2014) or en dashes (U+2013) into form values or overlays. Use "-".
- Do not OCR silently: a PDF without a text layer is reported as such and routed to `pdf-ocr-extractor`.
- Encrypted files: `info` reports it; ask for the password, never attempt to break it.
- Keep forms fillable unless the user asks for a flattened copy; deliver both when the use is unclear.
- Fonts for overlays are the standard 14 (Helvetica, Times-Roman, Courier) unless a TTF path is given; non-Latin text needs a TTF with the glyphs.
- Always render or re-read the output before delivering; a fill that "succeeded" with empty widgets is a failure.

## References

- `references/libraries.md`: pypdf, pdfplumber, PyMuPDF and reportlab compared, with snippets for text, tables, images, metadata, encryption, watermarks and bookmarks.
- `references/forms.md`: AcroForm field types, export values, NeedAppearances and appearance streams, flattening, and the overlay method for non-fillable forms.
