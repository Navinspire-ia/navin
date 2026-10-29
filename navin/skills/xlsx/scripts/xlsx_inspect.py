#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Inspect an Excel workbook and print a JSON summary.

Usage:
    xlsx_inspect.py <file.xlsx> [--sheet NAME] [--sample N] [--markdown]

Per sheet: dimensions, guessed header row, formulas count, merged cells,
cells holding error values (#REF!, #DIV/0! ...), sample rows. Workbook
level: named ranges, tables, data validations, conditional formats.

Exit codes: 0 ok (even when errors are found; see "error_cells"), 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

# Errors a malformed input or a bad argument can raise; reported as JSON with exit 2.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, InvalidFileException)

ERROR_VALUES = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!", "#SPILL!", "#CALC!")


def _is_error(value) -> bool:
    return isinstance(value, str) and value.strip().upper() in ERROR_VALUES


def _guess_header_row(ws, max_scan: int = 10) -> int | None:
    """First row where most non-empty cells are strings and the next row has data."""
    best = None
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, max_scan)):
        values = [c.value for c in row if c.value is not None]
        if not values:
            continue
        strings = sum(1 for v in values if isinstance(v, str) and not str(v).startswith("="))
        if strings >= max(1, int(0.6 * len(values))):
            best = row[0].row
            break
    return best


def inspect_sheet(ws_formulas, ws_values, sample: int) -> dict:
    formulas = 0
    error_cells = []
    non_empty = 0
    for row_f, row_v in zip(ws_formulas.iter_rows(), ws_values.iter_rows()):
        for cf, cv in zip(row_f, row_v):
            if cf.value is None:
                continue
            non_empty += 1
            if isinstance(cf.value, str) and cf.value.startswith("="):
                formulas += 1
            if _is_error(cv.value) or _is_error(cf.value):
                error_cells.append(
                    {"cell": cf.coordinate, "value": cv.value if cv.value is not None else cf.value, "formula": cf.value if isinstance(cf.value, str) and cf.value.startswith("=") else None}
                )
    header_row = _guess_header_row(ws_formulas)
    headers = []
    if header_row:
        headers = [c.value for c in ws_formulas[header_row]]
    rows = []
    first_data = (header_row or 0) + 1
    for row in ws_values.iter_rows(min_row=first_data, max_row=min(ws_values.max_row, first_data + sample - 1), values_only=True):
        rows.append([_json_safe(v) for v in row])
    return {
        "name": ws_formulas.title,
        "state": ws_formulas.sheet_state,
        "dimensions": ws_formulas.dimensions,
        "max_row": ws_formulas.max_row,
        "max_col": ws_formulas.max_column,
        "non_empty_cells": non_empty,
        "formulas": formulas,
        "header_row": header_row,
        "headers": [_json_safe(h) for h in headers],
        "merged_cells": [str(r) for r in ws_formulas.merged_cells.ranges],
        "freeze_panes": ws_formulas.freeze_panes,
        "auto_filter": ws_formulas.auto_filter.ref,
        "tables": list(ws_formulas.tables.keys()),
        "data_validations": len(ws_formulas.data_validations.dataValidation),
        "conditional_formats": sum(len(r.rules) for r in ws_formulas.conditional_formatting),
        "error_cells": error_cells,
        "sample_rows": rows,
    }


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def inspect(path: Path, sheet: str | None, sample: int) -> dict:
    wb_f = load_workbook(str(path), data_only=False)
    wb_v = load_workbook(str(path), data_only=True)
    names = [sheet] if sheet else wb_f.sheetnames
    sheets = []
    for name in names:
        if name not in wb_f.sheetnames:
            raise KeyError(f"sheet {name!r} not found; available: {wb_f.sheetnames}")
        sheets.append(inspect_sheet(wb_f[name], wb_v[name], sample))
    defined = []
    for dn in wb_f.defined_names.values():
        defined.append({"name": dn.name, "refers_to": dn.attr_text})
    result = {
        "file": str(path),
        "sheetnames": wb_f.sheetnames,
        "active": wb_f.active.title if wb_f.active is not None else None,
        "named_ranges": defined,
        "total_formulas": sum(s["formulas"] for s in sheets),
        "total_error_cells": sum(len(s["error_cells"]) for s in sheets),
        "sheets": sheets,
    }
    wb_f.close()
    wb_v.close()
    return result


def to_markdown(report: dict) -> str:
    lines = [f"# {report['file']}", ""]
    lines.append(f"Sheets: {', '.join(report['sheetnames'])}  |  formulas: {report['total_formulas']}  |  error cells: {report['total_error_cells']}")
    if report["named_ranges"]:
        lines.append("Named ranges: " + ", ".join(f"{d['name']} = {d['refers_to']}" for d in report["named_ranges"]))
    for s in report["sheets"]:
        lines += ["", f"## {s['name']} ({s['dimensions']})", f"formulas {s['formulas']}, header row {s['header_row']}, merged {len(s['merged_cells'])}, errors {len(s['error_cells'])}"]
        if s["headers"]:
            lines.append("| " + " | ".join(str(h) for h in s["headers"]) + " |")
            lines.append("|" + "---|" * len(s["headers"]))
        for row in s["sample_rows"]:
            lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
        for err in s["error_cells"][:20]:
            lines.append(f"- {err['cell']}: {err['value']} {err['formula'] or ''}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xlsx_inspect.py", description="Summarize an .xlsx workbook.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--sheet", default=None, help="only this sheet")
    parser.add_argument("--sample", type=int, default=5, help="sample rows per sheet")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = inspect(args.file, args.sheet, args.sample)
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2
    print(to_markdown(report) if args.markdown else json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
