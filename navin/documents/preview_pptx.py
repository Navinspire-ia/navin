"""Render a PPTX to PNG images for visual inspection.

With LibreOffice on the machine the deck is laid out for real (the render
PowerPoint's reader will see: fonts, wrapping, charts) and each slide becomes a
PNG. Without it, the fallback is approximate on purpose: it draws the backdrop,
the shapes, the pictures and the text boxes with their real geometry, font
sizes and colors. That is enough to catch what matters after a conversion - a
decor that does not cover the slide, text over text, an empty block, white on
white - and the command says which of the two it produced.

Usage::

    <navin-python> .navin/resources/tools/preview_pptx.py deck.pptx previews/
"""

from __future__ import annotations

import os
import sys
from contextlib import suppress
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

if __package__:
    from . import _pdf
    from ._office import OfficeUnavailableError, to_pdf
else:  # copied as a standalone folder into a user workspace
    import _pdf
    from _office import OfficeUnavailableError, to_pdf

SCALE = 0.5
_FONT_DIRS = (
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts",
    # Without these the fallback is a fixed-size bitmap that ignores the
    # requested size, which turns the preview into an image that cannot show
    # the overlap and overflow it exists to reveal.
    os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts"),
    "/System/Library/Fonts/Supplemental",
    "/System/Library/Fonts",
    "/Library/Fonts",
)
_FONT_NAMES_BOLD = (
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
    "arialbd.ttf",
    "segoeuib.ttf",
    "calibrib.ttf",
    "Arial Bold.ttf",
    "Helvetica.ttc",
)
_FONT_NAMES_REGULAR = (
    "DejaVuSans.ttf",
    "LiberationSans-Regular.ttf",
    "arial.ttf",
    "segoeui.ttf",
    "calibri.ttf",
    "Arial.ttf",
    "Helvetica.ttc",
)


def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont:
    names = _FONT_NAMES_BOLD if bold else _FONT_NAMES_REGULAR
    for folder in _FONT_DIRS:
        for name in names:
            candidate = Path(folder) / name
            if candidate.is_file():
                with suppress(OSError):
                    return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _color(fill, default=(255, 255, 255)) -> tuple[int, int, int]:
    try:
        rgb = fill.fore_color.rgb
        return (rgb[0], rgb[1], rgb[2])
    except Exception:
        return default


def _px(value) -> int:
    return int(round(Emu(int(value)).inches * 144 * SCALE))


def render(deck: Path, folder: Path) -> list[Path]:
    """Real render through LibreOffice when available, approximation otherwise."""
    written, _ = render_best(deck, folder)
    return written


def render_best(deck: Path, folder: Path) -> tuple[list[Path], str]:
    """Return the page images and the mode used (``real`` or ``approximate``)."""
    folder.mkdir(parents=True, exist_ok=True)
    try:
        pdf = to_pdf(deck, folder / "_render")
    except OfficeUnavailableError:
        return render_approximate(deck, folder), "approximate"
    except RuntimeError as exc:
        print(f"preview_pptx: {exc}; falling back to the approximation", file=sys.stderr)
        return render_approximate(deck, folder), "approximate"
    try:
        pages = _pdf.rasterize(pdf, folder, prefix=deck.stem, max_pages=200, max_edge=1920)
    except RuntimeError:
        return render_approximate(deck, folder), "approximate"
    return pages, "real"


def render_approximate(deck: Path, folder: Path) -> list[Path]:
    """Draw the slides from their shape geometry, without an office suite."""
    presentation = Presentation(str(deck))
    width = _px(presentation.slide_width)
    height = _px(presentation.slide_height)
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, slide in enumerate(presentation.slides, start=1):
        canvas = Image.new("RGB", (width, height), "white")
        paper: tuple[int, int, int] = (255, 255, 255)
        try:
            if slide.background.fill.type is not None:
                paper = _color(slide.background.fill)
                canvas.paste(paper, (0, 0, width, height))
        except Exception:
            pass
        draw = ImageDraw.Draw(canvas)
        for shape in slide.shapes:
            box = (_px(shape.left), _px(shape.top), _px(shape.width), _px(shape.height))
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                _draw_picture(canvas, shape, box)
                continue
            if getattr(shape, "has_chart", False) and shape.has_chart:
                _draw_chart(draw, shape.chart, box, paper=paper)
                continue
            if shape.has_text_frame and shape.text_frame.text.strip():
                _draw_shape(draw, shape, box)
                _draw_text(draw, shape, box)
                continue
            _draw_shape(draw, shape, box)
        target = folder / f"{deck.stem}-{index:02d}.png"
        canvas.save(target)
        written.append(target)
    return written


def _draw_picture(canvas: Image.Image, shape, box: tuple[int, int, int, int]) -> None:
    left, top, width, height = box
    if width < 1 or height < 1:
        return
    try:
        from io import BytesIO

        with Image.open(BytesIO(shape.image.blob)) as picture:
            resized = picture.convert("RGB").resize((width, height))
        canvas.paste(resized, (left, top))
    except Exception:
        ImageDraw.Draw(canvas).rectangle(
            [left, top, left + width, top + height], outline=(200, 60, 60)
        )


def _draw_shape(draw: ImageDraw.ImageDraw, shape, box: tuple[int, int, int, int]) -> None:
    left, top, width, height = box
    try:
        fill_type = shape.fill.type
    except Exception:
        fill_type = None
    fill = _color(shape.fill, None) if fill_type == 1 else None
    outline = None
    try:
        if shape.line.fill.type == 1:
            rgb = shape.line.color.rgb
            outline = (rgb[0], rgb[1], rgb[2])
    except Exception:
        outline = None
    if fill or outline:
        draw.rectangle([left, top, left + width, top + height], fill=fill, outline=outline)


def _series_color(series, index: int) -> tuple[int, int, int]:
    palette = [(68, 114, 196), (237, 125, 49), (165, 165, 165), (255, 192, 0), (91, 155, 213)]
    try:
        rgb = series.format.fill.fore_color.rgb
        return (rgb[0], rgb[1], rgb[2])
    except Exception:
        pass
    try:
        rgb = series.format.line.color.rgb
        return (rgb[0], rgb[1], rgb[2])
    except Exception:
        return palette[index % len(palette)]


def _draw_chart(
    draw: ImageDraw.ImageDraw,
    chart,
    box: tuple[int, int, int, int],
    *,
    paper: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Sketch a native chart from its data: bars, lines or a ring, plus labels.

    The point is to show the slide is not empty and the proportions are right;
    PowerPoint's own rendering comes with LibreOffice.
    """
    left, top, width, height = box
    if width < 8 or height < 8:
        return
    try:
        plot = chart.plots[0]
        kind = str(chart.chart_type)
        categories = [str(c) for c in plot.categories]
        series = list(plot.series)
    except Exception:
        draw.rectangle([left, top, left + width, top + height], outline=(120, 120, 120))
        return
    values = [[float(v) if v is not None else 0.0 for v in s.values] for s in series]
    if not values or not any(values):
        draw.rectangle([left, top, left + width, top + height], outline=(120, 120, 120))
        return
    try:
        rgb = chart.font.color.rgb
        ink = (rgb[0], rgb[1], rgb[2])
    except Exception:
        ink = (60, 60, 60)
    label_font = _font(max(8, int(14 * SCALE)), False)
    label_height = int(max(8, int(14 * SCALE)) * 1.4)

    if "PIE" in kind or "DOUGHNUT" in kind:
        total = sum(values[0]) or 1.0
        size = min(width, height - label_height)
        cx, cy = left + width // 2, top + size // 2
        bbox = [cx - size // 2, cy - size // 2, cx + size // 2, cy + size // 2]
        angle = -90.0
        for index, value in enumerate(values[0]):
            sweep = 360.0 * value / total
            try:
                rgb = series[0].points[index].format.fill.fore_color.rgb
                color = (rgb[0], rgb[1], rgb[2])
            except Exception:
                color = _series_color(series[0], index)
            draw.pieslice(bbox, angle, angle + sweep, fill=color)
            angle += sweep
        if "DOUGHNUT" in kind:
            hole = size // 4
            draw.ellipse([cx - hole, cy - hole, cx + hole, cy + hole], fill=paper)
        legend = "  ".join(f"{c} {100 * v / total:.0f}%" for c, v in zip(categories, values[0], strict=False))
        draw.text((left, top + size + 4), legend[:120], font=label_font, fill=ink)
        return

    plot_top = top
    plot_height = max(4, height - label_height)
    peak = max(max(v) for v in values) or 1.0
    count = max(len(categories), max(len(v) for v in values), 1)
    slot = width / count
    if "LINE" in kind or "AREA" in kind or "RADAR" in kind:
        for s_index, row in enumerate(values):
            color = _series_color(series[s_index], s_index)
            points = [
                (int(left + slot * (i + 0.5)), int(plot_top + plot_height - plot_height * v / peak))
                for i, v in enumerate(row)
            ]
            if len(points) > 1:
                draw.line(points, fill=color, width=max(2, int(3 * SCALE)))
            for x, y in points:
                r = max(2, int(4 * SCALE))
                draw.ellipse([x - r, y - r, x + r, y + r], fill=color)
    else:
        groups = max(len(values), 1)
        bar = max(2, int(slot * 0.7 / groups))
        for s_index, row in enumerate(values):
            color = _series_color(series[s_index], s_index)
            for i, v in enumerate(row):
                x0 = int(left + slot * i + slot * 0.15 + bar * s_index)
                y0 = int(plot_top + plot_height - plot_height * v / peak)
                draw.rectangle([x0, y0, x0 + bar - 1, plot_top + plot_height], fill=color)
    for i, category in enumerate(categories[:count]):
        x = int(left + slot * (i + 0.5))
        text = category[:14]
        text_width = draw.textlength(text, font=label_font)
        draw.text((x - text_width / 2, plot_top + plot_height + 2), text, font=label_font, fill=ink)


def _draw_text(draw: ImageDraw.ImageDraw, shape, box: tuple[int, int, int, int]) -> None:
    left, top, width, _ = box
    cursor = top
    for paragraph in shape.text_frame.paragraphs:
        text = "".join(run.text for run in paragraph.runs)
        if not text.strip():
            cursor += 6
            continue
        first = paragraph.runs[0]
        points = first.font.size.pt if first.font.size else 12
        size = max(6, int(round(points * 2 * SCALE)))
        font = _font(size, bool(first.font.bold))
        try:
            rgb = first.font.color.rgb
            color = (rgb[0], rgb[1], rgb[2])
        except Exception:
            color = (30, 30, 30)
        line_height = int(size * 1.25)
        for line in _wrap(draw, text, font, width if shape.text_frame.word_wrap else 10**6):
            draw.text((left, cursor), line, font=font, fill=color)
            cursor += line_height


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, limit: int) -> list[str]:
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= limit or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    written, mode = render_best(Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser())
    if not written:
        print("preview_pptx: the deck has no slide", file=sys.stderr)
        return 1
    print(f"Wrote {len(written)} images in {written[0].parent} ({mode} render)")
    if mode == "approximate":
        print(
            "preview_pptx: LibreOffice is not installed, so the images are drawn from the "
            "shapes' geometry (approximate type). Install LibreOffice for a faithful render.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
