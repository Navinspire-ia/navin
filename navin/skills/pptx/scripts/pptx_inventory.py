#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Inventory every slide and shape of a PowerPoint deck as JSON.

Usage:
    pptx_inventory.py <file.pptx> [--slide N] [--markdown]

Per slide: index (1-based), layout name, notes, and every shape with id, name,
shape type, placeholder type, position/size in inches, text, image presence,
table size and chart type.

Exit codes: 0 ok, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from pptx import Presentation
from pptx.exc import PythonPptxError
from pptx.util import Emu

# Errors a malformed input or a bad argument can raise; reported as JSON with exit 2.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, PythonPptxError)


def _inches(value) -> float | None:
    return round(Emu(value).inches, 2) if value is not None else None


def _shape_record(shape) -> dict:
    rec: dict = {
        "id": shape.shape_id,
        "name": shape.name,
        "type": str(shape.shape_type).split(" ")[0] if shape.shape_type is not None else "UNKNOWN",
        "placeholder": None,
        "left": _inches(shape.left),
        "top": _inches(shape.top),
        "width": _inches(shape.width),
        "height": _inches(shape.height),
        "has_text": bool(getattr(shape, "has_text_frame", False)),
        "text": None,
        "image": False,
    }
    if shape.is_placeholder:
        rec["placeholder"] = str(shape.placeholder_format.type).split(" ")[0]
        rec["placeholder_idx"] = shape.placeholder_format.idx
    if rec["has_text"]:
        rec["text"] = shape.text_frame.text
        rec["paragraphs"] = len(shape.text_frame.paragraphs)
    if shape.shape_type is not None and "PICTURE" in str(shape.shape_type):
        rec["image"] = True
        # Linked pictures have no embedded blob: python-pptx raises ValueError there.
        try:
            rec["image_type"] = shape.image.content_type
        except ValueError:
            rec["image_type"] = "linked"
    if getattr(shape, "has_table", False) and shape.has_table:
        table = shape.table
        rec["table"] = {"rows": len(table.rows), "cols": len(table.columns), "header": [c.text for c in table.rows[0].cells]}
    if getattr(shape, "has_chart", False) and shape.has_chart:
        rec["chart"] = str(shape.chart.chart_type).split(" ")[0]
    if shape.shape_type is not None and "GROUP" in str(shape.shape_type):
        rec["children"] = [_shape_record(child) for child in shape.shapes]
    return rec


def inventory(path: Path, only_slide: int | None = None) -> dict:
    prs = Presentation(str(path))
    slides = []
    for index, slide in enumerate(prs.slides, start=1):
        if only_slide and index != only_slide:
            continue
        notes = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
        slides.append(
            {
                "index": index,
                "slide_id": slide.slide_id,
                "layout": slide.slide_layout.name,
                "notes": notes,
                "shapes": [_shape_record(s) for s in slide.shapes],
            }
        )
    return {
        "file": str(path),
        "slide_width_in": _inches(prs.slide_width),
        "slide_height_in": _inches(prs.slide_height),
        "slide_count": len(prs.slides),
        "layouts": [layout.name for layout in prs.slide_layouts],
        "slides": slides,
    }


def to_markdown(report: dict) -> str:
    lines = [f"# {report['file']}  ({report['slide_count']} slides, {report['slide_width_in']}x{report['slide_height_in']} in)"]
    for slide in report["slides"]:
        lines += ["", f"## Slide {slide['index']} - {slide['layout']}"]
        for shape in slide["shapes"]:
            text = (shape.get("text") or "").replace("\n", " / ")
            extra = " [image]" if shape["image"] else ""
            if "table" in shape:
                extra += f" [table {shape['table']['rows']}x{shape['table']['cols']}]"
            if "chart" in shape:
                extra += f" [chart {shape['chart']}]"
            lines.append(f"- #{shape['id']} {shape['name']} ({shape['type']}{', ' + shape['placeholder'] if shape['placeholder'] else ''}){extra}: {text[:120]}")
        if slide["notes"]:
            lines.append(f"  notes: {slide['notes'][:200]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pptx_inventory.py", description="List slides and shapes of a .pptx.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--slide", type=int, default=None, help="1-based slide number")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = inventory(args.file, args.slide)
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2
    print(to_markdown(report) if args.markdown else json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
