#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Build a 16:9 PowerPoint deck in the Navin theme from a JSON spec.

Usage:
    pptx_build.py --spec deck.json --out deck.pptx [--template base.pptx] [--json]
    pptx_build.py --spec-example            # prints a complete example spec

Spec shape:
    {"title": "...", "language": "fr", "theme": {"accent": "lime"},
     "slides": [ {"type": "title", ...}, ... ]}

Slide types: title, agenda, section, bullets, two_column, comparison,
big_number, quote, image, table, chart, closing. See --spec-example.

Exit codes: 0 ok, 2 spec or I/O error.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.exc import PythonPptxError
from pptx.util import Emu, Inches, Pt

# Errors a malformed spec, a missing asset or a bad template can raise; reported as JSON, exit 2.
CLI_ERRORS = (OSError, ValueError, KeyError, TypeError, IndexError, zipfile.BadZipFile, PythonPptxError)

PALETTE = {
    "ink": "17171A",
    "graphite": "121214",
    "paper": "F4F3EE",
    "cream": "FAF9F6",
    "lime": "C6E88A",
    "teal": "0E7490",
    "sky": "0EA5E9",
    "leaf": "6FBF6A",
    "violet": "7C3AED",
    "coral": "FF5F57",
    "amber": "FEBC2E",
}
SERIES_COLORS = ("teal", "lime", "sky", "leaf", "violet", "amber", "coral")
FONT_TEXT = "Inter"
FONT_SERIF = "Lora"
FONT_MONO = "IBM Plex Mono"

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
MARGIN = Inches(0.7)
CONTENT_W = SLIDE_W - 2 * MARGIN
TITLE_TOP = Inches(0.55)
BODY_TOP = Inches(1.7)
BODY_H = SLIDE_H - BODY_TOP - Inches(0.8)

TYPE_SCALE = {"display": 54, "h1": 36, "h2": 28, "body": 20, "small": 14, "caption": 12}

EXAMPLE_SPEC = {
    "title": "Quarterly review",
    "language": "en",
    "author": "Navin",
    "theme": {"accent": "lime", "dark_sections": True},
    "slides": [
        {"type": "title", "title": "Quarterly review", "subtitle": "Q3 results and Q4 plan", "footer": "October 2026"},
        {"type": "agenda", "title": "Agenda", "items": ["Results", "Customers", "Roadmap", "Decisions"]},
        {"type": "section", "title": "Results", "number": "01"},
        {"type": "bullets", "title": "Highlights", "bullets": ["Revenue up 18 percent", "Churn down to 2.1 percent", ["Enterprise segment leads", "Two new regions opened"], "Hiring on plan"], "notes": "Keep it to one minute."},
        {"type": "two_column", "title": "What worked and what did not", "left_title": "Worked", "left": ["Self-serve onboarding", "Partner channel"], "right_title": "Did not", "right": ["Outbound in DACH", "Legacy migration tool"]},
        {"type": "comparison", "title": "Options for Q4", "headers": ["", "Option A", "Option B"], "rows": [["Cost", "Low", "High"], ["Time", "6 weeks", "3 weeks"], ["Risk", "Medium", "Low"]]},
        {"type": "big_number", "title": "Net revenue retention", "value": "118%", "label": "trailing twelve months", "trend": "+6 pts vs last year"},
        {"type": "quote", "quote": "The new dashboard removed a full day of reporting every week.", "author": "Head of Finance, pilot customer"},
        {"type": "image", "title": "Product shot", "image": "assets/dashboard.png", "caption": "Dashboard, October release"},
        {"type": "table", "title": "Pipeline by region", "headers": ["Region", "Deals", "Value"], "rows": [["EMEA", "24", "1.2 M"], ["Americas", "18", "0.9 M"], ["APAC", "7", "0.3 M"]]},
        {"type": "chart", "title": "Revenue by quarter", "chart": "column", "categories": ["Q1", "Q2", "Q3"], "series": {"2025": [1.1, 1.3, 1.5], "2026": [1.4, 1.6, 1.9]}, "number_format": "0.0", "legend": True},
        {"type": "closing", "title": "Thank you", "subtitle": "Questions and next steps", "contact": "team@example.com"},
    ],
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def rgb(name_or_hex: str) -> RGBColor:
    value = PALETTE.get(name_or_hex.lower(), name_or_hex.lstrip("#"))
    return RGBColor.from_string(value.upper())


class Theme:
    def __init__(self, spec: dict):
        self.accent = spec.get("accent", "lime")
        self.dark_sections = bool(spec.get("dark_sections", True))
        self.font = spec.get("font", FONT_TEXT)
        self.serif = spec.get("serif", FONT_SERIF)


def _fill(shape, color: str) -> None:
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(color)
    shape.line.fill.background()


def _background(slide, color: str) -> None:
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = rgb(color)


def _textbox(slide, left, top, width, height, text, size=TYPE_SCALE["body"], color="ink", bold=False, font=FONT_TEXT, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, italic=False):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.05)
    lines = text if isinstance(text, list) else [text]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = str(line)
        _style_run(run, size, color, bold, font, italic)
    return box


def _style_run(run, size, color, bold=False, font=FONT_TEXT, italic=False):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.name = font
    run.font.color.rgb = rgb(color)


def _title(slide, theme: Theme, text: str, color="ink"):
    _textbox(slide, MARGIN, TITLE_TOP, CONTENT_W, Inches(0.9), text, TYPE_SCALE["h1"], color, bold=True, font=theme.font, anchor=MSO_ANCHOR.MIDDLE)
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, MARGIN, TITLE_TOP + Inches(0.95), Inches(1.0), Inches(0.06))
    _fill(bar, theme.accent)


def _footer(slide, theme: Theme, index: int, total: int, label: str, color="ink"):
    _textbox(slide, MARGIN, SLIDE_H - Inches(0.55), CONTENT_W - Inches(1), Inches(0.4), label, TYPE_SCALE["caption"], color, font=theme.font)
    _textbox(slide, SLIDE_W - MARGIN - Inches(1), SLIDE_H - Inches(0.55), Inches(1), Inches(0.4), f"{index} / {total}", TYPE_SCALE["caption"], color, font=theme.font, align=PP_ALIGN.RIGHT)


def _bullets(slide, theme: Theme, left, top, width, height, items, size=TYPE_SCALE["body"], color="ink"):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    first = True

    def add(text, level):
        nonlocal first
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.level = level
        p.space_after = Pt(8)
        run = p.add_run()
        marker = "-  " if level == 0 else "   -  "
        run.text = marker + str(text)
        _style_run(run, size - 3 * level, color, font=theme.font)

    for item in items:
        if isinstance(item, list):
            for sub in item:
                add(sub, 1)
        else:
            add(item, 0)
    return box


def _table(slide, theme: Theme, left, top, width, height, headers, rows, size=TYPE_SCALE["small"]):
    n_rows, n_cols = len(rows) + 1, len(headers)
    shape = slide.shapes.add_table(n_rows, n_cols, left, top, width, height)
    table = shape.table
    for c, header in enumerate(headers):
        cell = table.cell(0, c)
        cell.text = str(header)
        cell.fill.solid()
        cell.fill.fore_color.rgb = rgb("ink")
        for p in cell.text_frame.paragraphs:
            for r in p.runs:
                _style_run(r, size, "paper", bold=True, font=theme.font)
    for r_i, row in enumerate(rows, start=1):
        for c, value in enumerate(row):
            cell = table.cell(r_i, c)
            cell.text = str(value)
            cell.fill.solid()
            cell.fill.fore_color.rgb = rgb("cream" if r_i % 2 == 0 else "paper")
            for p in cell.text_frame.paragraphs:
                for r in p.runs:
                    _style_run(r, size, "ink", font=theme.font)
    return shape


CHART_TYPES = {
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "stacked": XL_CHART_TYPE.COLUMN_STACKED,
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "pie": XL_CHART_TYPE.PIE,
    "doughnut": XL_CHART_TYPE.DOUGHNUT,
    "area": XL_CHART_TYPE.AREA,
}


def _chart(slide, theme: Theme, left, top, width, height, spec: dict):
    kind = CHART_TYPES.get(str(spec.get("chart", "column")).lower(), XL_CHART_TYPE.COLUMN_CLUSTERED)
    data = CategoryChartData()
    data.categories = [str(c) for c in spec.get("categories", [])]
    series = spec.get("series", {})
    for name, values in series.items():
        data.add_series(str(name), [float(v) if v is not None else None for v in values])
    frame = slide.shapes.add_chart(kind, left, top, width, height, data)
    chart = frame.chart
    chart.has_legend = bool(spec.get("legend", len(series) > 1))
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(TYPE_SCALE["small"])
        chart.legend.font.name = theme.font
    chart.font.name = theme.font
    chart.font.size = Pt(TYPE_SCALE["small"])
    chart.font.color.rgb = rgb("ink")
    plot = chart.plots[0]
    if kind in (XL_CHART_TYPE.PIE, XL_CHART_TYPE.DOUGHNUT):
        plot.has_data_labels = True
        plot.data_labels.number_format = spec.get("number_format", "0%")
        plot.data_labels.number_format_is_linked = False
        points = plot.series[0].points
        for i in range(len(data.categories)):
            points[i].format.fill.solid()
            points[i].format.fill.fore_color.rgb = rgb(SERIES_COLORS[i % len(SERIES_COLORS)])
    else:
        for i, s in enumerate(plot.series):
            color = rgb(SERIES_COLORS[i % len(SERIES_COLORS)])
            if kind == XL_CHART_TYPE.LINE_MARKERS:
                s.format.line.color.rgb = color
                s.format.line.width = Pt(2.5)
                s.smooth = False
            else:
                s.format.fill.solid()
                s.format.fill.fore_color.rgb = color
        if spec.get("data_labels", False):
            plot.has_data_labels = True
            plot.data_labels.number_format = spec.get("number_format", "General")
            plot.data_labels.number_format_is_linked = False
        # Column, bar and line charts always carry a value axis (pie and doughnut are
        # handled above), so no guard is needed here.
        chart.value_axis.has_major_gridlines = True
        chart.value_axis.major_gridlines.format.line.color.rgb = rgb("paper")
        chart.value_axis.tick_labels.number_format = spec.get("number_format", "General")
        chart.value_axis.tick_labels.number_format_is_linked = False
    return frame


# --------------------------------------------------------------------------- #
# slide builders
# --------------------------------------------------------------------------- #


def build_title(slide, theme, s, ctx):
    _background(slide, "ink")
    strip = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.35), SLIDE_H)
    _fill(strip, theme.accent)
    _textbox(slide, MARGIN, Inches(2.4), CONTENT_W, Inches(1.6), s.get("title", ""), TYPE_SCALE["display"], "paper", bold=True, font=theme.font, anchor=MSO_ANCHOR.BOTTOM)
    if s.get("subtitle"):
        _textbox(slide, MARGIN, Inches(4.1), CONTENT_W, Inches(1.0), s["subtitle"], TYPE_SCALE["h2"], theme.accent, font=theme.font)
    if s.get("footer"):
        _textbox(slide, MARGIN, SLIDE_H - Inches(0.9), CONTENT_W, Inches(0.5), s["footer"], TYPE_SCALE["small"], "paper", font=theme.font)


def build_agenda(slide, theme, s, ctx):
    _title(slide, theme, s.get("title", "Agenda"))
    items = s.get("items", [])
    row_h = min(Inches(0.9), int(BODY_H / max(1, len(items))))
    for i, item in enumerate(items):
        top = BODY_TOP + i * row_h
        badge = slide.shapes.add_shape(MSO_SHAPE.OVAL, MARGIN, top + Inches(0.1), Inches(0.55), Inches(0.55))
        _fill(badge, theme.accent)
        tf = badge.text_frame
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        run = tf.paragraphs[0].add_run()
        run.text = f"{i + 1}"
        _style_run(run, TYPE_SCALE["small"], "ink", bold=True, font=theme.font)
        _textbox(slide, MARGIN + Inches(0.8), top, CONTENT_W - Inches(0.8), row_h, str(item), TYPE_SCALE["h2"] - 4, "ink", font=theme.font, anchor=MSO_ANCHOR.MIDDLE)


def build_section(slide, theme, s, ctx):
    dark = theme.dark_sections
    _background(slide, "graphite" if dark else "paper")
    fg = "paper" if dark else "ink"
    if s.get("number"):
        _textbox(slide, MARGIN, Inches(1.8), CONTENT_W, Inches(1.2), str(s["number"]), TYPE_SCALE["display"] + 20, theme.accent, bold=True, font=FONT_MONO)
    _textbox(slide, MARGIN, Inches(3.3), CONTENT_W, Inches(1.4), s.get("title", ""), TYPE_SCALE["display"] - 8, fg, bold=True, font=theme.font)
    if s.get("subtitle"):
        _textbox(slide, MARGIN, Inches(4.7), CONTENT_W, Inches(0.8), s["subtitle"], TYPE_SCALE["h2"] - 4, fg, font=theme.font)


def build_bullets(slide, theme, s, ctx):
    _title(slide, theme, s.get("title", ""))
    _bullets(slide, theme, MARGIN, BODY_TOP, CONTENT_W, BODY_H, s.get("bullets", []))


def build_two_column(slide, theme, s, ctx):
    _title(slide, theme, s.get("title", ""))
    col_w = int((CONTENT_W - Inches(0.5)) / 2)
    for i, side in enumerate(("left", "right")):
        left = MARGIN + i * (col_w + Inches(0.5))
        top = BODY_TOP
        if s.get(f"{side}_title"):
            _textbox(slide, left, top, col_w, Inches(0.6), s[f"{side}_title"], TYPE_SCALE["h2"] - 4, "teal", bold=True, font=theme.font)
            top += Inches(0.7)
        content = s.get(side, [])
        if isinstance(content, list):
            _bullets(slide, theme, left, top, col_w, BODY_H - (top - BODY_TOP), content, size=TYPE_SCALE["body"] - 2)
        else:
            _textbox(slide, left, top, col_w, BODY_H - (top - BODY_TOP), str(content), TYPE_SCALE["body"] - 2, "ink", font=theme.font)


def build_comparison(slide, theme, s, ctx):
    _title(slide, theme, s.get("title", ""))
    headers = s.get("headers", [])
    rows = s.get("rows", [])
    height = min(BODY_H, Inches(0.5) * (len(rows) + 1))
    _table(slide, theme, MARGIN, BODY_TOP, CONTENT_W, height, headers, rows, size=TYPE_SCALE["body"] - 2)


def build_big_number(slide, theme, s, ctx):
    _title(slide, theme, s.get("title", ""))
    _textbox(slide, MARGIN, Inches(2.2), CONTENT_W, Inches(2.0), str(s.get("value", "")), 110, theme.accent if theme.accent != "lime" else "teal", bold=True, font=theme.font, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    if s.get("label"):
        _textbox(slide, MARGIN, Inches(4.3), CONTENT_W, Inches(0.7), s["label"], TYPE_SCALE["h2"], "ink", font=theme.font, align=PP_ALIGN.CENTER)
    if s.get("trend"):
        _textbox(slide, MARGIN, Inches(5.0), CONTENT_W, Inches(0.6), s["trend"], TYPE_SCALE["body"], "leaf", bold=True, font=theme.font, align=PP_ALIGN.CENTER)


def build_quote(slide, theme, s, ctx):
    _background(slide, "cream")
    mark = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, MARGIN, Inches(2.0), Inches(0.12), Inches(2.6))
    _fill(mark, theme.accent)
    _textbox(slide, MARGIN + Inches(0.5), Inches(1.9), CONTENT_W - Inches(0.5), Inches(2.8), f"\u201c{s.get('quote', '')}\u201d", TYPE_SCALE["h2"] + 2, "ink", font=theme.serif, italic=True, anchor=MSO_ANCHOR.MIDDLE)
    if s.get("author"):
        _textbox(slide, MARGIN + Inches(0.5), Inches(4.9), CONTENT_W - Inches(0.5), Inches(0.6), s["author"], TYPE_SCALE["body"], "teal", bold=True, font=theme.font)


def build_image(slide, theme, s, ctx):
    _title(slide, theme, s.get("title", ""))
    image = s.get("image")
    cap_h = Inches(0.6) if s.get("caption") else 0
    box_h = BODY_H - cap_h
    path = Path(image) if image else None
    if path and not path.is_absolute():
        path = ctx["base_dir"] / path
    if path and path.is_file():
        pic = slide.shapes.add_picture(str(path), MARGIN, BODY_TOP, height=box_h)
        if pic.width > CONTENT_W:
            ratio = CONTENT_W / pic.width
            pic.width = int(pic.width * ratio)
            pic.height = int(pic.height * ratio)
        pic.left = int((SLIDE_W - pic.width) / 2)
    else:
        if not s.get("allow_missing"):
            raise FileNotFoundError(f"slide {ctx['index']}: image not found {image!r}")
        placeholder = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, MARGIN, BODY_TOP, CONTENT_W, box_h)
        _fill(placeholder, "cream")
        placeholder.line.color.rgb = rgb("ink")
        tf = placeholder.text_frame
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        run = tf.paragraphs[0].add_run()
        run.text = f"image missing: {image or '(none)'}"
        _style_run(run, TYPE_SCALE["small"], "coral", font=FONT_MONO)
        ctx["warnings"].append(f"slide {ctx['index']}: image not found {image!r}")
    if s.get("caption"):
        _textbox(slide, MARGIN, BODY_TOP + box_h, CONTENT_W, cap_h, s["caption"], TYPE_SCALE["caption"], "ink", font=theme.font, align=PP_ALIGN.CENTER, italic=True)


def build_table(slide, theme, s, ctx):
    _title(slide, theme, s.get("title", ""))
    rows = s.get("rows", [])
    height = min(BODY_H, Inches(0.42) * (len(rows) + 1))
    _table(slide, theme, MARGIN, BODY_TOP, CONTENT_W, height, s.get("headers", []), rows)


def build_chart(slide, theme, s, ctx):
    _title(slide, theme, s.get("title", ""))
    _chart(slide, theme, MARGIN, BODY_TOP, CONTENT_W, BODY_H, s)


def build_closing(slide, theme, s, ctx):
    _background(slide, "ink")
    strip = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, SLIDE_H - Inches(0.35), SLIDE_W, Inches(0.35))
    _fill(strip, theme.accent)
    _textbox(slide, MARGIN, Inches(2.6), CONTENT_W, Inches(1.4), s.get("title", "Thank you"), TYPE_SCALE["display"], "paper", bold=True, font=theme.font, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    if s.get("subtitle"):
        _textbox(slide, MARGIN, Inches(4.0), CONTENT_W, Inches(0.8), s["subtitle"], TYPE_SCALE["h2"] - 4, theme.accent, font=theme.font, align=PP_ALIGN.CENTER)
    if s.get("contact"):
        _textbox(slide, MARGIN, Inches(5.0), CONTENT_W, Inches(0.6), s["contact"], TYPE_SCALE["body"], "paper", font=FONT_MONO, align=PP_ALIGN.CENTER)


BUILDERS = {
    "title": build_title,
    "agenda": build_agenda,
    "section": build_section,
    "bullets": build_bullets,
    "two_column": build_two_column,
    "comparison": build_comparison,
    "big_number": build_big_number,
    "quote": build_quote,
    "image": build_image,
    "table": build_table,
    "chart": build_chart,
    "closing": build_closing,
}
NO_FOOTER = {"title", "section", "closing"}


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #


def build(spec: dict, out: Path, template: Path | None = None, base_dir: Path | None = None) -> dict:
    slides_spec = spec.get("slides")
    if not isinstance(slides_spec, list) or not slides_spec:
        raise ValueError("spec.slides must be a non-empty list")
    theme = Theme(spec.get("theme") or {})
    prs = Presentation(str(template)) if template else Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    blank = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[-1]
    ctx = {"base_dir": base_dir or Path.cwd(), "warnings": [], "index": 0}
    total = len(slides_spec)
    footer_label = spec.get("footer", spec.get("title", ""))
    for index, s in enumerate(slides_spec, start=1):
        kind = str(s.get("type", "bullets")).lower()
        builder = BUILDERS.get(kind)
        if builder is None:
            raise ValueError(f"slide {index}: unknown type {kind!r}; known: {', '.join(BUILDERS)}")
        ctx["index"] = index
        slide = prs.slides.add_slide(blank)
        if kind not in NO_FOOTER:
            _background(slide, "paper")
        builder(slide, theme, s, ctx)
        if kind not in NO_FOOTER:
            _footer(slide, theme, index, total, footer_label)
        if s.get("notes"):
            slide.notes_slide.notes_text_frame.text = str(s["notes"])
        if kind == "bullets" and len([b for b in s.get("bullets", []) if not isinstance(b, list)]) > 6:
            ctx["warnings"].append(f"slide {index}: more than 6 top-level bullets")
    if spec.get("title"):
        prs.core_properties.title = str(spec["title"])
    if spec.get("author"):
        prs.core_properties.author = str(spec["author"])
    if spec.get("language"):
        prs.core_properties.language = str(spec["language"])
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return {"ok": True, "output": str(out), "slides": total, "warnings": ctx["warnings"], "size_in": [round(Emu(SLIDE_W).inches, 2), round(Emu(SLIDE_H).inches, 2)]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pptx_build.py", description="Build a Navin-themed deck from JSON.")
    parser.add_argument("--spec", type=Path, help="JSON spec file")
    parser.add_argument("--out", type=Path, help="output .pptx")
    parser.add_argument("--template", type=Path, default=None, help="optional base .pptx for masters")
    parser.add_argument("--spec-example", action="store_true", help="print an example spec and exit")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.spec_example:
        print(json.dumps(EXAMPLE_SPEC, ensure_ascii=False, indent=2))
        return 0
    if not args.spec or not args.out:
        parser.error("--spec and --out are required (or use --spec-example)")
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        result = build(spec, args.out, args.template, args.spec.parent)
    except CLI_ERRORS as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False) if args.json else f"{result['output']} ({result['slides']} slides)" + (f"\nwarnings: {result['warnings']}" if result["warnings"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
