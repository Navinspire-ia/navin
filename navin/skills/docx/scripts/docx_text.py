#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Dump and replace text in a Word document while keeping run formatting.

Commands:
    dump    <file.docx> [--format json|markdown] [--tables]
    replace <file.docx> --find TEXT --with TEXT [--out OUT.docx] [--all] [--json]

`dump` lists every paragraph (body first, then table cells) with its index,
style, text and a flag telling whether it sits inside a table. `replace`
rewrites the matched text inside the run(s) that hold it, so bold, italic,
color and font survive. A match that spans several runs is merged into the
first run of the span and the following runs are emptied.

Exit codes: 0 ok, 1 nothing matched, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
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

# --------------------------------------------------------------------------- #
# iteration
# --------------------------------------------------------------------------- #


def iter_paragraphs(doc, include_tables: bool = True):
    """Yield (paragraph, in_table, location) across body and tables."""
    for para in doc.paragraphs:
        yield para, False, "body"
    if not include_tables:
        return
    for t_index, table in enumerate(doc.tables):
        for r_index, row in enumerate(table.rows):
            for c_index, cell in enumerate(row.cells):
                for para in cell.paragraphs:
                    yield para, True, f"table[{t_index}].row[{r_index}].cell[{c_index}]"


def dump(path: Path, include_tables: bool = True) -> list[dict]:
    doc = Document(str(path))
    rows = []
    for index, (para, in_table, location) in enumerate(iter_paragraphs(doc, include_tables)):
        rows.append(
            {
                "index": index,
                "style": para.style.name if para.style is not None else None,
                "in_table": in_table,
                "location": location,
                "runs": len(para.runs),
                "text": para.text,
            }
        )
    return rows


def to_markdown(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        style = row["style"] or ""
        prefix = ""
        if style.startswith("Heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            prefix = "#" * min(int(level), 6) + " "
        elif style == "Title":
            prefix = "# "
        elif "List" in style:
            prefix = "- "
        marker = " [table]" if row["in_table"] else ""
        lines.append(f"{row['index']:>4}{marker} | {prefix}{row['text']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# replace
# --------------------------------------------------------------------------- #


def _set_run_text(run, text: str) -> None:
    """Replace a run's visible text without touching its w:rPr."""
    r = run._r
    for child in list(r):
        if child.tag in (qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")):
            r.remove(child)
    if text:
        t = r.makeelement(qn("w:t"), {})
        t.text = text
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        r.append(t)


def _span_runs(texts: list[str], start: int, end: int):
    """Return (first, last) indexes of the runs covering [start, end)."""
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
    return first, last


def replace_in_paragraph(para, find: str, repl: str, replace_all: bool) -> int:
    """Replace `find` in a paragraph, run by run when possible, merging spans otherwise.

    Matches are located once on the concatenated text and applied from the last
    to the first, so earlier offsets stay valid and a replacement that contains
    the search text can never loop.
    """
    runs = para.runs
    if not runs or not find:
        return 0
    texts = [r.text for r in runs]
    full = "".join(texts)
    starts = []
    idx = full.find(find)
    while idx >= 0:
        starts.append(idx)
        if not replace_all:
            break
        idx = full.find(find, idx + len(find))
    if not starts:
        return 0
    for start in reversed(starts):
        end = start + len(find)
        first, last = _span_runs(texts, start, end)
        if first is None or last is None:
            continue
        off_first = sum(len(t) for t in texts[:first])
        off_last = sum(len(t) for t in texts[:last])
        head = texts[first][: start - off_first]
        tail = texts[last][end - off_last :]
        texts[first] = head + repl + tail
        for i in range(first + 1, last + 1):
            texts[i] = ""
    for run, text in zip(runs, texts):
        if run.text != text:
            _set_run_text(run, text)
    return len(starts)


def replace(path: Path, find: str, repl: str, out: Path | None, replace_all: bool) -> dict:
    doc = Document(str(path))
    hits = 0
    touched: list[int] = []
    for index, (para, _in_table, _loc) in enumerate(iter_paragraphs(doc, True)):
        if find not in para.text:
            continue
        n = replace_in_paragraph(para, find, repl, replace_all)
        if n:
            hits += n
            touched.append(index)
            if not replace_all:
                break
    target = out or path
    if hits:
        doc.save(str(target))
    return {"ok": hits > 0, "replacements": hits, "paragraphs": touched, "output": str(target)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docx_text.py", description="Dump or replace text in a .docx.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_dump = sub.add_parser("dump", help="list paragraphs with index, style, text")
    p_dump.add_argument("file", type=Path)
    p_dump.add_argument("--format", choices=("json", "markdown"), default="json")
    p_dump.add_argument("--no-tables", action="store_true", help="skip table cells")

    p_rep = sub.add_parser("replace", help="replace a text match preserving run formatting")
    p_rep.add_argument("file", type=Path)
    p_rep.add_argument("--find", required=True)
    p_rep.add_argument("--with", dest="repl", required=True)
    p_rep.add_argument("--out", type=Path, default=None, help="output path (default: in place)")
    p_rep.add_argument("--all", action="store_true", help="replace every occurrence")
    p_rep.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "dump":
            rows = dump(args.file, include_tables=not args.no_tables)
            print(json.dumps(rows, ensure_ascii=False, indent=2) if args.format == "json" else to_markdown(rows))
            return 0
        result = replace(args.file, args.find, args.repl, args.out, args.all)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"{result['replacements']} replacement(s) in paragraphs {result['paragraphs']} -> {result['output']}")
        return 0 if result["ok"] else 1
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
