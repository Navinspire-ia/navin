#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""List or add review comments in a Word document.

Commands:
    list <file.docx> [--json]
    add  <file.docx> --anchor TEXT --text COMMENT [--author NAME] [--initials AB] [--out OUT.docx]

`add` anchors the comment on the runs of the first paragraph whose text
contains --anchor. It relies on Document.add_comment (python-docx >= 1.2);
older versions get a clear error and exit code 3.

Exit codes: 0 ok, 1 anchor not found, 2 usage or I/O error, 3 python-docx too old.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.opc.exceptions import OpcError
from lxml import etree

# Errors a malformed input or a bad argument can raise; reported as JSON with exit 2.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, OpcError, etree.LxmlError)


def _all_paragraphs(doc):
    for para in doc.paragraphs:
        yield para
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def list_comments(path: Path) -> list[dict]:
    doc = Document(str(path))
    comments = getattr(doc, "comments", None)
    if comments is None:
        raise RuntimeError("python-docx >= 1.2 is required to read comments")
    rows = []
    for c in comments:
        rows.append(
            {
                "id": c.comment_id,
                "author": c.author,
                "initials": c.initials,
                "text": c.text,
            }
        )
    return rows


def add_comment(path: Path, anchor: str, text: str, author: str, initials: str, out: Path | None) -> dict:
    doc = Document(str(path))
    if not hasattr(doc, "add_comment"):
        raise RuntimeError("python-docx >= 1.2 is required for Document.add_comment")
    for index, para in enumerate(_all_paragraphs(doc)):
        if anchor in para.text and para.runs:
            comment = doc.add_comment(runs=para.runs, text=text, author=author, initials=initials)
            target = out or path
            doc.save(str(target))
            return {"ok": True, "paragraph": index, "comment_id": comment.comment_id, "output": str(target)}
    return {"ok": False, "error": f"no paragraph contains {anchor!r}"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docx_comments.py", description="List or add Word comments.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list", help="list existing comments")
    p_list.add_argument("file", type=Path)
    p_list.add_argument("--json", action="store_true")
    p_add = sub.add_parser("add", help="add a comment on the first paragraph containing --anchor")
    p_add.add_argument("file", type=Path)
    p_add.add_argument("--anchor", required=True, help="text that identifies the paragraph")
    p_add.add_argument("--text", required=True, help="comment body")
    p_add.add_argument("--author", default="Navin")
    p_add.add_argument("--initials", default="NV")
    p_add.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            rows = list_comments(args.file)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    print(f"#{row['id']} {row['author']}: {row['text']}")
                if not rows:
                    print("no comments")
            return 0
        result = add_comment(args.file, args.anchor, args.text, args.author, args.initials, args.out)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 3
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
