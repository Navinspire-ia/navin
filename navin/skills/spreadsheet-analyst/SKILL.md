---
name: spreadsheet-analyst
description: Read, analyze, and produce Excel/CSV files — cleanup, pivots, formulas, charts, and formatted workbooks — using pandas and openpyxl. Use for any spreadsheet task.
metadata: {"navin":{"emoji":"📗","category":"documents"}}
---

# Spreadsheet Analyst

## Overview

Two jobs: (1) analyze data that arrives as xlsx/csv, (2) deliver clean, formatted workbooks. Tools: `exec` + `pip install pandas openpyxl xlsxwriter`.

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

1. Profile the file: sheets, columns, types, nulls, duplicates — report anomalies before analyzing.
2. Clean explicitly (log every transformation: "removed 12 duplicate rows on ID").
3. Analyze per the question; sanity-check totals against the source.
4. Deliver: findings summary in chat + formatted workbook (Data / Analysis / README sheets).

## Rules

- Never overwrite the source file — output is always a new file.
- Watch FR conventions: decimal commas, dd/mm dates, spaces as thousands separators.
- >500k rows or joins across systems → consider `database-explorer`/`sql-analyst`.
