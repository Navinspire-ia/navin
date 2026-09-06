"""Render a finished document to page images and one contact sheet.

Reading the file back is the last step before handing it over: the agent looks
at what the reader will open, not at the HTML it came from. A PDF is rendered
directly. A DOCX, PPTX or XLSX goes through LibreOffice first, which lays it out
the way Word, PowerPoint and Excel do (real reflow, real fonts). Without
LibreOffice a PPTX falls back to the geometric approximation of
``preview_pptx`` and says so; a DOCX or XLSX cannot be rendered faithfully and
the command explains what to install rather than showing a guess.

Usage::

    python3 -m navin.documents.preview_document report.docx previews/
    python3 -m navin.documents.preview_document deck.pptx previews/ --json
    python3 -m navin.documents.preview_document report.pdf previews/ --max-pages 8
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__:
    from . import _pdf
    from ._office import OFFICE_SUFFIXES, OfficeUnavailableError, find_soffice, install_hint, to_pdf
else:  # copied as a standalone folder into a user workspace
    import _pdf
    from _office import OFFICE_SUFFIXES, OfficeUnavailableError, find_soffice, install_hint, to_pdf

DEFAULT_MAX_PAGES = 40


def _approximate_pptx(source: Path, folder: Path) -> list[Path]:
    if __package__:
        from .preview_pptx import render_approximate
    else:
        from preview_pptx import render_approximate
    return render_approximate(source, folder)


def render(
    source: Path,
    folder: Path,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_edge: int = _pdf.PAGE_MAX_EDGE,
    soffice: str | None = None,
    sheet: bool = True,
) -> dict[str, Any]:
    """Render *source* into *folder*; return the pages, the sheet and the mode."""
    source = Path(source).expanduser()
    folder = Path(folder).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"not found: {source}")
    folder.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    result: dict[str, Any] = {"source": str(source), "mode": "real", "note": None}

    if suffix == ".pdf":
        pdf = source
    elif suffix in OFFICE_SUFFIXES:
        try:
            pdf = to_pdf(source, folder / "_render", soffice=soffice)
        except OfficeUnavailableError:
            if suffix == ".pptx":
                pages = _approximate_pptx(source, folder)
                result.update(
                    mode="approximate",
                    note=(
                        "LibreOffice is not installed, so these images are drawn from the "
                        "shapes' geometry (real positions, sizes and colours, approximate "
                        "type). Good for spotting overlap, overflow and empty blocks; "
                        + install_hint()
                        + " for a faithful render."
                    ),
                    pages=[str(p) for p in pages],
                )
                if sheet and pages:
                    result["sheet"] = str(
                        _pdf.contact_sheet(pages, folder / f"{source.stem}-sheet.png")
                    )
                return result
            raise RuntimeError(
                f"{source.name} cannot be rendered without LibreOffice; {install_hint()}. "
                "Meanwhile, score the HTML source with word_qa (layout, contrast, "
                "overflow) and validate the file with doc_check."
            ) from None
    else:
        raise ValueError(f"{source.name}: unsupported format (pdf, docx, pptx, xlsx)")

    pages = _pdf.rasterize(pdf, folder, prefix=source.stem, max_pages=max_pages, max_edge=max_edge)
    result["pages"] = [str(p) for p in pages]
    result["total_pages"] = _pdf.page_count(pdf)
    if sheet and pages:
        result["sheet"] = str(_pdf.contact_sheet(pages, folder / f"{source.stem}-sheet.png"))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="preview_document",
        description="Render a DOCX / PPTX / XLSX / PDF to page images plus a contact sheet.",
    )
    parser.add_argument("file", help="The document to render")
    parser.add_argument("folder", help="Where the PNG files go")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--max-edge", type=int, default=_pdf.PAGE_MAX_EDGE, help="Longest side in px")
    parser.add_argument("--soffice", help="Path to the LibreOffice binary")
    parser.add_argument("--no-sheet", action="store_true", help="Skip the contact sheet")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON")
    args = parser.parse_args(argv)
    try:
        result = render(
            Path(args.file),
            Path(args.folder),
            max_pages=args.max_pages,
            max_edge=args.max_edge,
            soffice=args.soffice,
            sheet=not args.no_sheet,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"preview_document: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    pages = result.get("pages") or []
    total = result.get("total_pages")
    shown = f"{len(pages)}" + (f" of {total}" if total and total > len(pages) else "")
    print(f"Rendered {shown} page(s) of {Path(args.file).name} into {args.folder} ({result['mode']})")
    if result.get("sheet"):
        print(f"Contact sheet: {result['sheet']}")
    if result.get("note"):
        print(f"preview_document: {result['note']}", file=sys.stderr)
    return 0


def soffice_status() -> str:
    """One line for diagnostics: where LibreOffice is, or that it is missing."""
    found = find_soffice()
    return f"LibreOffice: {found}" if found else f"LibreOffice: missing ({install_hint()})"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
