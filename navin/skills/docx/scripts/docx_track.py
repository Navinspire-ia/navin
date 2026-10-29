#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Work with tracked changes (revisions) in a Word document.

Commands:
    accept  <file.docx> [--out OUT.docx]
        keep insertions, drop deletions, clear formatting-change records
    reject  <file.docx> [--out OUT.docx]
        drop insertions, restore deletions, clear formatting-change records
    replace <file.docx> --find TEXT --with TEXT [--author NAME] [--all] [--out OUT.docx]
        record a tracked deletion of the match followed by a tracked insertion
    count   <file.docx> [--json]
        number of w:ins / w:del / property-change elements

All parts holding body text are processed: document, headers, footers,
footnotes and endnotes. Output defaults to in-place.

Exit codes: 0 ok, 1 nothing matched/changed, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.opc.exceptions import OpcError
from docx.oxml.ns import qn
from lxml import etree

# Errors a malformed input or a bad argument can raise; reported as JSON with exit 2.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, OpcError, etree.LxmlError)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
PROP_CHANGES = ("w:rPrChange", "w:pPrChange", "w:sectPrChange", "w:tblPrChange", "w:trPrChange", "w:tcPrChange")


# --------------------------------------------------------------------------- #
# part iteration
# --------------------------------------------------------------------------- #


def _text_parts(doc):
    """Yield lxml root elements of every part that can hold revisions."""
    yield doc.element
    for section in doc.sections:
        for part in (
            section.header,
            section.footer,
            section.first_page_header,
            section.first_page_footer,
            section.even_page_header,
            section.even_page_footer,
        ):
            if not part.is_linked_to_previous:
                yield part._element
    for rel in doc.part.rels.values():
        if rel.reltype.endswith(("/footnotes", "/endnotes")) and hasattr(rel.target_part, "_element"):
            yield rel.target_part._element


def _unwrap(el) -> None:
    """Replace an element by its children."""
    parent = el.getparent()
    idx = parent.index(el)
    for child in list(el):
        parent.insert(idx, child)
        idx += 1
    parent.remove(el)


def _remove(el) -> None:
    el.getparent().remove(el)


# --------------------------------------------------------------------------- #
# accept / reject
# --------------------------------------------------------------------------- #


def _strip_property_changes(root) -> int:
    n = 0
    for tag in PROP_CHANGES:
        for el in list(root.iter(qn(tag))):
            _remove(el)
            n += 1
    return n


def _del_text_to_text(root) -> None:
    """Turn w:delText back into w:t when a deletion is rejected."""
    for el in list(root.iter(qn("w:delText"))):
        el.tag = qn("w:t")
    for el in list(root.iter(qn("w:delInstrText"))):
        el.tag = qn("w:instrText")


def _apply(root, mode: str) -> dict:
    ins = list(root.iter(qn("w:ins")))
    dels = list(root.iter(qn("w:del")))
    stats = {"ins": len(ins), "del": len(dels)}
    if mode == "accept":
        for el in dels:
            # paragraph-mark deletions live inside w:rPr: dropping the marker is enough
            if el.getparent().tag == qn("w:rPr"):
                _remove(el)
            else:
                _remove(el)
        for el in ins:
            if el.getparent().tag == qn("w:rPr"):
                _remove(el)  # inserted paragraph mark becomes a normal one
            else:
                _unwrap(el)
        for el in list(root.iter(qn("w:moveFrom"))):
            _remove(el)
        for el in list(root.iter(qn("w:moveTo"))):
            _unwrap(el)
    else:
        for el in ins:
            _remove(el)
        for el in dels:
            if el.getparent().tag == qn("w:rPr"):
                _remove(el)
            else:
                _del_text_to_text(el)
                _unwrap(el)
        for el in list(root.iter(qn("w:moveTo"))):
            _remove(el)
        for el in list(root.iter(qn("w:moveFrom"))):
            _del_text_to_text(el)
            _unwrap(el)
    for tag in ("w:moveFromRangeStart", "w:moveFromRangeEnd", "w:moveToRangeStart", "w:moveToRangeEnd"):
        for el in list(root.iter(qn(tag))):
            _remove(el)
    stats["property_changes"] = _strip_property_changes(root)
    return stats


def resolve(path: Path, mode: str, out: Path | None) -> dict:
    doc = Document(str(path))
    total = {"ins": 0, "del": 0, "property_changes": 0}
    for root in _text_parts(doc):
        for key, value in _apply(root, mode).items():
            total[key] += value
    target = out or path
    doc.save(str(target))
    changed = sum(total.values()) > 0
    return {"ok": changed, "mode": mode, "output": str(target), **total}


def count(path: Path) -> dict:
    doc = Document(str(path))
    total = {"ins": 0, "del": 0, "property_changes": 0}
    for root in _text_parts(doc):
        total["ins"] += sum(1 for _ in root.iter(qn("w:ins")))
        total["del"] += sum(1 for _ in root.iter(qn("w:del")))
        total["property_changes"] += sum(sum(1 for _ in root.iter(qn(t))) for t in PROP_CHANGES)
    return total


# --------------------------------------------------------------------------- #
# tracked replace
# --------------------------------------------------------------------------- #


class _IdPool:
    def __init__(self, roots):
        used = [0]
        for root in roots:
            for el in root.iter():
                if el.tag in (qn("w:ins"), qn("w:del"), qn("w:comment")) or el.tag.endswith("RangeStart"):
                    raw = el.get(qn("w:id"))
                    if raw is None:
                        continue
                    if not raw.lstrip("-").isdigit():
                        raise ValueError(f"non-numeric w:id {raw!r} on {etree.QName(el).localname}; the document is malformed")
                    used.append(int(raw))
        self.next_id = max(used) + 1

    def take(self) -> str:
        value = self.next_id
        self.next_id += 1
        return str(value)


def _make_run_like(template_r, text: str, deleted: bool):
    """Clone the rPr of template_r into a fresh run holding `text`."""
    r = template_r.makeelement(qn("w:r"), {})
    rpr = template_r.find(qn("w:rPr"))
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    t = r.makeelement(qn("w:delText") if deleted else qn("w:t"), {})
    t.text = text
    t.set(XML_SPACE, "preserve")
    r.append(t)
    return r


def _wrap(tag: str, run_el, author: str, date: str, ids: _IdPool):
    wrapper = run_el.makeelement(qn(tag), {})
    wrapper.set(qn("w:id"), ids.take())
    wrapper.set(qn("w:author"), author)
    wrapper.set(qn("w:date"), date)
    wrapper.append(run_el)
    return wrapper


def _tracked_replace_in_paragraph(para_el, find: str, repl: str, author: str, date: str, ids: _IdPool, replace_all: bool) -> int:
    hits = 0
    while True:
        runs = [r for r in para_el.iter(qn("w:r")) if r.getparent().tag not in (qn("w:ins"), qn("w:del"))]
        texts = ["".join(t.text or "" for t in r.iter(qn("w:t"))) for r in runs]
        full = "".join(texts)
        start = full.find(find)
        if start < 0:
            return hits
        end = start + len(find)
        pos = 0
        first = last = None
        for i, text in enumerate(texts):
            nxt = pos + len(text)
            if first is None and start < nxt:
                first = i
            if first is not None and end <= nxt:
                last = i
                break
            pos = nxt
        if first is None or last is None:
            return hits
        off_first = sum(len(t) for t in texts[:first])
        off_last = sum(len(t) for t in texts[:last])
        head = texts[first][: start - off_first]
        tail = texts[last][end - off_last :]
        anchor = runs[first]
        parent = anchor.getparent()
        idx = parent.index(anchor)
        new_nodes = []
        if head:
            new_nodes.append(_make_run_like(anchor, head, deleted=False))
        new_nodes.append(_wrap("w:del", _make_run_like(anchor, find, deleted=True), author, date, ids))
        if repl:
            new_nodes.append(_wrap("w:ins", _make_run_like(anchor, repl, deleted=False), author, date, ids))
        if tail:
            new_nodes.append(_make_run_like(runs[last], tail, deleted=False))
        for r in runs[first : last + 1]:
            parent.remove(r)
        for offset, node in enumerate(new_nodes):
            parent.insert(idx + offset, node)
        hits += 1
        if not replace_all or find in repl:
            return hits


def tracked_replace(path: Path, find: str, repl: str, author: str, out: Path | None, replace_all: bool) -> dict:
    doc = Document(str(path))
    roots = list(_text_parts(doc))
    ids = _IdPool(roots)
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hits = 0
    for root in roots:
        for para_el in root.iter(qn("w:p")):
            if find not in "".join(t.text or "" for t in para_el.iter(qn("w:t"))):
                continue
            hits += _tracked_replace_in_paragraph(para_el, find, repl, author, date, ids, replace_all)
            if hits and not replace_all:
                break
        if hits and not replace_all:
            break
    target = out or path
    if hits:
        doc.save(str(target))
    return {"ok": hits > 0, "replacements": hits, "author": author, "output": str(target)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docx_track.py", description="Accept, reject or create tracked changes.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("accept", "accept every revision"), ("reject", "reject every revision")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("file", type=Path)
        p.add_argument("--out", type=Path, default=None)
    p_rep = sub.add_parser("replace", help="tracked deletion + insertion for a text match")
    p_rep.add_argument("file", type=Path)
    p_rep.add_argument("--find", required=True)
    p_rep.add_argument("--with", dest="repl", required=True)
    p_rep.add_argument("--author", default="Navin")
    p_rep.add_argument("--all", action="store_true")
    p_rep.add_argument("--out", type=Path, default=None)
    p_count = sub.add_parser("count", help="count revisions")
    p_count.add_argument("file", type=Path)
    p_count.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in ("accept", "reject"):
            result = resolve(args.file, args.command, args.out)
        elif args.command == "replace":
            result = tracked_replace(args.file, args.find, args.repl, args.author, args.out, args.all)
        else:
            result = count(args.file)
            print(json.dumps(result))
            return 0
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
