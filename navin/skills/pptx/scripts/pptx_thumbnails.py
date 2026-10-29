#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Render a contact sheet of a PowerPoint deck, or print its outline.

Usage:
    pptx_thumbnails.py <file.pptx> [--out sheet.png] [--cols 4] [--dpi 60] [--outline]

With LibreOffice available the deck is converted to PDF, each page is
rendered with PyMuPDF and tiled into one PNG with slide numbers (also writes
one PNG per slide next to the sheet when --per-slide is set). Without
LibreOffice, or with --outline, a text outline (slide number, layout, title,
first text lines) is printed so the deck can still be reviewed.

Exit codes: 0 ok, 3 soffice missing and no --outline, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from pptx import Presentation
from pptx.exc import PythonPptxError

# Errors a malformed deck, a failed LibreOffice run or a rendering problem can raise.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile, PythonPptxError, subprocess.SubprocessError)


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return None


def outline(path: Path) -> list[dict]:
    prs = Presentation(str(path))
    rows = []
    for index, slide in enumerate(prs.slides, start=1):
        title = None
        lines: list[str] = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False) or not shape.text_frame.text.strip():
                continue
            if title is None and shape.is_placeholder and "TITLE" in str(shape.placeholder_format.type):
                title = shape.text_frame.text.strip()
                continue
            lines.extend(ln.strip() for ln in shape.text_frame.text.splitlines() if ln.strip())
        if title is None and lines:
            title = lines.pop(0)
        rows.append({"slide": index, "layout": slide.slide_layout.name, "title": title or "", "lines": lines[:8]})
    return rows


def outline_text(rows: list[dict]) -> str:
    out = []
    for row in rows:
        out.append(f"{row['slide']:>3}. [{row['layout']}] {row['title']}")
        for line in row["lines"]:
            out.append(f"       - {line[:100]}")
    return "\n".join(out)


def _no_window() -> dict:
    """No console window for soffice on Windows, through navin's helper when navin is importable."""
    try:
        from navin.utils.proc import no_window_kwargs

        return dict(no_window_kwargs())
    except ImportError:
        return {"creationflags": 0x08000000} if os.name == "nt" else {}


def to_pdf(path: Path, work: Path, timeout: int) -> Path:
    binary = find_soffice()
    if not binary:
        raise RuntimeError("soffice missing")
    cmd = [binary, "--headless", "--norestore", f"-env:UserInstallation=file://{work / 'profile'}", "--convert-to", "pdf", "--outdir", str(work), str(path.resolve())]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, **_no_window())
    pdf = work / f"{path.stem}.pdf"
    if proc.returncode != 0 or not pdf.is_file():
        raise RuntimeError(f"soffice failed: {(proc.stderr or proc.stdout).strip()[-400:]}")
    return pdf


def contact_sheet(pdf: Path, out: Path, cols: int, dpi: int, per_slide: bool) -> dict:
    import pymupdf
    from PIL import Image, ImageDraw

    doc = pymupdf.open(str(pdf))
    zoom = dpi / 72
    thumbs = []
    for page_index, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if per_slide:
            img.save(out.parent / f"{out.stem}-{page_index:02d}.png")
        thumbs.append(img)
    doc.close()
    if not thumbs:
        raise RuntimeError("no pages rendered")
    w, h = thumbs[0].size
    gap, label_h = 12, 18
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * (w + gap) + gap, rows * (h + gap + label_h) + gap), "#f4f3ee")
    draw = ImageDraw.Draw(sheet)
    for i, thumb in enumerate(thumbs):
        x = gap + (i % cols) * (w + gap)
        y = gap + (i // cols) * (h + gap + label_h)
        draw.rectangle([x - 1, y - 1, x + w, y + h], outline="#17171a")
        sheet.paste(thumb, (x, y))
        draw.text((x, y + h + 2), f"{i + 1}", fill="#17171a")
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return {"ok": True, "output": str(out), "slides": len(thumbs), "cols": cols, "size": sheet.size}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pptx_thumbnails.py", description="Contact sheet or outline of a deck.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--out", type=Path, default=None, help="PNG path (default: <deck>-thumbs.png)")
    parser.add_argument("--cols", type=int, default=4)
    parser.add_argument("--dpi", type=int, default=60)
    parser.add_argument("--per-slide", action="store_true", help="also write one PNG per slide")
    parser.add_argument("--outline", action="store_true", help="print a text outline instead of rendering")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        if args.outline or not find_soffice():
            rows = outline(args.file)
            if not args.outline:
                print("soffice not found: printing a text outline instead of thumbnails", file=sys.stderr)
            print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json else outline_text(rows))
            return 0 if args.outline else 3
        out = args.out or args.file.with_name(f"{args.file.stem}-thumbs.png")
        with tempfile.TemporaryDirectory(prefix="navin-thumbs-") as tmp:
            pdf = to_pdf(args.file, Path(tmp), args.timeout)
            result = contact_sheet(pdf, out, max(1, args.cols), args.dpi, args.per_slide)
        print(json.dumps(result) if args.json else result["output"])
        return 0
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
