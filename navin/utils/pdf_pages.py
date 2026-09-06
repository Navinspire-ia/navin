"""Give the model the pages of a PDF that has no text layer.

A scanned contract or a photographed invoice extracts to nothing: ``pypdf``
finds no text, the turn carried ``[File: scan.pdf]`` with an empty body, and
the model answered as if the document were blank. Such a PDF is really a stack
of images, so it is handled like one: the first pages are rendered to PNG and
attached as image blocks a vision model reads directly. When a ``tesseract``
binary is on the machine its OCR text is added too, which also covers the
pages beyond the image budget.

Rendering tries PyMuPDF, then pypdfium2, then the ``pdftoppm`` binary, so the
feature works with whichever the install happens to have.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

# Pages are full image payloads for the model, like sampled video frames.
DEFAULT_MAX_PAGES = 6
MAX_PAGES_CEILING = 12
# Long edge of a rendered page. Higher than a video frame: body text on an A4
# scan must stay legible for the model.
PAGE_MAX_EDGE = 1600

# Fewer non-blank characters per page than this and the text layer is treated
# as absent: a real page of prose has hundreds, a scan has a stray glyph or two.
MIN_TEXT_CHARS_PER_PAGE = 25
# OCR text is cheap compared to images, so every rendered page goes through it.
OCR_MAX_PAGES = MAX_PAGES_CEILING
OCR_MAX_CHARS = 60_000

_RENDER_TIMEOUT_S = 30
_OCR_TIMEOUT_S = 60
_PAGE_MARKER_RE = re.compile(r"--- Page \d+ ---")


@dataclass(slots=True)
class PdfPages:
    """Rendered pages of one PDF.

    ``paths`` is empty when rendering was not possible; ``reason`` then says
    why, in a form safe to show the model.
    """

    source: str
    total_pages: int = 0
    paths: list[str] = field(default_factory=list)
    page_numbers: list[int] = field(default_factory=list)
    ocr_text: str | None = None
    ocr_pages: int = 0
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.paths)


def looks_scanned(text: str, pages_read: int) -> bool:
    """True when *text* extracted from *pages_read* pages is too thin to be a text layer."""
    if pages_read <= 0:
        return False
    body = _PAGE_MARKER_RE.sub("", text or "")
    visible = sum(1 for char in body if not char.isspace())
    return visible < MIN_TEXT_CHARS_PER_PAGE * pages_read


def pages_cache_root() -> Path:
    """Where rendered pages live, next to the sampled video frames."""
    return Path.home() / ".navin" / "cache" / "pdf-pages"


def _page_dir(pdf: Path, base_dir: Path | None) -> Path:
    try:
        stat = pdf.stat()
        fingerprint = f"{pdf.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|{PAGE_MAX_EDGE}"
    except OSError:
        fingerprint = f"{pdf}|{PAGE_MAX_EDGE}"
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", pdf.stem).strip("-.")[:40] or "pdf"
    root = base_dir if base_dir is not None else pages_cache_root()
    return root / f"{stem}-{digest}"


def _target(out_dir: Path, page_number: int) -> Path:
    return out_dir / f"page-{page_number:03d}.png"


def _render_with_pymupdf(pdf: Path, out_dir: Path, page_numbers: list[int]) -> list[Path] | None:
    try:
        import fitz  # type: ignore[import-not-found]
    except Exception:
        return None
    written: list[Path] = []
    try:
        with fitz.open(str(pdf)) as doc:
            for number in page_numbers:
                target = _target(out_dir, number)
                if target.is_file() and target.stat().st_size > 0:
                    written.append(target)
                    continue
                page = doc[number - 1]
                longest = max(page.rect.width, page.rect.height) or 1
                zoom = min(PAGE_MAX_EDGE / longest, 4.0)
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                pix.save(str(target))
                written.append(target)
    except Exception as exc:
        logger.debug("PyMuPDF rendering failed for {}: {}", pdf, exc)
        return None
    return written


def _render_with_pdfium(pdf: Path, out_dir: Path, page_numbers: list[int]) -> list[Path] | None:
    try:
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
    except Exception:
        return None
    written: list[Path] = []
    try:
        doc = pdfium.PdfDocument(str(pdf))
        try:
            for number in page_numbers:
                target = _target(out_dir, number)
                if target.is_file() and target.stat().st_size > 0:
                    written.append(target)
                    continue
                page = doc[number - 1]
                width, height = page.get_size()
                scale = min(PAGE_MAX_EDGE / (max(width, height) or 1), 4.0)
                image = page.render(scale=scale).to_pil()
                image.save(str(target), format="PNG")
                written.append(target)
        finally:
            doc.close()
    except Exception as exc:
        logger.debug("pypdfium2 rendering failed for {}: {}", pdf, exc)
        return None
    return written


def _render_with_pdftoppm(pdf: Path, out_dir: Path, page_numbers: list[int]) -> list[Path] | None:
    binary = shutil.which("pdftoppm")
    if not binary:
        return None
    from navin.utils.proc import no_window_kwargs

    written: list[Path] = []
    for number in page_numbers:
        target = _target(out_dir, number)
        if target.is_file() and target.stat().st_size > 0:
            written.append(target)
            continue
        args = [
            binary,
            "-png",
            "-f",
            str(number),
            "-l",
            str(number),
            "-singlefile",
            "-scale-to",
            str(PAGE_MAX_EDGE),
            str(pdf),
            str(target.with_suffix("")),
        ]
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_RENDER_TIMEOUT_S,
                check=False,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("pdftoppm failed for {} page {}: {}", pdf, number, exc)
            return written or None
        if proc.returncode != 0 or not target.is_file():
            return written or None
        written.append(target)
    return written


_RENDERERS = (_render_with_pymupdf, _render_with_pdfium, _render_with_pdftoppm)


def _count_pages(pdf: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(pdf, strict=False).pages)


def render_pdf_pages(
    path: str | Path,
    *,
    total_pages: int | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    first_page: int = 1,
    base_dir: Path | None = None,
) -> PdfPages:
    """Render up to *max_pages* pages of *path*, from *first_page*, as PNG files.

    Never raises: a PDF that cannot be rendered degrades to a reason string the
    caller shows the model instead of pretending the document was read.
    """
    pdf = Path(path)
    result = PdfPages(source=str(pdf))
    if not pdf.is_file():
        result.reason = "file not found"
        return result
    if total_pages is None:
        try:
            total_pages = _count_pages(pdf)
        except Exception as exc:
            result.reason = f"unreadable PDF: {exc}"
            return result
    result.total_pages = total_pages
    if total_pages <= 0:
        result.reason = "the PDF has no pages"
        return result

    start = max(1, min(int(first_page), total_pages))
    count = max(1, min(int(max_pages), MAX_PAGES_CEILING, total_pages - start + 1))
    page_numbers = list(range(start, start + count))
    out_dir = _page_dir(pdf, base_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.reason = f"cannot write pages: {exc}"
        return result

    for renderer in _RENDERERS:
        written = renderer(pdf, out_dir, page_numbers)
        if written:
            result.paths = [str(p) for p in written]
            result.page_numbers = page_numbers[: len(written)]
            return result
    result.reason = (
        "no PDF renderer available (install PyMuPDF or pypdfium2, or poppler's pdftoppm)"
    )
    return result


def find_tesseract() -> str | None:
    """Path of the ``tesseract`` OCR binary when one is installed."""
    return shutil.which("tesseract")


def ocr_page_images(
    paths: list[str], page_numbers: list[int], *, tesseract: str | None = None
) -> tuple[str | None, int]:
    """Plain text OCR of rendered pages: ``(text, pages_covered)``.

    ``(None, 0)`` when no engine is available or nothing was recognized.
    """
    binary = tesseract or find_tesseract()
    if not binary or not paths:
        return None, 0
    from navin.utils.proc import no_window_kwargs

    parts: list[str] = []
    used = 0
    covered = 0
    for image, number in list(zip(paths, page_numbers, strict=False))[:OCR_MAX_PAGES]:
        try:
            proc = subprocess.run(
                [binary, image, "stdout"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_OCR_TIMEOUT_S,
                check=False,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("tesseract failed on {}: {}", image, exc)
            break
        text = (proc.stdout or "").strip() if proc.returncode == 0 else ""
        covered = number
        if not text:
            continue
        chunk = f"--- Page {number} (OCR) ---\n{text}"
        if used + len(chunk) > OCR_MAX_CHARS:
            parts.append(chunk[: max(0, OCR_MAX_CHARS - used)] + "\n... (OCR truncated)")
            break
        parts.append(chunk)
        used += len(chunk)
    if not parts:
        return None, 0
    return "\n\n".join(parts), covered


def describe_pdf_pages(pages: PdfPages) -> str:
    """Note handed to the model with the page images (or the reason there are none)."""
    name = Path(pages.source).name
    total = f"{pages.total_pages} page{'s' if pages.total_pages != 1 else ''}"
    if not pages.ok:
        return (
            f"[pdf: {name} - {total}, no text layer (scanned document), "
            f"pages could not be rendered: {pages.reason}. Ask the user for a text "
            "version rather than guessing the content.]"
        )
    first, last = pages.page_numbers[0], pages.page_numbers[-1]
    shown = f"page {first}" if first == last else f"pages {first}-{last}"
    parts = [
        f"[pdf: {name} - {total}, no text layer (scanned document). "
        f"{shown.capitalize()} attached as images in reading order; read the text from them."
    ]
    covered = max(last, pages.ocr_pages if pages.ocr_text else 0)
    if pages.ocr_text:
        parts.append(f" OCR text for pages {first}-{pages.ocr_pages} follows.")
    if covered < pages.total_pages:
        parts.append(f" Pages {covered + 1}-{pages.total_pages} are not shown.")
    note = "".join(parts) + "]"
    if pages.ocr_text:
        note = f"{note}\n{pages.ocr_text}"
    return note


def expand_scanned_pdf(
    path: str | Path,
    *,
    total_pages: int | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    first_page: int = 1,
    base_dir: Path | None = None,
    ocr: bool = True,
) -> PdfPages:
    """Render a scanned PDF and OCR it when an engine exists. Never raises.

    Only the first *max_pages* pages travel as images; with an OCR engine,
    more pages are rendered so their text can travel cheaply instead.
    """
    tesseract = find_tesseract() if ocr else None
    render_count = max(max_pages, OCR_MAX_PAGES) if tesseract else max_pages
    pages = render_pdf_pages(
        path,
        total_pages=total_pages,
        max_pages=render_count,
        first_page=first_page,
        base_dir=base_dir,
    )
    if not pages.ok:
        return pages
    if tesseract:
        pages.ocr_text, pages.ocr_pages = ocr_page_images(
            pages.paths, pages.page_numbers, tesseract=tesseract
        )
    keep = max(1, min(int(max_pages), MAX_PAGES_CEILING))
    pages.paths = pages.paths[:keep]
    pages.page_numbers = pages.page_numbers[:keep]
    return pages
