"""Convert an HTML spreadsheet master into a live, editable XLSX workbook.

The template library mocks spreadsheets as HTML: a ``.sheet`` section holding a
title and one or more tables. That mock is a design, not a deliverable - a
workbook is expected to hold real cells with real types, so a reader can sort
the column, change a rate and watch the total follow.

The converter reads the laid-out page in Chromium and rebuilds it with
openpyxl: every table cell becomes a cell, numbers become numbers with the
number format their formatting implies (currency, percent, thousands, dates),
styling becomes fills, fonts and borders, column widths follow the rendered
ones, and total rows and columns become ``SUM`` formulas whenever the sum
actually matches what the template displays.

Usage::

    python3 -m navin.documents.html2xlsx document.html -o budget.xlsx
    python3 -m navin.documents.html2xlsx document.html -o budget.xlsx --csv exports/
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Sequence

if __package__:
    from . import _dom
    from ._chromium import ConversionError, find_chromium, measure
    from ._fonts import office_font
else:  # copied as a standalone folder into a user workspace
    import _dom
    from _chromium import ConversionError, find_chromium, measure
    from _fonts import office_font

SHEET_WIDTH_PX = 1123  # A4 landscape at 96 dpi, the usual master size
SHEET_HEIGHT_PX = 794
PT_PER_PX = 0.75

_MEASURE_BODY = r"""
  const out = { sheets: [], lang: document.documentElement.lang || "" };

  const cellStyle = (el, cs) => {
    const fill = color(cs.backgroundColor);
    const paint = color(cs.color);
    const weight = parseInt(cs.fontWeight, 10) || 400;
    const sides = {};
    for (const side of ["Top", "Right", "Bottom", "Left"]) {
      const width = parseFloat(cs[`border${side}Width`]) || 0;
      const paintSide = color(cs[`border${side}Color`]);
      const style = cs[`border${side}Style`];
      if (width > 0.4 && paintSide && style !== "none" && style !== "hidden") {
        sides[side.toLowerCase()] = { width: round(width), color: paintSide.hex };
      }
    }
    return {
      font: fontFamily(cs.fontFamily),
      families: fontStack(cs.fontFamily),
      size: round(parseFloat(cs.fontSize) || 14),
      bold: weight >= 600,
      italic: cs.fontStyle === "italic",
      color: paint ? paint.hex : "000000",
      fill: fill && fill.alpha > 0.15 ? fill.hex : null,
      align: cs.textAlign,
      valign: cs.verticalAlign,
      wrap: /^(normal|pre-wrap|pre-line)$/.test(cs.whiteSpace),
      borders: Object.keys(sides).length ? sides : null,
    };
  };

  // A cell often wraps its text in a colored badge; that badge carries the
  // meaning, so its own fill and color win over the neutral cell around it.
  const badge = (el) => {
    const kids = Array.from(el.children).filter((kid) => hasText(kid));
    if (kids.length !== 1) return null;
    const kid = kids[0];
    if ((kid.textContent || "").trim() !== (el.textContent || "").trim()) return null;
    const cs = getComputedStyle(kid);
    const fill = color(cs.backgroundColor);
    return fill && fill.alpha > 0.15 ? cellStyle(kid, cs) : null;
  };

  const cellOf = (el) => {
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const style = badge(el) || cellStyle(el, cs);
    return {
      text: transformText((el.textContent || "").replace(/\s+/g, " ").trim(), cs.textTransform),
      header: el.tagName === "TH",
      colspan: parseInt(el.getAttribute("colspan") || "1", 10) || 1,
      rowspan: parseInt(el.getAttribute("rowspan") || "1", 10) || 1,
      width: round(rect.width),
      style,
    };
  };

  const tableOf = (table) => {
    const rows = [];
    for (const tr of Array.from(table.querySelectorAll("tr"))) {
      const cs = getComputedStyle(tr);
      const rect = tr.getBoundingClientRect();
      if (!visible(cs, rect)) continue;
      const cells = Array.from(tr.children)
        .filter((c) => c.tagName === "TD" || c.tagName === "TH")
        .map(cellOf);
      if (cells.length) {
        rows.push({
          cells,
          head: !!tr.closest("thead") || cells.every((c) => c.header),
          height: round(rect.height),
        });
      }
    }
    if (!rows.length) return null;
    return { kind: "table", rows, top: round(table.getBoundingClientRect().top) };
  };

  const walk = (el, blocks, seen) => {
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (SKIP_TAGS.has(el.tagName) || !visible(cs, rect)) return;
    if (el.tagName === "TABLE") {
      const table = tableOf(el);
      if (table) blocks.push(table);
      return;
    }
    const blockKids = Array.from(el.children).filter(
      (kid) => !SKIP_TAGS.has(kid.tagName) && isBlockish(kid) && hasText(kid),
    );
    if (blockKids.length || el.querySelector("table")) {
      // A row of KPI cards belongs side by side in the sheet, not stacked in
      // column A.
      const columns = columnsOf(el, cs);
      if (columns > 1 && !el.querySelector("table")) {
        const cells = blockKids.map((kid) => {
          const inner = [];
          walk(kid, inner, seen);
          return inner.filter((block) => block.kind === "text");
        });
        if (cells.some((cell) => cell.length)) {
          blocks.push({ kind: "grid", cells, top: round(rect.top) });
          return;
        }
      }
      for (const kid of Array.from(el.children)) walk(kid, blocks, seen);
      return;
    }
    if (!hasText(el)) return;
    const heading = /^H([1-6])$/.exec(el.tagName);
    blocks.push({
      kind: "text",
      text: transformText((el.textContent || "").replace(/\s+/g, " ").trim(), cs.textTransform),
      level: heading ? parseInt(heading[1], 10) : 0,
      style: cellStyle(el, cs),
      top: round(rect.top),
    });
  };

  for (const root of pageRoots([".sheet", ".page", "body > section", "body > main"])) {
    const blocks = [];
    for (const kid of Array.from(root.children)) walk(kid, blocks, new Set());
    if (!blocks.length) continue;
    const heading = root.querySelector("h1, h2, .title");
    out.sheets.push({
      name: heading ? (heading.textContent || "").trim() : "",
      blocks,
    });
  }
  publish(out);
"""

_TOTAL_WORDS = (
    "total", "totaux", "totale", "totali", "sum", "somme", "sous-total", "subtotal",
    "sub-total", "grand total", "gesamt", "summe", "suma", "totaal", "合計", "المجموع",
)
_CURRENCIES = {"€": "€", "$": "$", "£": "£", "¥": "¥", "CHF": "CHF", "MAD": "MAD", "DH": "DH"}
_NUMBER_RE = re.compile(r"^[-+(]?\s*[\d][\d\s.,\u00a0']*\s*\)?$")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})$")


@dataclass
class SheetData:
    """One measured sheet: an ordered list of text and table blocks."""

    name: str
    blocks: list[dict[str, Any]] = field(default_factory=list)


def measure_workbook(source: Path, chromium: str, workdir: Path, timeout: int) -> tuple[
    list[SheetData], str
]:
    """Measure an HTML spreadsheet master and return its sheets."""
    payload = measure(
        source,
        _dom.script(_MEASURE_BODY, SHEET_WIDTH_PX, 0),
        chromium=chromium,
        workdir=workdir,
        timeout=timeout,
        viewport=(SHEET_WIDTH_PX, SHEET_HEIGHT_PX * 3),
        prefix="sheet",
    )
    sheets = [
        SheetData(name=str(sheet.get("name") or ""), blocks=sheet.get("blocks") or [])
        for sheet in payload.get("sheets", [])
        if sheet.get("blocks")
    ]
    if not sheets:
        raise ConversionError(f"{source.name}: no table or content found")
    return sheets, str(payload.get("lang") or "")


def _clean_number(text: str) -> tuple[float, bool] | None:
    """Parse a displayed number, handling both decimal conventions."""
    raw = text.strip()
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1].strip()
    if not _NUMBER_RE.match(raw):
        return None
    body = raw.replace("\u00a0", "").replace(" ", "").replace("'", "").lstrip("+")
    if body.startswith("-"):
        negative = True
        body = body[1:]
    if "," in body and "." in body:
        # Whichever separator comes last is the decimal one.
        decimal = "," if body.rfind(",") > body.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        body = body.replace(thousands, "").replace(decimal, ".")
    elif "," in body:
        parts = body.split(",")
        body = body.replace(",", "." if len(parts) == 2 and len(parts[1]) != 3 else "")
    elif body.count(".") > 1:
        body = body.replace(".", "")
    try:
        value = float(body)
    except ValueError:
        return None
    return (-value if negative else value), "." in body


def parse_value(text: str, day_first: bool | None) -> tuple[Any, str | None]:
    """Turn displayed text into a typed value and its Excel number format."""
    raw = text.strip()
    if not raw:
        return "", None
    iso = _ISO_DATE_RE.match(raw)
    if iso:
        year, month, day = (int(part) for part in iso.groups())
        try:
            return date(year, month, day), "yyyy-mm-dd"
        except ValueError:
            return raw, None
    slash = _SLASH_DATE_RE.match(raw)
    if slash and day_first is not None:
        first, second, year_raw = (int(part) for part in slash.groups())
        day, month = (first, second) if day_first else (second, first)
        year = year_raw + 2000 if year_raw < 100 else year_raw
        try:
            return date(year, month, day), "dd/mm/yyyy" if day_first else "mm/dd/yyyy"
        except ValueError:
            return raw, None
    if raw.endswith("%"):
        parsed = _clean_number(raw[:-1])
        if parsed:
            value, decimals = parsed
            return value / 100, "0.0%" if decimals else "0%"
    symbol = next((sym for sym in _CURRENCIES if sym in raw), None)
    if symbol:
        parsed = _clean_number(raw.replace(symbol, "").strip())
        if parsed:
            value, decimals = parsed
            digits = "0.00" if decimals else "0"
            money = f'#,##{digits}\\ "{symbol}"' if raw.strip().endswith(symbol) else (
                f'"{symbol}"\\ #,##{digits}'
            )
            return value, money
    parsed = _clean_number(raw)
    if parsed:
        value, decimals = parsed
        if abs(value) >= 1000 or decimals:
            return value, "#,##0.00" if decimals else "#,##0"
        return value, None
    return raw, None


def _day_first(rows: Sequence[dict[str, Any]], lang: str) -> bool | None:
    """Decide the day/month order of ambiguous dates for the whole sheet."""
    for row in rows:
        for cell in row.get("cells", []):
            match = _SLASH_DATE_RE.match(str(cell.get("text", "")).strip())
            if match:
                first, second = int(match.group(1)), int(match.group(2))
                if first > 12 and second <= 12:
                    return True
                if second > 12 and first <= 12:
                    return False
    code = (lang or "").lower()
    if code.startswith("en") and not code.startswith("en-gb"):
        return None if code in {"en", "und", ""} else False
    return True if code and not code.startswith("und") else None


def _is_total_label(text: str) -> bool:
    lowered = text.strip().lower()
    return any(word in lowered for word in _TOTAL_WORDS)


def _column_letter(index: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(index)


def _grid(rows: Sequence[dict[str, Any]]) -> tuple[list[list[dict[str, Any] | None]], list[tuple]]:
    """Expand a HTML table into a rectangular grid plus its merge ranges."""
    grid: list[list[dict[str, Any] | None]] = []
    merges: list[tuple[int, int, int, int]] = []
    occupied: dict[tuple[int, int], bool] = {}
    for row_index, row in enumerate(rows):
        while len(grid) <= row_index:
            grid.append([])
        column = 0
        for cell in row.get("cells", []):
            while occupied.get((row_index, column)):
                column += 1
            span_x = max(1, int(cell.get("colspan", 1)))
            span_y = max(1, int(cell.get("rowspan", 1)))
            while len(grid[row_index]) <= column:
                grid[row_index].append(None)
            grid[row_index][column] = cell
            for dy in range(span_y):
                for dx in range(span_x):
                    if dy or dx:
                        occupied[(row_index + dy, column + dx)] = True
            if span_x > 1 or span_y > 1:
                merges.append((row_index, column, row_index + span_y - 1, column + span_x - 1))
            column += span_x
    width = max((len(row) for row in grid), default=0)
    for row in grid:
        row.extend([None] * (width - len(row)))
    return grid, merges


def _style_cell(cell, style: dict[str, Any], keep_fonts: bool) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    name = office_font(style.get("families") or [], style.get("font") or "Calibri", keep_fonts)
    cell.font = Font(
        name=name,
        size=max(6, round(float(style.get("size") or 14) * PT_PER_PX, 1)),
        bold=bool(style.get("bold")),
        italic=bool(style.get("italic")),
        color=f"FF{style.get('color') or '000000'}",
    )
    if style.get("fill"):
        cell.fill = PatternFill("solid", fgColor=f"FF{style['fill']}")
    horizontal = {"start": "left", "end": "right", "justify": "justify"}.get(
        str(style.get("align") or ""), str(style.get("align") or "")
    )
    vertical = {"middle": "center", "baseline": "bottom"}.get(
        str(style.get("valign") or ""), str(style.get("valign") or "")
    )
    cell.alignment = Alignment(
        horizontal=horizontal if horizontal in {"left", "center", "right", "justify"} else None,
        vertical=vertical if vertical in {"top", "center", "bottom"} else "center",
        wrap_text=bool(style.get("wrap")),
    )
    borders = style.get("borders")
    if borders:
        sides = {}
        for side, spec in borders.items():
            weight = "thin" if float(spec.get("width") or 1) < 2 else "medium"
            sides[side] = Side(style=weight, color=f"FF{spec.get('color') or '999999'}")
        cell.border = Border(**sides)


def _write_table(
    sheet, block: dict[str, Any], row_cursor: int, lang: str, keep_fonts: bool, formulas: bool
) -> tuple[int, int]:
    """Write one table and return the next free row and its column count."""
    rows = block.get("rows") or []
    grid, merges = _grid(rows)
    if not grid:
        return row_cursor, 0
    day_first = _day_first(rows, lang)
    columns = len(grid[0])
    values: dict[tuple[int, int], float] = {}
    first_row = row_cursor

    for y, row in enumerate(grid):
        excel_row = first_row + y
        height = float(rows[y].get("height") or 0) if y < len(rows) else 0
        if height:
            sheet.row_dimensions[excel_row].height = max(14, round(height * PT_PER_PX, 1))
        for x, data in enumerate(row):
            if data is None:
                continue
            cell = sheet.cell(row=excel_row, column=x + 1)
            text = str(data.get("text") or "")
            if data.get("header"):
                cell.value = text
            else:
                value, number_format = parse_value(text, day_first)
                cell.value = value
                if number_format:
                    cell.number_format = number_format
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[(y, x)] = float(value)
            _style_cell(cell, data.get("style") or {}, keep_fonts)

    if formulas:
        _apply_sums(sheet, grid, values, first_row)
    for top, left, bottom, right in merges:
        sheet.merge_cells(
            start_row=first_row + top, start_column=left + 1,
            end_row=first_row + bottom, end_column=right + 1,
        )
    header_rows = sum(1 for row in rows if row.get("head"))
    if header_rows:
        sheet.freeze_panes = sheet.cell(row=first_row + header_rows, column=1)
        sheet.auto_filter.ref = (
            f"A{first_row + header_rows - 1}:"
            f"{_column_letter(columns)}{first_row + len(grid) - 1}"
        )
    for x in range(columns):
        widths = [
            float(row[x].get("width") or 0)
            for row in grid
            if row[x] and int(row[x].get("colspan", 1)) == 1
        ]
        if widths:
            letter = _column_letter(x + 1)
            current = sheet.column_dimensions[letter].width or 0
            # Excel widths count characters, roughly 7px each.
            sheet.column_dimensions[letter].width = max(current, round(max(widths) / 7.0, 1))
    return first_row + len(grid), columns


def _apply_sums(sheet, grid, values: dict[tuple[int, int], float], first_row: int) -> None:
    """Replace total cells with SUM formulas when the sum checks out."""
    height = len(grid)
    width = len(grid[0]) if height else 0

    def contiguous_above(y: int, x: int) -> tuple[int, int] | None:
        start = y - 1
        while start >= 0 and (start, x) in values:
            start -= 1
        return (start + 1, y - 1) if y - 1 - start >= 2 else None

    def contiguous_left(y: int, x: int) -> tuple[int, int] | None:
        start = x - 1
        while start >= 0 and (y, start) in values:
            start -= 1
        return (start + 1, x - 1) if x - 1 - start >= 2 else None

    total_rows = {
        y for y in range(height)
        if any(grid[y][x] and _is_total_label(str(grid[y][x].get("text", ""))) for x in range(min(2, width)))
    }
    total_columns = {
        x for x in range(width)
        if grid[0][x] and _is_total_label(str(grid[0][x].get("text", "")))
    }
    for (y, x), value in values.items():
        span = None
        if y in total_rows:
            span = contiguous_above(y, x)
            if span:
                total = sum(values[(row, x)] for row in range(span[0], span[1] + 1))
                if abs(total - value) > max(0.02, abs(value) * 0.01):
                    span = None
                else:
                    letter = _column_letter(x + 1)
                    formula = f"=SUM({letter}{first_row + span[0]}:{letter}{first_row + span[1]})"
        if span is None and x in total_columns:
            span = contiguous_left(y, x)
            if span:
                total = sum(values[(y, col)] for col in range(span[0], span[1] + 1))
                if abs(total - value) > max(0.02, abs(value) * 0.01):
                    span = None
                else:
                    row_number = first_row + y
                    formula = (
                        f"=SUM({_column_letter(span[0] + 1)}{row_number}:"
                        f"{_column_letter(span[1] + 1)}{row_number})"
                    )
        if span is None:
            continue
        cell = sheet.cell(row=first_row + y, column=x + 1)
        number_format = cell.number_format
        cell.value = formula
        cell.number_format = number_format


def _write_free_cell(sheet, row: int, column: int, block: dict[str, Any], keep: bool) -> bool:
    """Write a block living outside any table, typed like a table cell would be."""
    text = str(block.get("text") or "").strip()
    if not text:
        return False
    cell = sheet.cell(row=row, column=column)
    value, number_format = parse_value(text, None)
    cell.value = value
    if number_format:
        cell.number_format = number_format
    _style_cell(cell, block.get("style") or {}, keep)
    return True


def _safe_sheet_name(name: str, used: set[str], index: int) -> str:
    cleaned = re.sub(r"[\\/*?:\[\]]", " ", name).strip() or f"Sheet{index}"
    cleaned = cleaned[:31]
    candidate, suffix = cleaned, 2
    while candidate.lower() in used:
        candidate = f"{cleaned[:28]} {suffix}"
        suffix += 1
    used.add(candidate.lower())
    return candidate


def build_workbook(
    sheets: Sequence[SheetData],
    output: Path,
    lang: str = "",
    keep_fonts: bool = False,
    formulas: bool = True,
) -> Path:
    """Assemble measured sheets into a live XLSX workbook."""
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    used: set[str] = set()
    for index, data in enumerate(sheets, start=1):
        sheet = workbook.create_sheet(_safe_sheet_name(data.name, used, index))
        cursor = 1
        widest = 1
        for block in data.blocks:
            if block.get("kind") == "table":
                cursor, columns = _write_table(sheet, block, cursor, lang, keep_fonts, formulas)
                widest = max(widest, columns)
                cursor += 1
                continue
            if block.get("kind") == "grid":
                cells = block.get("cells") or []
                for column, lines in enumerate(cells, start=1):
                    for offset, line in enumerate(lines):
                        _write_free_cell(sheet, cursor + offset, column, line, keep_fonts)
                widest = max(widest, len(cells))
                cursor += max((len(lines) for lines in cells), default=0) + 1
                continue
            if _write_free_cell(sheet, cursor, 1, block, keep_fonts):
                cursor += 1
        sheet.sheet_view.showGridLines = False
        sheet.page_setup.orientation = "landscape"
        sheet.print_options.horizontalCentered = True
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(output))
    return output


def export_csv(workbook_path: Path, folder: Path) -> list[Path]:
    """Write one UTF-8 CSV per sheet, values as displayed by the workbook."""
    from openpyxl import load_workbook

    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    workbook = load_workbook(str(workbook_path), data_only=False)
    for sheet in workbook.worksheets:
        target = folder / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', sheet.title).strip('_') or 'sheet'}.csv"
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            for row in sheet.iter_rows(values_only=True):
                if all(value is None or value == "" for value in row):
                    continue
                writer.writerow(["" if value is None else value for value in row])
        written.append(target)
    return written


def convert(
    source: str,
    output: Path,
    *,
    chromium: str | None = None,
    timeout: int = 90,
    keep_workdir: Path | None = None,
    keep_fonts: bool = False,
    formulas: bool = True,
    csv_dir: Path | None = None,
) -> Path:
    """Convert an HTML spreadsheet master into a live workbook."""
    page = Path(source).expanduser()
    if page.is_dir():
        candidates = sorted(page.glob("document.html")) or sorted(page.glob("*.html"))
        if not candidates:
            raise ConversionError(f"No HTML document in {page}")
        page = candidates[0]
    if not page.is_file():
        raise ConversionError(f"Not found: {source}")
    binary = find_chromium(chromium)
    workdir = keep_workdir or Path(tempfile.mkdtemp(prefix="navin-html2xlsx-"))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        sheets, lang = measure_workbook(page, binary, workdir, timeout)
        result = build_workbook(sheets, output, lang, keep_fonts=keep_fonts, formulas=formulas)
    finally:
        if keep_workdir is None:
            shutil.rmtree(workdir, ignore_errors=True)
    if csv_dir:
        export_csv(result, csv_dir)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="html2xlsx",
        description="Convert an HTML spreadsheet master into a live XLSX workbook.",
    )
    parser.add_argument("input", help="document.html, or the folder holding it")
    parser.add_argument("-o", "--output", required=True, help="Destination .xlsx")
    parser.add_argument("--csv", help="Also export one CSV per sheet into this folder")
    parser.add_argument("--chromium", help="Path to a Chromium binary")
    parser.add_argument("--timeout", type=int, default=90, help="Chromium timeout")
    parser.add_argument("--keep-workdir", help="Keep intermediate files in this folder")
    parser.add_argument("--keep-fonts", action="store_true", help="Keep the CSS font names")
    parser.add_argument(
        "--no-formulas",
        action="store_true",
        help="Write total rows as values instead of SUM formulas",
    )
    args = parser.parse_args(argv)
    try:
        output = convert(
            args.input,
            Path(args.output).expanduser(),
            chromium=args.chromium,
            timeout=args.timeout,
            keep_workdir=Path(args.keep_workdir).expanduser() if args.keep_workdir else None,
            keep_fonts=args.keep_fonts,
            formulas=not args.no_formulas,
            csv_dir=Path(args.csv).expanduser() if args.csv else None,
        )
    except ConversionError as exc:
        print(f"html2xlsx: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
