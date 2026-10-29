---
name: xlsx
description: Builds and audits Excel workbooks that carry real formulas. Inspects sheets, named ranges and error cells, recalculates with headless LibreOffice or runs a static formula audit, applies the Navin table style and the financial model color code, repacks and validates the package. Use when the user asks to "check the formulas in this spreadsheet", "build a financial model", "find the formula errors", "recalculate the workbook" or "format this sheet as a clean table". In French "crée un fichier Excel avec des formules", "vérifie les formules de ce tableur", "recalcule le classeur" or "mets en forme ce tableau". Data analysis with pandas belongs to spreadsheet-analyst.
metadata: {"navin":{"emoji":"📈","category":"documents"}}
---

# XLSX Toolkit

## Overview

This skill is for workbooks where the formulas are the product: budgets, financial models, trackers, pricing sheets. It inspects what exists, writes formulas instead of pasted results, recalculates, scans for error values and applies a consistent Navin style. Everything runs with `exec` on the bundled openpyxl, pandas and xlsxwriter libraries.

Pick the sibling skill when the job is different:

- `spreadsheet-analyst`: analyse or clean data that arrives as xlsx or csv, pivots, charts, pandas work.
- `docx` / `pptx` / `pdf`: the deliverable is another format.

## Workflow

### 1. Inspect

`python3 navin/skills/xlsx/scripts/xlsx_inspect.py book.xlsx --sample 5` prints, per sheet, the dimensions, the guessed header row, the number of formulas, merged cells, cells already holding error values and sample rows; at workbook level the named ranges, tables, data validations and conditional formats. Add `--markdown` for a readable summary to quote to the user, `--sheet NAME` to focus.

Read the result before writing anything: it tells where inputs live, which sheets feed others and whether the file already contains errors the user may not know about.

### 2. Build or edit with formulas

Formula-first rule: when a value can be computed from other cells, write the formula (`ws["D2"] = "=B2*C2"`), never the computed number. Hard-coded results go stale and hide the logic the user asked for.

- Write with openpyxl for edits of an existing file (`load_workbook`, set cells, `save`), or xlsxwriter for a brand-new file with many formats.
- Use structured references and named ranges for inputs that drive the model (`=Revenue*Growth`), so assumptions are editable in one place.
- Pin the header row and the first column when the sheet scrolls; add an autofilter on data tables.
- Follow the financial model color code (mapped to the Navin palette): inputs Teal `#0e7490`, formulas Ink `#17171a`, links to other sheets Leaf `#6fbf6a`, checks and flags Coral `#ff5f57`. `xlsx_style.py --model-colors` applies it from the cell content.
- openpyxl is one-based for rows and columns (`ws.cell(row=1, column=1)`); pandas and Python lists are zero-based. Convert explicitly, and prefer A1 references in formulas.

### 3. Recalculate and scan

openpyxl never computes formulas; a file saved by it has no cached values until a spreadsheet application opens it. Before delivering:

```
python3 navin/skills/xlsx/scripts/xlsx_recalc.py book.xlsx --out book.xlsx
```

With LibreOffice present the workbook is re-saved headless (every formula recomputed) and the cached values are scanned for `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, `#N/A`, `#NUM!`, `#NULL!`. Without LibreOffice the script falls back to a static audit (references to missing sheets, out-of-range references, literal errors, division by a zero literal) and the JSON report says `"recalculated": false`. Say which mode ran. Exit code 1 means errors were found: fix them (see `references/formulas-and-errors.md`) and run again until the report is clean.

### 4. Style

`python3 navin/skills/xlsx/scripts/xlsx_style.py book.xlsx --sheet Data --out book.xlsx` applies the Navin table style to the used range: header row filled Ink with Paper text, zebra rows in Cream, freeze panes under the header, autofilter, fitted column widths. Options: `--range A1:F200`, `--number-format C=#,##0.00 --number-format D=0.0%`, `--no-zebra`, `--no-freeze`, `--no-filter`, `--no-widths`, `--model-colors`.

### 5. Validate, convert, deliver

- `python3 navin/skills/xlsx/scripts/office_pkg.py validate book.xlsx` checks the zip, content types, relationships, XML well-formedness and an openpyxl round trip; `unpack` / `pack` allow package-level fixes (shared strings, broken relationships, custom XML).
- `python3 navin/skills/xlsx/scripts/office_convert.py book.xlsx --to pdf --out-dir out/` prints the sheets through headless LibreOffice when a PDF is wanted; the script reports clearly when `soffice` is absent.
- Re-run `xlsx_inspect.py` on the final file and quote the sheet summary and the recalculation status to the user.

## Scripts

| Script | Command |
|--------|---------|
| `scripts/xlsx_inspect.py` | `FILE [--sheet NAME] [--sample N] [--markdown]` |
| `scripts/xlsx_recalc.py` | `FILE [--out F] [--static] [--timeout S]` (exit 1 when errors are found) |
| `scripts/xlsx_style.py` | `FILE [--sheet NAME] [--range A1:Z99] [--out F] [--number-format COL=FMT] [--model-colors] [--no-zebra] [--no-freeze] [--no-filter] [--no-widths]` |
| `scripts/office_pkg.py` | `unpack ARCHIVE DIR`; `pack DIR ARCHIVE`; `validate ARCHIVE [--json]` |
| `scripts/office_convert.py` | `SOURCE --to pdf\|xlsx\|html [--out-dir D] [--timeout S] [--json]` |

## Rules

- Language gate: sheet names, headers and labels follow the document language; ask for it when the user did not state it and the workbook is new.
- Never write em dashes (U+2014) or en dashes (U+2013) in headers, labels or notes. Use "-".
- Formulas over values; one assumptions block; no merged cells inside data ranges (they break sorting and filters).
- Recalculate and scan before delivering; a workbook with `#REF!` cells is not finished. When recalculation could not run, say so explicitly.
- Keep the file editable: no images of tables, no protection unless asked.
- Numbers keep their type: do not store numbers as text; set a number format instead.
- Dates are real dates (`datetime`), formatted with a locale-appropriate format, never strings.

## References

- `references/formulas-and-errors.md`: error values and their fixes, recalculation behaviour of openpyxl versus LibreOffice, named ranges, tables, data validation and conditional formatting recipes.
