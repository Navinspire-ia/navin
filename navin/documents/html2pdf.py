"""Produce a PDF from an HTML document, or from a finished Office file.

A PDF is the one deliverable a reader opens on any machine, so it deserves the
same route as the DOCX and the PPTX: one command, the same quality gate, and a
readback of the result instead of a blind print.

Two inputs are accepted:

* an HTML document laid out as A4 ``.page`` sections (what ``word_design
  render`` produces and what the Word and PDF templates ship). Chromium prints
  it with the template's own ``@page`` rule, no browser header or footer, web
  fonts loaded, screen-only decor (page shadows, grey desk) turned off;
* a ``.docx``, ``.pptx`` or ``.xlsx`` file, rendered by LibreOffice when the
  machine has it. Without LibreOffice the command says so and points at the
  HTML source instead of producing an approximation.

After printing, the PDF is read back: page count against the number of ``.page``
sections (a spill means a page ran longer than A4), pages that carry no
selectable text (a picture of text is not a document), and the paper size.

Usage::

    python3 -m navin.documents.html2pdf document.html -o report.pdf
    python3 -m navin.documents.html2pdf report.docx -o report.pdf
    python3 -m navin.documents.html2pdf slides/ -o deck.pdf       # 1920x1080 slides
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

if __package__:
    from . import _pdf
    from ._chromium import ConversionError, find_chromium, run_chromium
    from ._office import OFFICE_SUFFIXES, OfficeUnavailableError, to_pdf
else:  # copied as a standalone folder into a user workspace
    import _pdf
    from _chromium import ConversionError, find_chromium, run_chromium
    from _office import OFFICE_SUFFIXES, OfficeUnavailableError, to_pdf

PAPERS: dict[str, tuple[str, str]] = {
    "a4": ("210mm", "297mm"),
    "letter": ("8.5in", "11in"),
    "legal": ("8.5in", "14in"),
    "a3": ("297mm", "420mm"),
    # A 1920x1080 slide deck printed one slide per page, at the CSS pixel size
    # the slides were designed for.
    "slide": ("1920px", "1080px"),
}
DEFAULT_TIMEOUT = 90

_PAGE_RULE_RE = re.compile(r"@page\b", re.IGNORECASE)
_PAGE_MARKER_RE = re.compile(r"<\w+[^>]*class=\"[^\"]*\b(?:page|sheet|nv-slide)\b")

# Screen-only furniture goes; every .page becomes exactly one sheet. The last
# page loses its forced break so Chromium does not append a blank sheet.
_PRINT_CSS = """<style id="__navin_print__">
  @media print {
    html, body { background: transparent !important; margin: 0 !important; padding: 0 !important; }
    .page, .sheet, .nv-slide { margin: 0 auto !important; box-shadow: none !important; }
    .page, .sheet { break-after: page; page-break-after: always; }
    .page.__navin-last, .sheet.__navin-last { break-after: auto; page-break-after: auto; }
    .nv-slide { break-after: page; page-break-after: always; overflow: hidden; }
    .nv-slide.__navin-last { break-after: auto; page-break-after: auto; }
  }
  *, *::before, *::after { animation: none !important; transition: none !important; }
</style>"""
_MARK_LAST_JS = (
    "<script>(function(){var s=document.querySelectorAll('.page,.sheet,.nv-slide');"
    "if(s.length){s[s.length-1].classList.add('__navin-last');}})();</script>"
)


def _page_rule_css(paper: str, landscape: bool) -> str:
    width, height = PAPERS[paper]
    size = f"{height} {width}" if landscape else f"{width} {height}"
    return f'<style id="__navin_page__">@page {{ size: {size}; margin: 0; }}</style>'


def prepare_print_copy(
    source: Path,
    workdir: Path,
    *,
    paper: str,
    landscape: bool,
) -> tuple[Path, int]:
    """Write the print-ready copy; return it with its number of page sections.

    The template's own ``@page`` rule wins when it has one and A4 portrait was
    asked: it knows the paper it was designed for. Ours fills the gap, and
    takes over when the caller asked for another paper or orientation.
    """
    html = source.read_text(encoding="utf-8", errors="ignore")
    base_tag = f'<base href="{source.resolve().parent.as_uri()}/">'
    head = html.lower().find("<head")
    if head != -1:
        insert_at = html.find(">", head) + 1
        html = html[:insert_at] + base_tag + html[insert_at:]
    else:
        html = base_tag + html
    injection = _PRINT_CSS
    if not _PAGE_RULE_RE.search(html) or paper != "a4" or landscape:
        injection += _page_rule_css(paper, landscape)
    injection += _MARK_LAST_JS
    if "</body>" in html:
        html = html.replace("</body>", f"{injection}</body>", 1)
    else:
        html += injection
    target = workdir / f"__h2pdf_{source.stem}.html"
    target.write_text(html, encoding="utf-8")
    return target, len(_PAGE_MARKER_RE.findall(html))


def _print_html(
    page: Path,
    output: Path,
    *,
    chromium: str,
    timeout: int,
    workdir: Path,
    paper: str,
    landscape: bool,
) -> int:
    prepared, sections = prepare_print_copy(page, workdir, paper=paper, landscape=landscape)
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={output.resolve()}",
        prepared.resolve().as_uri(),
    ]
    viewport = (1920, 1080) if paper == "slide" else None
    result = run_chromium(chromium, args, timeout, viewport)
    if not output.is_file() or output.stat().st_size == 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        tail = detail[-1] if detail else "no output"
        raise ConversionError(f"Chromium printed nothing for {page.name}: {tail}")
    return sections


def _print_deck(
    slides: list[Path],
    output: Path,
    *,
    chromium: str,
    timeout: int,
    workdir: Path,
) -> int:
    """Print each slide on its own 1920x1080 sheet and bind the sheets.

    Slides are printed one file at a time: each carries its own styles and a
    body laid out for a single slide, so stacking their markup into one page
    would let the first slide's rules reach the others. Binding the PDFs keeps
    every slide exactly as its own file renders.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    for index, slide in enumerate(slides, start=1):
        sheet = workdir / f"__h2pdf_slide_{index:02d}.pdf"
        _print_html(
            slide,
            sheet,
            chromium=chromium,
            timeout=timeout,
            workdir=workdir,
            paper="slide",
            landscape=False,
        )
        writer.append(str(sheet))
    # The bound file has no <title> of its own: the first slide names the deck,
    # and the folder does when the slide carries none.
    first = slides[0].read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"<title>(.*?)</title>", first, flags=re.S | re.I)
    title = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
    writer.add_metadata({"/Title": title or slides[0].parent.resolve().name})
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as handle:
        writer.write(handle)
    return len(slides)


def check_qa(folder: Path, force: bool = False, tool: str = "html2pdf") -> None:
    """Refuse to print a document its own quality pass already rejected.

    ``word_qa`` leaves ``qa.json`` beside a document and ``ppt_design deck``
    leaves ``critique.json`` beside the slides; either one failing stops the
    print until the source is fixed, unless the caller passes ``--force``
    because the user accepts the document as is.
    """
    for name, label, fixer in (
        ("qa.json", "document QA", "re-run word_qa"),
        ("critique.json", "deck critique", "re-run ppt_design deck"),
    ):
        gate = folder / name
        if not gate.is_file():
            continue
        try:
            data = json.loads(gate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("pass") is not False:
            continue
        score = data.get("score")
        threshold = data.get("threshold")
        rework = ", ".join(
            str(n) for n in ((data.get("rework") or data.get("notes")) or [])[:6]
        )
        message = (
            f"{gate}: the {label} failed (score {score}, threshold {threshold}"
            + (f": {rework}" if rework else "")
            + ")."
        )
        if force:
            print(f"{tool}: {message} (printed anyway: --force)", file=sys.stderr)
            continue
        raise ConversionError(
            message
            + f" Fix the source, {fixer} until it passes, then print."
            " Use --force only if the user explicitly accepts the document as is."
        )


def inspect_output(output: Path, sections: int | None) -> dict[str, Any]:
    """Read the produced PDF back and list what a reader would notice."""
    warnings: list[str] = []
    info: dict[str, Any] = {"path": str(output)}
    try:
        info.update(_pdf.metadata(output))
        texts = _pdf.page_texts(output)
    except Exception as exc:  # pragma: no cover - a PDF pypdf cannot open
        warnings.append(f"the PDF could not be read back ({exc})")
        info["warnings"] = warnings
        return info
    pages = int(info.get("pages") or 0)
    if sections and pages > sections:
        warnings.append(
            f"{pages} pages for {sections} .page sections: a section runs longer than "
            "its sheet and spills onto an extra page. Shorten it or split it."
        )
    if pages == 0:
        warnings.append("the PDF has no page")
    thin = _pdf.looks_rasterized(texts)
    if thin and len(thin) == pages and pages > 0:
        warnings.append(
            "no page carries selectable text: the document was rendered as pictures. "
            "Text must stay real text (searchable, selectable, translatable)."
        )
    elif thin:
        listed = ", ".join(str(n) for n in thin[:6])
        warnings.append(
            f"page(s) {listed} carry almost no selectable text; if they hold text, "
            "it was rendered as an image"
        )
    if not info.get("title"):
        warnings.append("the PDF has no title: set <title> in the HTML (readers and search show it)")
    info["warnings"] = warnings
    return info


def convert(
    source: str,
    output: Path,
    *,
    paper: str = "a4",
    landscape: bool = False,
    chromium: str | None = None,
    soffice: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    keep_workdir: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Produce *output* from *source* and return the readback report."""
    paper = paper.lower()
    if paper not in PAPERS:
        raise ConversionError(f"Unknown paper '{paper}'. Choose from: {', '.join(PAPERS)}")
    path = Path(source).expanduser()
    if not path.exists():
        raise ConversionError(f"Not found: {source}")
    output = Path(output).expanduser()

    if path.is_file() and path.suffix.lower() in OFFICE_SUFFIXES:
        try:
            produced = to_pdf(path, output.parent, soffice=soffice, timeout=max(timeout, 120))
        except OfficeUnavailableError as exc:
            raise ConversionError(
                f"{exc} A document built from HTML can be printed from its source "
                "instead: html2pdf document.html -o document.pdf"
            ) from exc
        except RuntimeError as exc:
            raise ConversionError(str(exc)) from exc
        if produced.resolve() != output.resolve():
            shutil.move(str(produced), str(output))
        return inspect_output(output, None)

    binary = find_chromium(chromium)
    workdir = keep_workdir or Path(tempfile.mkdtemp(prefix="navin-html2pdf-"))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        if path.is_dir():
            check_qa(path, force=force)
            document = path / "document.html"
            slides = sorted(path.glob("slide_*.html"))
            if document.is_file():
                page = document
            elif slides:
                sections = _print_deck(
                    slides, output, chromium=binary, timeout=timeout, workdir=workdir
                )
                report = inspect_output(output, sections)
                report["paper"] = "slide"
                return report
            else:
                candidates = sorted(path.glob("*.html"))
                if not candidates:
                    raise ConversionError(f"No HTML document in {path}")
                page = candidates[0]
        else:
            page = path
            check_qa(page.parent, force=force)
        sections = _print_html(
            page,
            output,
            chromium=binary,
            timeout=timeout,
            workdir=workdir,
            paper=paper,
            landscape=landscape,
        )
        report = inspect_output(output, sections or None)
        report["paper"] = paper
        return report
    finally:
        if keep_workdir is None:
            shutil.rmtree(workdir, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="html2pdf",
        description="Print an A4 HTML document (or an Office file) to PDF and read it back.",
    )
    parser.add_argument("input", help="document.html, a folder of slides, or a .docx/.pptx/.xlsx")
    parser.add_argument("-o", "--output", required=True, help="Destination .pdf")
    parser.add_argument(
        "--paper", default="a4", choices=sorted(PAPERS), help="Paper size (default a4)"
    )
    parser.add_argument("--landscape", action="store_true", help="Landscape orientation")
    parser.add_argument("--chromium", help="Path to a Chromium binary")
    parser.add_argument("--soffice", help="Path to the LibreOffice binary (Office inputs)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Per-step timeout")
    parser.add_argument("--keep-workdir", help="Keep intermediate files in this folder")
    parser.add_argument(
        "--force", action="store_true", help="Print even when qa.json rejected the document"
    )
    parser.add_argument("--json", action="store_true", help="Print the readback report as JSON")
    args = parser.parse_args(argv)
    try:
        report = convert(
            args.input,
            Path(args.output),
            paper=args.paper,
            landscape=args.landscape,
            chromium=args.chromium,
            soffice=args.soffice,
            timeout=args.timeout,
            keep_workdir=Path(args.keep_workdir).expanduser() if args.keep_workdir else None,
            force=args.force,
        )
    except ConversionError as exc:
        print(f"html2pdf: {exc}", file=sys.stderr)
        return 1
    for warning in report.get("warnings") or []:
        print(f"html2pdf: {warning}", file=sys.stderr)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        size = report.get("page_size_pt")
        paper = f", {size[0]:.0f}x{size[1]:.0f}pt" if size else ""
        print(f"Wrote {args.output} ({report.get('pages', 0)} pages{paper})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
