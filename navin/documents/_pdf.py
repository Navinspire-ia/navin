"""PDF inspection and rasterization shared by the document tools.

Reading a PDF back is how a generated document gets checked the way a reader
will see it: how many pages came out, whether the text is real text or a
picture of text, whether a page is blank, and what the pages look like once
rendered. Rendering tries PyMuPDF, then pypdfium2, then poppler's ``pdftoppm``
so it works with whatever the installation happens to have; the callers say so
when none of the three is present instead of failing silently.

Everything here runs from a copied folder of scripts as well as from the
package: no Navin import.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

# Long edge of a rendered page. Body text on an A4 page stays legible for a
# vision model at this size without producing a multi-megabyte image.
PAGE_MAX_EDGE = 1600
_RENDER_TIMEOUT_S = 60
# Fewer visible characters than this on a page and its text layer is treated as
# missing: real prose has hundreds, a rasterized page has none or a stray glyph.
MIN_TEXT_CHARS_PER_PAGE = 20


def _no_window_kwargs() -> dict[str, Any]:
    try:
        from navin.utils.proc import no_window_kwargs
    except Exception:
        flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flag} if flag else {}
    return no_window_kwargs()


def page_count(pdf: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(pdf), strict=False).pages)


def page_texts(pdf: Path, limit: int | None = None) -> list[str]:
    """Text of each page in order (``""`` for a page without a text layer)."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf), strict=False)
    texts: list[str] = []
    for index, page in enumerate(reader.pages):
        if limit is not None and index >= limit:
            break
        try:
            texts.append(page.extract_text() or "")
        except Exception:  # pragma: no cover - a damaged page stays a blank one
            texts.append("")
    return texts


def metadata(pdf: Path) -> dict[str, Any]:
    """Title, author and page size (points) of the document."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf), strict=False)
    info = reader.metadata or {}
    out: dict[str, Any] = {
        "title": str(info.get("/Title") or "").strip() or None,
        "author": str(info.get("/Author") or "").strip() or None,
        "pages": len(reader.pages),
    }
    if reader.pages:
        box = reader.pages[0].mediabox
        out["page_size_pt"] = (round(float(box.width), 1), round(float(box.height), 1))
    return out


def looks_rasterized(texts: list[str]) -> list[int]:
    """1-based numbers of pages whose text layer is too thin to be text."""
    thin: list[int] = []
    for index, text in enumerate(texts, start=1):
        visible = sum(1 for char in text if not char.isspace())
        if visible < MIN_TEXT_CHARS_PER_PAGE:
            thin.append(index)
    return thin


def _target(out_dir: Path, prefix: str, number: int) -> Path:
    return out_dir / f"{prefix}-{number:02d}.png"


def _render_with_pymupdf(
    pdf: Path, out_dir: Path, numbers: list[int], prefix: str, max_edge: int
) -> list[Path] | None:
    try:
        import fitz  # type: ignore[import-not-found]
    except Exception:
        return None
    written: list[Path] = []
    try:
        with fitz.open(str(pdf)) as document:
            for number in numbers:
                page = document[number - 1]
                longest = max(page.rect.width, page.rect.height) or 1
                zoom = min(max_edge / longest, 4.0)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                target = _target(out_dir, prefix, number)
                pixmap.save(str(target))
                written.append(target)
    except Exception:
        return None
    return written


def _render_with_pdfium(
    pdf: Path, out_dir: Path, numbers: list[int], prefix: str, max_edge: int
) -> list[Path] | None:
    try:
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
    except Exception:
        return None
    written: list[Path] = []
    try:
        document = pdfium.PdfDocument(str(pdf))
        try:
            for number in numbers:
                page = document[number - 1]
                width, height = page.get_size()
                scale = min(max_edge / (max(width, height) or 1), 4.0)
                image = page.render(scale=scale).to_pil()
                target = _target(out_dir, prefix, number)
                image.save(str(target), format="PNG")
                written.append(target)
        finally:
            document.close()
    except Exception:
        return None
    return written


def _render_with_pdftoppm(
    pdf: Path, out_dir: Path, numbers: list[int], prefix: str, max_edge: int
) -> list[Path] | None:
    binary = shutil.which("pdftoppm")
    if not binary:
        return None
    written: list[Path] = []
    for number in numbers:
        target = _target(out_dir, prefix, number)
        command = [
            binary,
            "-png",
            "-f",
            str(number),
            "-l",
            str(number),
            "-singlefile",
            "-scale-to",
            str(max_edge),
            str(pdf),
            str(target.with_suffix("")),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=_RENDER_TIMEOUT_S,
                check=False,
                **_no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError):
            return written or None
        if result.returncode != 0 or not target.is_file():
            return written or None
        written.append(target)
    return written


_RENDERERS = (_render_with_pymupdf, _render_with_pdfium, _render_with_pdftoppm)


def renderer_available() -> bool:
    """Whether at least one of the three PDF rasterizers is installed."""
    for module in ("fitz", "pypdfium2"):
        try:
            __import__(module)
            return True
        except Exception:
            continue
    return shutil.which("pdftoppm") is not None


def rasterize(
    pdf: Path,
    out_dir: Path,
    *,
    prefix: str | None = None,
    max_pages: int = 60,
    max_edge: int = PAGE_MAX_EDGE,
) -> list[Path]:
    """Render the first *max_pages* pages of *pdf* to PNG files.

    Raises :class:`RuntimeError` when the machine has no rasterizer at all.
    """
    pdf = Path(pdf)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = page_count(pdf)
    numbers = list(range(1, min(total, max_pages) + 1))
    if not numbers:
        return []
    name = prefix or pdf.stem
    for renderer in _RENDERERS:
        written = renderer(pdf, out_dir, numbers, name, max_edge)
        if written:
            return written
    raise RuntimeError(
        "no PDF rasterizer available: install PyMuPDF (pip install pymupdf), "
        "pypdfium2, or poppler's pdftoppm"
    )


def contact_sheet(
    images: list[Path],
    output: Path,
    *,
    columns: int = 3,
    cell_width: int = 640,
    labels: list[str] | None = None,
) -> Path | None:
    """Tile page images into one labelled grid.

    One picture the agent reads in a single look, instead of forty files opened
    one by one; the per-page images stay beside it for a closer read.
    """
    if not images:
        return None
    from PIL import Image, ImageDraw

    columns = max(1, min(columns, len(images)))
    thumbs: list[Image.Image] = []
    for path in images:
        with Image.open(path) as picture:
            picture = picture.convert("RGB")
            ratio = cell_width / max(picture.width, 1)
            thumbs.append(
                picture.resize((cell_width, max(1, int(round(picture.height * ratio)))))
            )
    cell_height = max(thumb.height for thumb in thumbs)
    label_height = 28
    gap = 16
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (
            columns * cell_width + (columns + 1) * gap,
            rows * (cell_height + label_height) + (rows + 1) * gap,
        ),
        (236, 238, 241),
    )
    draw = ImageDraw.Draw(sheet)
    for index, thumb in enumerate(thumbs):
        column, row = index % columns, index // columns
        x = gap + column * (cell_width + gap)
        y = gap + row * (cell_height + label_height + gap)
        draw.rectangle([x - 1, y - 1, x + cell_width, y + thumb.height], outline=(200, 204, 210))
        sheet.paste(thumb, (x, y))
        text = labels[index] if labels and index < len(labels) else f"{index + 1}"
        draw.text((x + 4, y + thumb.height + 6), text, fill=(60, 64, 72))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    return output
