#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Page-level PDF operations: merge, split, rotate, extract, render, info.

Commands:
    info    <file.pdf> [--json]
    merge   <a.pdf> <b.pdf> ... --out merged.pdf
    split   <file.pdf> --ranges 1-3,4-6 --out-dir DIR        (one PDF per range)
    split   <file.pdf> --every 1 --out-dir DIR               (one PDF per N pages)
    extract <file.pdf> --pages 2,5-7 --out subset.pdf
    rotate  <file.pdf> --degrees 90 [--pages 1,3] --out rotated.pdf
    render  <file.pdf> [--pages 1-2] [--dpi 110] --out-dir DIR   (PNG per page, PyMuPDF)

Pages are 1-based everywhere.

Exit codes: 0 ok, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

# Errors a malformed PDF, a bad page selection or the PyMuPDF renderer can raise.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError, PyPdfError)


def parse_pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a) if a else 1, (int(b) if b else total) + 1))
        else:
            pages.append(int(part))
    return [p for p in dict.fromkeys(pages) if 1 <= p <= total]


def info(path: Path) -> dict:
    reader = PdfReader(str(path))
    meta = reader.metadata or {}
    first = reader.pages[0] if len(reader.pages) else None
    return {
        "file": str(path),
        "pages": len(reader.pages),
        "encrypted": reader.is_encrypted,
        "has_form": bool(reader.get_fields()),
        "page_size_pt": [float(first.mediabox.width), float(first.mediabox.height)] if first is not None else None,
        "metadata": {str(k).lstrip("/"): str(v) for k, v in meta.items()},
        "outline_items": len(reader.outline) if reader.outline else 0,
    }


def merge(inputs: list[Path], out: Path) -> dict:
    writer = PdfWriter()
    for path in inputs:
        writer.append(str(path))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        writer.write(fh)
    return {"ok": True, "output": str(out), "inputs": len(inputs), "pages": len(writer.pages)}


def _write_subset(reader: PdfReader, pages: list[int], out: Path) -> None:
    writer = PdfWriter()
    for number in pages:
        writer.add_page(reader.pages[number - 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        writer.write(fh)


def split(path: Path, ranges: str | None, every: int | None, out_dir: Path) -> dict:
    reader = PdfReader(str(path))
    total = len(reader.pages)
    chunks: list[list[int]] = []
    if ranges:
        for part in ranges.split(","):
            chunks.append(parse_pages(part, total))
    else:
        step = max(1, every or 1)
        for start in range(1, total + 1, step):
            chunks.append(list(range(start, min(total, start + step - 1) + 1)))
    outputs = []
    for index, pages in enumerate(chunks, start=1):
        if not pages:
            continue
        target = out_dir / f"{path.stem}-part{index:02d}.pdf"
        _write_subset(reader, pages, target)
        outputs.append({"file": str(target), "pages": pages})
    return {"ok": True, "outputs": outputs}


def extract(path: Path, pages: str, out: Path) -> dict:
    reader = PdfReader(str(path))
    selected = parse_pages(pages, len(reader.pages))
    _write_subset(reader, selected, out)
    return {"ok": True, "output": str(out), "pages": selected}


def rotate(path: Path, degrees: int, pages: str | None, out: Path) -> dict:
    if degrees % 90:
        raise ValueError("--degrees must be a multiple of 90")
    reader = PdfReader(str(path))
    selected = set(parse_pages(pages, len(reader.pages)))
    writer = PdfWriter()
    for number, page in enumerate(reader.pages, start=1):
        if number in selected:
            page.rotate(degrees)
        writer.add_page(page)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        writer.write(fh)
    return {"ok": True, "output": str(out), "rotated_pages": sorted(selected), "degrees": degrees}


def render(path: Path, pages: str | None, dpi: int, out_dir: Path) -> dict:
    import pymupdf

    doc = pymupdf.open(str(path))
    selected = parse_pages(pages, doc.page_count)
    out_dir.mkdir(parents=True, exist_ok=True)
    zoom = dpi / 72
    outputs = []
    for number in selected:
        pix = doc[number - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        target = out_dir / f"{path.stem}-p{number:03d}.png"
        pix.save(str(target))
        outputs.append({"page": number, "file": str(target), "width": pix.width, "height": pix.height})
    doc.close()
    return {"ok": True, "dpi": dpi, "outputs": outputs}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdf_pages.py", description="Merge, split, rotate, extract, render PDF pages.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("info")
    p.add_argument("file", type=Path)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("merge")
    p.add_argument("files", type=Path, nargs="+")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("split")
    p.add_argument("file", type=Path)
    p.add_argument("--ranges", default=None, help="comma separated ranges, e.g. 1-3,4-6")
    p.add_argument("--every", type=int, default=None, help="pages per chunk")
    p.add_argument("--out-dir", type=Path, required=True)
    p = sub.add_parser("extract")
    p.add_argument("file", type=Path)
    p.add_argument("--pages", required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("rotate")
    p.add_argument("file", type=Path)
    p.add_argument("--degrees", type=int, required=True)
    p.add_argument("--pages", default=None)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("render")
    p.add_argument("file", type=Path)
    p.add_argument("--pages", default=None)
    p.add_argument("--dpi", type=int, default=110)
    p.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "info":
            result = info(args.file)
        elif args.command == "merge":
            result = merge(args.files, args.out)
        elif args.command == "split":
            result = split(args.file, args.ranges, args.every, args.out_dir)
        elif args.command == "extract":
            result = extract(args.file, args.pages, args.out)
        elif args.command == "rotate":
            result = rotate(args.file, args.degrees, args.pages, args.out)
        else:
            result = render(args.file, args.pages, args.dpi, args.out_dir)
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
