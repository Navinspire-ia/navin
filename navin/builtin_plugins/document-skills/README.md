# Document Skills

One pack for every office file the agent reads or produces: Word, Excel, PowerPoint and PDF.

## What it does

Navin picks the right skill from the file type and the job:

| Job | Skill |
|-----|-------|
| Edit an existing `.docx`: replace text while keeping formatting, add comments, accept or reject tracked changes, validate the package, convert to PDF | `docx` |
| Write a new Word document from a brief or a template | `docx-generator` |
| Build a workbook with real formulas, recalculate it, scan for `#REF!` and friends, apply the Navin table style | `xlsx` |
| Analyse tabular data (pandas, pivots, charts) | `spreadsheet-analyst` |
| Inventory the shapes of a deck, edit slide text in place, rebuild from a JSON spec, render a contact sheet of thumbnails | `pptx` |
| Write a new deck from a story and a slide plan | `pptx-generator` |
| Extract text and tables, list and fill form fields, merge, split, rotate, render pages, overlay text on non-fillable forms | `pdf` |
| Produce a new PDF from HTML or ReportLab | `pdf-generator` |

## How it works

1. **Inspect first**: every skill ships an inspection script (`docx_text.py`, `xlsx_inspect.py`, `pptx_inventory.py`, `pdf_text.py`) that dumps the file structure as JSON before anything is changed.
2. **Edit at the right level**: high-level libraries (python-docx, openpyxl, python-pptx, pypdf) for ordinary edits; the shared `office_pkg.py` helper unpacks and repacks the OOXML package when the library cannot express the change (tracked changes, comments, raw XML).
3. **Validate before delivering**: `office_pkg.py validate` checks the zip, content types, relationships and XML well-formedness, then reopens the file with the library. Workbooks are recalculated and scanned for error cells.
4. **Convert when asked**: `office_convert.py` wraps LibreOffice headless for PDF, DOCX, PPTX, XLSX and HTML conversions, and says clearly when LibreOffice is absent.

## Examples

```
"Accept all tracked changes in contract-v3.docx and export it to PDF"
"Add a comment on the pricing paragraph of the proposal"
"Build a 12-month cash-flow model in Excel with formulas, not hard-coded numbers"
"Fix the #REF! errors in budget.xlsx"
"Replace the Q3 numbers on slides 4 and 7 of the board deck"
"Give me a thumbnail sheet of this presentation"
"Extract the tables from this PDF into CSV"
"Fill this PDF form with the data in customer.json"
```

## Dependencies

Python: python-docx, openpyxl, python-pptx, pypdf, pdfplumber, PyMuPDF, reportlab, Pillow, lxml (installed with the `documents` extra). LibreOffice (`soffice`) is optional: conversions, recalculation and slide thumbnails use it when present and fall back to a documented static mode otherwise.
