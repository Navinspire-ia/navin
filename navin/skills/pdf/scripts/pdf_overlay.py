#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Overlay text on PDF pages at measured coordinates (for non-fillable forms).

Usage:
    pdf_overlay.py <file.pdf> --items items.json --out out.pdf [--font Helvetica] [--size 10]
    pdf_overlay.py <file.pdf> --text "Jane Doe" --x 120 --y 640 [--page 1] --out out.pdf
    pdf_overlay.py <file.pdf> --grid --out-dir DIR [--page 1] [--step 50] [--dpi 100]

items.json is a list: [{"page": 1, "x": 120, "y": 640, "text": "Jane Doe",
"size": 10, "color": "#17171a"}]. Coordinates are PDF points with the origin
at the bottom-left corner of the page. --grid renders the page as PNG with a
labelled coordinate grid so positions can be measured before writing.

Exit codes: 0 ok, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

# Errors a malformed PDF, a bad items list, a missing font or the grid renderer can raise.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError, PyPdfError)


def _group_by_page(items: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for item in items:
        grouped.setdefault(int(item.get("page", 1)), []).append(item)
    return grouped


def overlay(path: Path, items: list[dict], out: Path, font: str, size: float) -> dict:
    reader = PdfReader(str(path))
    writer = PdfWriter()
    by_page = _group_by_page(items)
    written = 0
    for number, page in enumerate(reader.pages, start=1):
        page_items = by_page.get(number)
        if page_items:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            buffer = io.BytesIO()
            c = canvas.Canvas(buffer, pagesize=(width, height))
            for item in page_items:
                c.setFont(item.get("font", font), float(item.get("size", size)))
                c.setFillColor(HexColor(item.get("color", "#17171a")))
                c.drawString(float(item["x"]), float(item["y"]), str(item["text"]))
                written += 1
            c.save()
            buffer.seek(0)
            stamp = PdfReader(buffer).pages[0]
            page.merge_page(stamp)
        writer.add_page(page)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        writer.write(fh)
    return {"ok": True, "output": str(out), "items_written": written, "pages": len(reader.pages)}


def grid(path: Path, page_no: int, step: int, dpi: int, out_dir: Path) -> dict:
    import fitz  # PyMuPDF
    from PIL import Image, ImageDraw

    doc = fitz.open(str(path))
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"page {page_no} out of range (1..{doc.page_count})")
    page = doc[page_no - 1]
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    draw = ImageDraw.Draw(img)
    height_pt = page.rect.height
    width_pt = page.rect.width
    for x in range(0, int(width_pt) + 1, step):
        px = x * zoom
        draw.line([(px, 0), (px, img.height)], fill="#0ea5e9", width=1)
        draw.text((px + 2, 2), str(x), fill="#0e7490")
    for y in range(0, int(height_pt) + 1, step):
        py = (height_pt - y) * zoom  # PDF origin is bottom-left
        draw.line([(0, py), (img.width, py)], fill="#ff5f57", width=1)
        draw.text((2, py - 11), str(y), fill="#ff5f57")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{path.stem}-p{page_no:03d}-grid.png"
    img.save(target)
    doc.close()
    return {"ok": True, "output": str(target), "page": page_no, "page_size_pt": [width_pt, height_pt], "step": step}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdf_overlay.py", description="Stamp text at coordinates or draw a measuring grid.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--items", type=Path, default=None, help="JSON list of {page,x,y,text,size,color}")
    parser.add_argument("--text", default=None)
    parser.add_argument("--x", type=float, default=None)
    parser.add_argument("--y", type=float, default=None)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--font", default="Helvetica")
    parser.add_argument("--size", type=float, default=10)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--grid", action="store_true", help="render a coordinate grid PNG")
    parser.add_argument("--step", type=int, default=50, help="grid step in points")
    parser.add_argument("--dpi", type=int, default=100)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.grid:
            result = grid(args.file, args.page, args.step, args.dpi, args.out_dir or args.file.parent)
        else:
            if args.items:
                items = json.loads(args.items.read_text(encoding="utf-8"))
                if not isinstance(items, list):
                    raise ValueError("--items must hold a JSON list")
            elif args.text is not None and args.x is not None and args.y is not None:
                items = [{"page": args.page, "x": args.x, "y": args.y, "text": args.text}]
            else:
                parser.error("give --items, or --text with --x and --y, or --grid")
            if not args.out:
                parser.error("--out is required")
            result = overlay(args.file, items, args.out, args.font, args.size)
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
