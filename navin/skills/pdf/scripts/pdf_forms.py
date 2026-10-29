#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""List and fill AcroForm fields of a PDF.

Commands:
    fields <file.pdf> [--json]
        every field with its qualified name, type, current value, options
        (choice lists, checkbox export values) and page number
    fill   <file.pdf> --data values.json --out filled.pdf [--flatten]
        values.json maps field name -> value (string, number, bool for
        checkboxes). --flatten bakes the appearance into page content with
        PyMuPDF so the result is no longer editable.

Exit codes: 0 ok, 1 no form fields / unknown field names, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

# Errors a malformed PDF, a bad mapping or the PyMuPDF flattening step can raise.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError, PyPdfError)

FIELD_TYPES = {"/Tx": "text", "/Btn": "button", "/Ch": "choice", "/Sig": "signature"}


def list_fields(path: Path) -> list[dict]:
    reader = PdfReader(str(path))
    fields = reader.get_fields() or {}
    rows = []
    # collect widget annotations per field to find pages and checkbox export values
    widgets: dict[str, list] = {}
    for p_index, page in enumerate(reader.pages, start=1):
        for annot_ref in page.get("/Annots") or []:
            annot = annot_ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            name = annot.get("/T")
            parent = annot.get("/Parent")
            while name is None and parent is not None:
                parent = parent.get_object()
                name = parent.get("/T")
                parent = parent.get("/Parent")
            if name is not None:
                widgets.setdefault(str(name), []).append((p_index, annot))
    for name, field in fields.items():
        ftype = FIELD_TYPES.get(str(field.get("/FT")), str(field.get("/FT")))
        flags = int(field.get("/Ff", 0) or 0)
        if ftype == "button":
            ftype = "radio" if flags & (1 << 15) else ("pushbutton" if flags & (1 << 16) else "checkbox")
        if ftype == "choice":
            ftype = "combobox" if flags & (1 << 17) else "listbox"
        value = field.get("/V")
        options = []
        if "/Opt" in field:
            for opt in field["/Opt"]:
                options.append([str(o) for o in opt] if isinstance(opt, list) else str(opt))
        short = name.split(".")[-1]
        page_no = None
        for key in (name, short):
            if key in widgets:
                page_no = widgets[key][0][0]
                if ftype in ("checkbox", "radio"):
                    for _, annot in widgets[key]:
                        ap = annot.get("/AP")
                        if ap is not None and "/N" in ap:
                            for state in ap["/N"].keys():
                                if state != "/Off" and str(state) not in options:
                                    options.append(str(state))
                break
        rows.append(
            {
                "name": name,
                "type": ftype,
                "value": None if value is None else str(value),
                "options": options,
                "page": page_no,
                "required": bool(flags & 2),
                "readonly": bool(flags & 1),
            }
        )
    return rows


def fill(path: Path, data: dict, out: Path, flatten: bool) -> dict:
    reader = PdfReader(str(path))
    known = reader.get_fields() or {}
    if not known:
        return {"ok": False, "error": "the PDF has no AcroForm fields; use pdf_overlay.py instead"}
    unknown = [k for k in data if k not in known and k not in {n.split('.')[-1] for n in known}]
    writer = PdfWriter()
    writer.append(reader)
    # normalise checkbox booleans to their export value
    normalized: dict[str, object] = {}
    export_values = {row["name"]: row["options"] for row in list_fields(path)}
    for name, value in data.items():
        if name in unknown:
            continue
        if isinstance(value, bool):
            opts = export_values.get(name) or ["/Yes"]
            normalized[name] = opts[0] if value else "/Off"
        else:
            normalized[name] = value
    for page in writer.pages:
        writer.update_page_form_field_values(page, normalized, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        writer.write(fh)
    if flatten:
        _flatten(out)
    return {"ok": not unknown, "output": str(out), "filled": sorted(normalized), "unknown_fields": unknown, "flattened": flatten}


def _flatten(path: Path) -> None:
    """Bake widget appearances into the page content so nothing stays editable."""
    import pymupdf

    doc = pymupdf.open(str(path))
    for page in doc:
        for widget in page.widgets():
            widget.update()
    doc.bake(annots=True, widgets=True)
    tmp = path.with_suffix(".flat.tmp.pdf")
    doc.save(str(tmp), garbage=3, deflate=True)
    doc.close()
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdf_forms.py", description="List or fill PDF form fields.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_fields = sub.add_parser("fields", help="list form fields")
    p_fields.add_argument("file", type=Path)
    p_fields.add_argument("--json", action="store_true")
    p_fill = sub.add_parser("fill", help="fill fields from a JSON mapping")
    p_fill.add_argument("file", type=Path)
    p_fill.add_argument("--data", type=Path, required=True, help="JSON object field -> value")
    p_fill.add_argument("--out", type=Path, required=True)
    p_fill.add_argument("--flatten", action="store_true", help="make the result non-editable")
    args = parser.parse_args(argv)
    try:
        if args.command == "fields":
            rows = list_fields(args.file)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    opts = f" options={row['options']}" if row["options"] else ""
                    print(f"{row['name']} [{row['type']}] page={row['page']} value={row['value']!r}{opts}")
                if not rows:
                    print("no form fields (not an AcroForm PDF)")
            return 0 if rows else 1
        data = json.loads(args.data.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("--data must be a JSON object")
        result = fill(args.file, data, args.out, args.flatten)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
