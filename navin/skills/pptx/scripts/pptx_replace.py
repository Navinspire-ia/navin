#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Edit text, delete or reorder slides of an existing PowerPoint deck.

Usage:
    pptx_replace.py <file.pptx> [--edits EDITS.json] [--delete-slide N ...]
                    [--reorder 3,1,2] [--out OUT.pptx]

EDITS.json is a list of objects:
    {"slide": 2, "shape_id": 5, "text": "New title"}
    {"slide": 2, "shape_name": "Content Placeholder 2",
     "paragraphs": ["first bullet", "second bullet"]}
    {"slide": 3, "shape_name": "Title 1", "find": "2025", "replace": "2026"}

The first run of the first paragraph provides the formatting (font, size,
color, bold) reused for the new text; bullet levels follow the original
paragraphs when present. --delete-slide uses 1-based numbers (applied after
edits); --reorder lists the new order of the 1-based slide numbers.

Exit codes: 0 ok, 1 an edit matched nothing, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import zipfile
from pathlib import Path

from pptx import Presentation
from pptx.exc import PythonPptxError

# Errors a malformed input, a bad edit list or a bad argument can raise; JSON + exit 2.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, IndexError, zipfile.BadZipFile, PythonPptxError)


def _find_shape(slide, shape_id=None, shape_name=None):
    for shape in slide.shapes:
        if shape_id is not None and shape.shape_id == shape_id:
            return shape
        if shape_name is not None and shape.name == shape_name:
            return shape
    return None


def set_paragraphs(text_frame, paragraphs: list[str]) -> None:
    """Rewrite a text frame keeping the formatting of the first run / paragraph."""
    originals = list(text_frame.paragraphs)
    template_p = originals[0]._p
    template_r = template_p.find("{http://schemas.openxmlformats.org/drawingml/2006/main}r")
    rpr = template_r.find("{http://schemas.openxmlformats.org/drawingml/2006/main}rPr") if template_r is not None else None
    ppr_by_index = [copy.deepcopy(p._p.find("{http://schemas.openxmlformats.org/drawingml/2006/main}pPr")) for p in originals]
    tx_body = template_p.getparent()
    for p in originals:
        tx_body.remove(p._p)
    a_ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    for index, text in enumerate(paragraphs):
        p = tx_body.makeelement(f"{a_ns}p", {})
        ppr = ppr_by_index[index] if index < len(ppr_by_index) else ppr_by_index[-1]
        if ppr is not None:
            p.append(copy.deepcopy(ppr))
        r = p.makeelement(f"{a_ns}r", {})
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        t = r.makeelement(f"{a_ns}t", {})
        t.text = text
        r.append(t)
        p.append(r)
        tx_body.append(p)


def _find_replace(text_frame, find: str, repl: str) -> int:
    hits = 0
    for paragraph in text_frame.paragraphs:
        runs = paragraph.runs
        joined = "".join(r.text for r in runs)
        if find not in joined:
            continue
        # keep it simple: put the whole paragraph text in the first run
        new_text = joined.replace(find, repl)
        hits += joined.count(find)
        if runs:
            runs[0].text = new_text
            for r in runs[1:]:
                r.text = ""
    return hits


def apply_edits(prs, edits: list[dict]) -> list[dict]:
    results = []
    slides = list(prs.slides)
    for edit in edits:
        slide_no = int(edit.get("slide", 0))
        if not 1 <= slide_no <= len(slides):
            results.append({"edit": edit, "ok": False, "error": "slide out of range"})
            continue
        shape = _find_shape(slides[slide_no - 1], edit.get("shape_id"), edit.get("shape_name"))
        if shape is None or not getattr(shape, "has_text_frame", False):
            results.append({"edit": edit, "ok": False, "error": "shape not found or has no text frame"})
            continue
        if "paragraphs" in edit:
            set_paragraphs(shape.text_frame, [str(p) for p in edit["paragraphs"]])
        elif "text" in edit:
            set_paragraphs(shape.text_frame, str(edit["text"]).split("\n"))
        elif "find" in edit:
            hits = _find_replace(shape.text_frame, str(edit["find"]), str(edit.get("replace", "")))
            if not hits:
                results.append({"edit": edit, "ok": False, "error": "find text not present"})
                continue
        else:
            results.append({"edit": edit, "ok": False, "error": "edit needs text, paragraphs or find"})
            continue
        results.append({"edit": edit, "ok": True, "shape_id": shape.shape_id})
    return results


def delete_slides(prs, numbers: list[int]) -> int:
    sld_id_lst = prs.slides._sldIdLst
    ids = list(sld_id_lst)
    removed = 0
    for number in sorted(set(numbers), reverse=True):
        if not 1 <= number <= len(ids):
            continue
        sld_id = ids[number - 1]
        prs.part.drop_rel(sld_id.rId)
        sld_id_lst.remove(sld_id)
        removed += 1
    return removed


def reorder_slides(prs, order: list[int]) -> None:
    sld_id_lst = prs.slides._sldIdLst
    ids = list(sld_id_lst)
    if sorted(order) != list(range(1, len(ids) + 1)):
        raise ValueError(f"--reorder must list every slide number exactly once (1..{len(ids)})")
    for sld_id in ids:
        sld_id_lst.remove(sld_id)
    for number in order:
        sld_id_lst.append(ids[number - 1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pptx_replace.py", description="Edit text / slides of a .pptx.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--edits", type=Path, default=None, help="JSON list of edits")
    parser.add_argument("--delete-slide", type=int, action="append", default=[], metavar="N")
    parser.add_argument("--reorder", default=None, help="comma separated new order, e.g. 3,1,2")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        prs = Presentation(str(args.file))
        results = []
        if args.edits:
            edits = json.loads(args.edits.read_text(encoding="utf-8"))
            if not isinstance(edits, list):
                raise ValueError("edits file must hold a JSON list")
            results = apply_edits(prs, edits)
        deleted = delete_slides(prs, args.delete_slide) if args.delete_slide else 0
        if args.reorder:
            reorder_slides(prs, [int(x) for x in args.reorder.split(",") if x.strip()])
        target = args.out or args.file
        prs.save(str(target))
        failed = [r for r in results if not r["ok"]]
        print(json.dumps({"ok": not failed, "output": str(target), "edits": results, "deleted": deleted, "slides": len(prs.slides)}, ensure_ascii=False))
        return 1 if failed else 0
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
