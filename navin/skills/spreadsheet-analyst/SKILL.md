---
name: spreadsheet-analyst
description: Read, analyze, and produce Excel/CSV files - cleanup, pivots, formulas, charts, and formatted workbooks - using pandas and openpyxl. Use for any spreadsheet task.
metadata: {"navin":{"emoji":"📗","category":"documents"}}
---

# Spreadsheet Analyst

## Overview

Two jobs: (1) analyze data that arrives as xlsx/csv, (2) deliver clean, formatted workbooks. Navin provides the document libraries, so do not check imports or install anything up front: write the script, and only if it actually fails on a missing dependency, explain the missing capability and ask before touching the environment.

Workbooks stay live: real cells, real formulas, real number formats, never values pasted from a screenshot or a picture of a chart.

## Language and locale gate (mandatory)

- Before creating or exporting an XLSX or CSV, require the user to explicitly select the output language. Never infer it from the prompt, UI language, source file, names, or location. If absent, ask and stop generation.
- Preserve that language in sheet names, headers, labels, formulas shown to users, charts, legends, comments, validation messages and workbook metadata. Translate every template/example label.
- Apply locale-appropriate dates, decimal/grouping separators and currency formats consistently. Keep stored numeric/date values typed, not preformatted strings.
- For Arabic and other RTL languages, enable right-to-left sheet views, right-align text where appropriate, mirror visual layout, retain logical column order, and use fonts with Arabic glyph coverage.
- CSV must be UTF-8 (use UTF-8 with BOM only when required for the target spreadsheet application). Choose a locale-appropriate separator, announce it in the delivery message and companion README, and quote fields correctly. Never rely on an ambiguous default delimiter.

## Typography & icon rules (mandatory)

- Never use em or en dashes (U+2014 / U+2013) in cell text, headers or labels. Use commas, colons or parentheses instead.
- Never use emoji or generic AI-style icons (📊 ✅ …) in workbooks. Use conditional formatting, cell fills and plain glyphs (✓ ✗) for status indicators.

## HTML sheet template library

Navin materializes the selected spreadsheet template inside the active workspace
(`document.html` mocking the sheet layout + `metadata.json` + `image.png`
preview). When the runtime context contains a "Document Template Attachment",
the user picked one in the WebUI - it is mandatory:

1. Read `metadata.json` and `document.html` to map the intended layout: KPI
   cards, grouped rows, header fills, conditional colors, total rows.
2. Copy the HTML into a working folder and put the real data in it, keeping
   the structure and the styling.
3. Convert it into a live workbook with the bundled converter, whose command
   is given in the runtime context:

   ```bash
   <navin-python> .navin/resources/tools/html2xlsx.py document.html -o budget.xlsx --csv exports/
   ```

   Numbers arrive as numbers with the number format their display implies
   (currency, percent, thousands, dates), header rows get frozen panes and an
   auto-filter, merged cells and column widths follow the design, and a total
   row becomes a `SUM` formula whenever the sum matches the displayed value.
   `--csv` writes one UTF-8 CSV per sheet. `--no-formulas` keeps plain values.
4. Reopen the workbook and refine with openpyxl: extra formulas, charts,
   conditional formatting, data validation. Never rebuild the whole sheet by
   hand, and never paste an image of a table.

Charts stay native: build them with openpyxl's chart API against the cell
ranges, not as a pasted matplotlib PNG.

## Analysis recipes (pandas)

```python
import pandas as pd
df = pd.read_excel("in.xlsx", sheet_name=None)      # dict of all sheets
df = pd.read_csv("in.csv", sep=None, engine="python")  # sniff delimiter

df.info(); df.describe(); df.isna().sum()           # profile first, always
pivot = df.pivot_table(index="region", columns="mois", values="ca", aggfunc="sum")
df["date"] = pd.to_datetime(df["date"], dayfirst=True)  # FR dates!
```

## Output recipes (formatted workbook)

```python
with pd.ExcelWriter("out.xlsx", engine="xlsxwriter") as xl:
    df.to_excel(xl, sheet_name="Data", index=False)
    wb, ws = xl.book, xl.sheets["Data"]
    money = wb.add_format({"num_format": "#,##0.00 €"})
    ws.set_column("C:C", 14, money)
    ws.autofilter(0, 0, len(df), len(df.columns)-1)
    ws.freeze_panes(1, 0)
    chart = wb.add_chart({"type": "column"})
    chart.add_series({"values": f"=Data!$C$2:$C${len(df)+1}"})
    ws.insert_chart("F2", chart)
```

Formulas that must stay live for the user: write with openpyxl (`ws["D2"] = "=B2*C2"`).

## Workflow

1. Profile the file: sheets, columns, types, nulls, duplicates - report anomalies before analyzing.
2. Clean explicitly (log every transformation: "removed 12 duplicate rows on ID").
3. Analyze per the question; sanity-check totals against the source.
4. Deliver: findings summary in chat + formatted workbook (Data / Analysis / README sheets).

## Rules

- Never overwrite the source file - output is always a new file.
- Watch FR conventions: decimal commas, dd/mm dates, spaces as thousands separators.
- >500k rows or joins across systems → consider `database-explorer`/`sql-analyst`.
