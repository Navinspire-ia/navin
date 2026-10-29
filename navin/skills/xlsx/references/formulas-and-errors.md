# Formulas, errors and recipes for .xlsx work

## Error values and how to fix them

| Error | Meaning | Usual cause | Fix |
|-------|---------|-------------|-----|
| `#REF!` | A reference points to a deleted cell, row, column or sheet | Rows deleted after the formula was written, a sheet renamed or removed, a range copied outside the grid | Rebuild the reference; use named ranges or structured references so deletions do not break formulas |
| `#DIV/0!` | Division by zero or by an empty cell | Denominator not filled yet, a count of zero | Guard: `=IF(B2=0,"",A2/B2)` or `=IFERROR(A2/B2,0)` only when zero is a meaningful result |
| `#VALUE!` | Wrong type in an operation | Text stored where a number is expected, a date typed as text, a space in a numeric cell | Convert with `VALUE()`, clean the inputs, set the cell type to number |
| `#NAME?` | Unknown function or name | Typo in a function, a named range that does not exist, a function unavailable in the application that recalculates (for example newer dynamic array functions in LibreOffice) | Fix the name; prefer functions both Excel and LibreOffice support (`SUMIFS`, `INDEX/MATCH`, `IFERROR`, `TEXT`) |
| `#N/A` | Lookup found nothing | `VLOOKUP`, `XLOOKUP`, `MATCH` without a match | Wrap in `IFERROR(..., "")` or `IFNA`; check trailing spaces and types on both sides |
| `#NUM!` | Invalid numeric result | Square root of a negative number, iteration that did not converge, a huge power | Guard the input; check the sign |
| `#NULL!` | Intersection of ranges that do not intersect | A space between two ranges instead of a comma | Replace the space with `,` (or `;` in European locales) |
| `#SPILL!`, `#CALC!` | Dynamic array could not spill | Cells in the way of the spilled result | Clear the blocking cells or use a classic formula |

Scan for these values with `xlsx_inspect.py` (literal values already stored) or `xlsx_recalc.py` (after recalculation).

## Recalculation: openpyxl versus LibreOffice

- openpyxl writes the formula string into the cell and clears the cached value. Reading the same file with `load_workbook(data_only=True)` then returns `None` for every formula cell: the workbook has not been computed yet.
- Excel recalculates on open. LibreOffice recalculates on open only when told to, which is why `xlsx_recalc.py` re-saves the file headless: the saved copy carries fresh cached values that `data_only=True` can read.
- Volatile functions (`NOW`, `TODAY`, `RAND`) differ between runs. Avoid them in delivered models; store the reporting date in an input cell.
- Locale: formulas in the file are always stored in English with `,` separators regardless of the UI locale. Write `=SUM(A1,B1)`, never `=SOMME(A1;B1)`.
- Shared formulas: Excel stores a formula once and marks the following cells as shared. openpyxl expands them on read; do not expect the raw XML to show one formula per cell.
- Array formulas: openpyxl supports `ArrayFormula("A1:A3", "=...")`; LibreOffice handles classic CSE arrays, not every dynamic array function.

## Named ranges

```python
from openpyxl.workbook.defined_name import DefinedName
wb.defined_names["Growth"] = DefinedName("Growth", attr_text="Assumptions!$B$3")
ws["C2"] = "=B2*(1+Growth)"
```

Names make assumptions editable in one place and make formulas readable. Use workbook-level names for inputs and sheet-level names for helper ranges.

## Tables (ListObjects)

```python
from openpyxl.worksheet.table import Table, TableStyleInfo
table = Table(displayName="Sales", ref="A1:F200")
table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
ws.add_table(table)
```

- The header row must contain unique, non-empty strings.
- Structured references (`=SUM(Sales[Amount])`) survive inserted rows.
- Do not overlap tables, merged cells or autofilters with a table range.

## Data validation

```python
from openpyxl.worksheet.datavalidation import DataValidation
dv = DataValidation(type="list", formula1='"Open,Won,Lost"', allow_blank=True)
dv.error = "Pick a value from the list"
ws.add_data_validation(dv)
dv.add("E2:E500")
```

For long lists point `formula1` at a named range (`=StatusList`). Whole-number and date validations use `type="whole"` / `type="date"` with `operator="between"`.

## Conditional formatting recipes

```python
from openpyxl.formatting.rule import CellIsRule, FormulaRule, ColorScaleRule
from openpyxl.styles import Font, PatternFill

# negative numbers in Coral
ws.conditional_formatting.add("D2:D500", CellIsRule(operator="lessThan", formula=["0"], font=Font(color="ff5f57")))
# flag a check cell that is not zero
ws.conditional_formatting.add("H2:H500", FormulaRule(formula=["ABS(H2)>0.01"], fill=PatternFill("solid", fgColor="ff5f57"), font=Font(color="f4f3ee")))
# heat map from Paper to Teal
ws.conditional_formatting.add("B2:G13", ColorScaleRule(start_type="min", start_color="f4f3ee", end_type="max", end_color="0e7490"))
```

## Financial model conventions

- One `Assumptions` sheet with every input, each labelled, each colored Teal; the rest of the model references them.
- Time runs across columns, line items down rows; one formula per row copied across, never a different formula in the middle of a row.
- Check rows (balance sheet balances, sources equal uses) end with `=ROUND(a-b, 2)` and are colored Coral when not zero.
- Units in the header (`EUR k`, `%`, `months`), never mixed within a column.
- Keep sign conventions explicit: costs negative or costs positive, stated once in the assumptions.

## Number formats worth remembering

| Purpose | Format code |
|---------|-------------|
| Thousands with two decimals | `#,##0.00` |
| Negative in parentheses | `#,##0;(#,##0)` |
| Percent with one decimal | `0.0%` |
| Currency, euro after the number | `#,##0.00 "EUR"` |
| ISO date | `yyyy-mm-dd` |
| Month and year | `mmm yyyy` |
| Hide zero | `#,##0;-#,##0;""` |

## Quick diagnostics

- Formula count zero on a sheet that should compute: the author pasted values; rebuild the formulas.
- Many `#N/A` in one column: the lookup key has a different type (number versus text) or trailing spaces on one side.
- `#REF!` in every row of a column after a sheet rename: run a search-and-replace on the old sheet name in the formulas.
- `#NAME?` only after LibreOffice recalculation: the function exists in Excel but not in LibreOffice; replace it or deliver without recalculation and say so.
