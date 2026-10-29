#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Extract text and tables from a PDF with pdfplumber.

Usage:
    pdf_text.py <file.pdf> [--pages 1-3,7] [--format json|markdown|text]
                [--tables] [--layout] [--out FILE]

--tables adds every detected table as a list of rows (CSV-like lists of
cells). --layout keeps the horizontal layout (columns stay aligned). Pages
are 1-based in the arguments and in the output.

Exit codes: 0 ok, 1 no text found (scanned PDF?), 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pdfplumber
from pdfminer.pdftypes import PDFException

# Errors a malformed PDF or a bad page selection can raise; reported as JSON with exit 2.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, IndexError, PDFException)


def parse_pages(spec: str | None, total: int) -> list[int]:
    """'1-3,7' -> [1, 2, 3, 7] (1-based, clipped to total)."""
    if not spec:
        return list(range(1, total + 1))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a else 1
            end = int(b) if b else total
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(part))
    return [p for p in dict.fromkeys(pages) if 1 <= p <= total]


def extract(path: Path, pages: str | None, tables: bool, layout: bool) -> dict:
    result = {"file": str(path), "pages": []}
    with pdfplumber.open(str(path)) as pdf:
        result["page_count"] = len(pdf.pages)
        result["metadata"] = {k: str(v) for k, v in (pdf.metadata or {}).items()}
        for number in parse_pages(pages, len(pdf.pages)):
            page = pdf.pages[number - 1]
            text = page.extract_text(layout=layout) or ""
            entry = {"page": number, "width": page.width, "height": page.height, "chars": len(text), "text": text}
            if tables:
                found = []
                for table in page.extract_tables():
                    found.append([[("" if c is None else str(c)) for c in row] for row in table])
                entry["tables"] = found
            result["pages"].append(entry)
    result["total_chars"] = sum(p["chars"] for p in result["pages"])
    return result


def to_markdown(result: dict) -> str:
    lines = [f"# {Path(result['file']).name}  ({result['page_count']} pages)"]
    for entry in result["pages"]:
        lines += ["", f"## Page {entry['page']}", "", entry["text"]]
        for t_index, table in enumerate(entry.get("tables", []), start=1):
            if not table:
                continue
            lines += ["", f"### Table {t_index}", ""]
            header = table[0]
            lines.append("| " + " | ".join(h.replace("\n", " ") for h in header) + " |")
            lines.append("|" + "---|" * len(header))
            for row in table[1:]:
                lines.append("| " + " | ".join(c.replace("\n", " ") for c in row) + " |")
    return "\n".join(lines)


def to_text(result: dict) -> str:
    return "\n\f\n".join(entry["text"] for entry in result["pages"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdf_text.py", description="Extract text and tables from a PDF.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--pages", default=None, help="1-based page selection, e.g. 1-3,7")
    parser.add_argument("--format", choices=("json", "markdown", "text"), default="json")
    parser.add_argument("--tables", action="store_true", help="also extract tables")
    parser.add_argument("--layout", action="store_true", help="preserve horizontal layout")
    parser.add_argument("--out", type=Path, default=None, help="write the output to a file")
    args = parser.parse_args(argv)
    try:
        result = extract(args.file, args.pages, args.tables, args.layout)
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2
    if args.format == "json":
        output = json.dumps(result, ensure_ascii=False, indent=2)
    elif args.format == "markdown":
        output = to_markdown(result)
    else:
        output = to_text(result)
    if args.out:
        args.out.write_text(output, encoding="utf-8")
        print(str(args.out))
    else:
        print(output)
    if result["total_chars"] == 0:
        print("no text layer found: the PDF is probably scanned, OCR is needed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
