"""Report the text a converter drops between an HTML master and its output.

Not a unit test: a diagnostic run over the whole template library, since the
only failure that matters here is content quietly disappearing.

Usage::

    python3 tests/documents/check_coverage.py word
    python3 tests/documents/check_coverage.py excel
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from navin.documents import html2docx, html2xlsx  # noqa: E402
from navin.documents._chromium import find_chromium, measure  # noqa: E402

_INNER_TEXT = "publish({ text: document.body.innerText });"


def rendered_text(page: Path, chromium: str, workdir: Path) -> str:
    from navin.documents import _dom

    payload = measure(
        page,
        _dom.script(_INNER_TEXT, 794, 1123),
        chromium=chromium,
        workdir=workdir,
        timeout=90,
        viewport=(794, 2246),
        prefix="text",
    )
    return str(payload.get("text") or "")


def words(text: str) -> list[str]:
    return [w for w in re.split(r"[^\w%€$£.,/-]+", text.lower()) if len(w) > 1]


def docx_text(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    parts = [p.text for p in document.paragraphs]
    parts += [c.text for t in document.tables for r in t.rows for c in r.cells]
    for section in document.sections:
        parts += [p.text for p in section.footer.paragraphs]
        parts += [p.text for p in section.header.paragraphs]
    return " ".join(parts)


def xlsx_content(path: Path) -> tuple[str, set[float]]:
    """Text of a workbook plus every number it holds, formulas included.

    A converted workbook stores 240000 where the master displayed
    "€240,000", so comparing rendered strings would report a loss that is in
    fact the whole point of the conversion.
    """
    from openpyxl import load_workbook
    from openpyxl.utils import range_boundaries

    workbook = load_workbook(str(path))
    parts: list[str] = []
    numbers: set[float] = set()
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                value = cell.value
                if value in (None, ""):
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numbers.add(round(float(value), 4))
                    continue
                text = str(value)
                match = re.match(r"^=SUM\(([A-Z]+\d+:[A-Z]+\d+)\)$", text)
                if match:
                    left, top, right, bottom = range_boundaries(match.group(1))
                    total = 0.0
                    for line in sheet.iter_rows(
                        min_row=top, max_row=bottom, min_col=left, max_col=right
                    ):
                        for member in line:
                            if isinstance(member.value, (int, float)):
                                total += float(member.value)
                    numbers.add(round(total, 4))
                    continue
                parts.append(text)
    return " ".join(parts), numbers


def as_number(token: str) -> float | None:
    raw = token.replace("€", "").replace("$", "").replace("£", "").replace(",", "")
    percent = raw.endswith("%")
    raw = raw.rstrip("%").strip(".,")
    try:
        value = float(raw)
    except ValueError:
        return None
    return round(value / 100, 4) if percent else round(value, 4)


def main() -> int:
    category = sys.argv[1] if len(sys.argv) > 1 else "word"
    folders = sorted((ROOT / "templates" / category).iterdir())
    chromium = find_chromium(None)
    failures = 0
    with tempfile.TemporaryDirectory() as raw:
        workdir = Path(raw)
        for folder in folders:
            page = folder / "document.html"
            if not page.is_file():
                continue
            output = workdir / f"{folder.name}.{'docx' if category == 'word' else 'xlsx'}"
            numbers: set[float] = set()
            if category == "word":
                html2docx.convert(str(page), output)
                produced = docx_text(output)
            else:
                html2xlsx.convert(str(page), output)
                produced, numbers = xlsx_content(output)
            source = rendered_text(page, chromium, workdir)
            expected = words(source)
            got = set(words(produced))
            missing = [
                word for word in expected
                if word not in got and (as_number(word) is None or as_number(word) not in numbers)
            ]
            ratio = 1 - len(missing) / max(1, len(expected))
            flag = "ok " if ratio > 0.98 else "LOW"
            if ratio <= 0.98:
                failures += 1
            print(f"{flag} {folder.name:38s} {ratio:6.1%}  manquant: {missing[:12]}")
    print(f"\n{len(folders) - failures}/{len(folders)} gabarits au-dessus de 98%")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
