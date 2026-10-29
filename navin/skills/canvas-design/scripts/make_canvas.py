#!/usr/bin/env python3
"""Render a single-page canvas (poster, cover, title card) from a JSON spec with Pillow.

Usage:
    make_canvas.py spec.json out.png            render to PNG
    make_canvas.py spec.json out.pdf            render to PDF (one page)
    make_canvas.py --spec-example               print an example spec (Navin palette)
    make_canvas.py --spec-example > spec.json

Spec (all colors are hex; positions are pixels, or fractions of the canvas
when between 0 and 1 inclusive and written as floats):

    {
      "width": 1200, "height": 1600, "dpi": 150,
      "background": "#f4f3ee",
      "fonts": {"display": "~/.cache/navin/fonts/Inter[opsz,wght].ttf"},
      "layers": [
        {"type": "rect",    "x": 0.08, "y": 0.08, "w": 0.84, "h": 0.5, "fill": "#17171a"},
        {"type": "line",    "x1": 0.08, "y1": 0.62, "x2": 0.92, "y2": 0.62, "color": "#17171a", "width": 2},
        {"type": "ellipse", "cx": 0.5, "cy": 0.33, "r": 0.12, "fill": "#c6e88a"},
        {"type": "grid",    "x": 0.08, "y": 0.66, "w": 0.84, "h": 0.2, "rows": 12, "cols": 0, "color": "#17171a", "width": 1},
        {"type": "dots",    "x": 0.08, "y": 0.66, "w": 0.84, "h": 0.2, "rows": 6, "cols": 24, "r": 2, "fill": "#17171a"},
        {"type": "text",    "text": "Navin", "x": 0.08, "y": 0.9, "font": "display", "size": 96,
                            "weight": 600, "tracking": -2, "color": "#17171a", "align": "left", "anchor": "ls"},
        {"type": "text",    "text": "N 48.85 / E 2.35", "x": 0.92, "y": 0.9, "font": "mono", "size": 20,
                            "tracking": 2, "color": "#17171a", "align": "right", "anchor": "ls",
                            "max_width": 0.4}
      ]
    }

Layers draw in order. `font` names an entry of `fonts`; a missing or unreadable
TTF falls back to Pillow's default face with a warning on stderr. `opacity`
(0-1) is honored on every layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAVIN = {
    "ink": "#17171a",
    "graphite": "#121214",
    "paper": "#f4f3ee",
    "cream": "#faf9f6",
    "lime": "#c6e88a",
    "teal": "#0e7490",
    "sky": "#0ea5e9",
    "leaf": "#6fbf6a",
    "violet": "#7c3aed",
    "coral": "#ff5f57",
    "amber": "#febc2e",
}

FONT_CACHE = Path.home() / ".cache" / "navin" / "fonts"

EXAMPLE_SPEC = {
    "width": 1200,
    "height": 1600,
    "dpi": 150,
    "background": NAVIN["paper"],
    "fonts": {
        "display": str(FONT_CACHE / "Inter[opsz,wght].ttf"),
        "mono": str(FONT_CACHE / "IBMPlexMono-Regular.ttf"),
    },
    "layers": [
        {"type": "rect", "x": 0.08, "y": 0.08, "w": 0.84, "h": 0.52, "fill": NAVIN["ink"]},
        {
            "type": "grid",
            "x": 0.08,
            "y": 0.08,
            "w": 0.84,
            "h": 0.52,
            "rows": 26,
            "cols": 0,
            "color": NAVIN["paper"],
            "width": 1,
            "opacity": 0.18,
        },
        {"type": "ellipse", "cx": 0.62, "cy": 0.34, "r": 0.11, "fill": NAVIN["lime"]},
        {"type": "ellipse", "cx": 0.62, "cy": 0.34, "r": 0.16, "outline": NAVIN["paper"], "width": 1, "opacity": 0.5},
        {"type": "line", "x1": 0.08, "y1": 0.66, "x2": 0.92, "y2": 0.66, "color": NAVIN["ink"], "width": 2},
        {
            "type": "dots",
            "x": 0.08,
            "y": 0.70,
            "w": 0.84,
            "h": 0.10,
            "rows": 4,
            "cols": 28,
            "r": 2,
            "fill": NAVIN["ink"],
        },
        {
            "type": "text",
            "text": "Field notes",
            "x": 0.08,
            "y": 0.90,
            "font": "display",
            "size": 104,
            "weight": 600,
            "tracking": -3,
            "color": NAVIN["ink"],
            "align": "left",
            "anchor": "ls",
        },
        {
            "type": "text",
            "text": "No. 07 / 2025",
            "x": 0.92,
            "y": 0.90,
            "font": "mono",
            "size": 22,
            "tracking": 3,
            "color": NAVIN["ink"],
            "align": "right",
            "anchor": "ls",
        },
    ],
}


def hex_to_rgba(value: str, opacity: float = 1.0) -> tuple[int, int, int, int]:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    r, g, b = (int(v[i : i + 2], 16) for i in (0, 2, 4))
    return r, g, b, max(0, min(255, round(opacity * 255)))


class Canvas:
    def __init__(self, spec: dict):
        self.w = int(spec.get("width", 1200))
        self.h = int(spec.get("height", 1600))
        self.dpi = int(spec.get("dpi", 150))
        bg = hex_to_rgba(spec.get("background", NAVIN["paper"]))
        self.image = Image.new("RGBA", (self.w, self.h), bg)
        self.fonts: dict[str, str] = spec.get("fonts", {})
        self._font_cache: dict[tuple, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self.warnings: list[str] = []

    # geometry -------------------------------------------------------------
    def px(self, value, axis: str) -> int:
        """Fraction (float in [0, 1]) to pixels along 'x' or 'y'; ints pass through."""
        if isinstance(value, float) and 0.0 <= value <= 1.0:
            return round(value * (self.w if axis == "x" else self.h))
        return int(round(value))

    def radius(self, value) -> int:
        if isinstance(value, float) and 0.0 <= value <= 1.0:
            return round(value * min(self.w, self.h))
        return int(round(value))

    # fonts ----------------------------------------------------------------
    def font(self, name: str | None, size: int, weight: int | None = None):
        key = (name, size, weight)
        if key in self._font_cache:
            return self._font_cache[key]
        path = self.fonts.get(name or "", name or "")
        font = None
        if path:
            p = Path(path).expanduser()
            if p.is_file():
                try:
                    font = ImageFont.truetype(str(p), size)
                    if weight is not None:
                        # Static fonts raise OSError on the variation API; old FreeType
                        # builds raise NotImplementedError. Both mean: keep the default face.
                        try:
                            axes = font.get_variation_axes()
                            values = [a.get("default", 400) for a in axes]
                            for i, axis in enumerate(axes):
                                if axis.get("name") in (b"Weight", "Weight") or axis.get("tag") == b"wght":
                                    values[i] = weight
                            font.set_variation_by_axes(values)
                        except (OSError, NotImplementedError):
                            self.warnings.append(f"font {p} is not variable; weight {weight} ignored")
                except OSError as exc:
                    self.warnings.append(f"font {p} unreadable ({exc}); using default face")
            else:
                self.warnings.append(f"font {p} not found; using default face (run scripts/fetch_fonts.py)")
        if font is None:
            try:
                font = ImageFont.load_default(size=size)
            except TypeError:  # very old Pillow
                font = ImageFont.load_default()
        self._font_cache[key] = font
        return font

    # layers ---------------------------------------------------------------
    def layer_surface(self):
        surface = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        return surface, ImageDraw.Draw(surface)

    def commit(self, surface: Image.Image) -> None:
        self.image.alpha_composite(surface)

    def draw_rect(self, layer: dict) -> None:
        surface, d = self.layer_surface()
        x, y = self.px(layer["x"], "x"), self.px(layer["y"], "y")
        w, h = self.px(layer["w"], "x"), self.px(layer["h"], "y")
        op = float(layer.get("opacity", 1.0))
        fill = hex_to_rgba(layer["fill"], op) if layer.get("fill") else None
        outline = hex_to_rgba(layer["outline"], op) if layer.get("outline") else None
        radius = self.radius(layer.get("radius", 0))
        if radius:
            d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill, outline=outline, width=int(layer.get("width", 1)))
        else:
            d.rectangle([x, y, x + w, y + h], fill=fill, outline=outline, width=int(layer.get("width", 1)))
        self.commit(surface)

    def draw_line(self, layer: dict) -> None:
        surface, d = self.layer_surface()
        pts = [
            (self.px(layer["x1"], "x"), self.px(layer["y1"], "y")),
            (self.px(layer["x2"], "x"), self.px(layer["y2"], "y")),
        ]
        d.line(pts, fill=hex_to_rgba(layer.get("color", NAVIN["ink"]), float(layer.get("opacity", 1.0))), width=int(layer.get("width", 1)))
        self.commit(surface)

    def draw_ellipse(self, layer: dict) -> None:
        surface, d = self.layer_surface()
        cx, cy = self.px(layer["cx"], "x"), self.px(layer["cy"], "y")
        r = self.radius(layer["r"])
        op = float(layer.get("opacity", 1.0))
        fill = hex_to_rgba(layer["fill"], op) if layer.get("fill") else None
        outline = hex_to_rgba(layer["outline"], op) if layer.get("outline") else None
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=outline, width=int(layer.get("width", 1)))
        self.commit(surface)

    def draw_grid(self, layer: dict) -> None:
        """Repeated horizontal (rows) and vertical (cols) hairlines inside a box."""
        surface, d = self.layer_surface()
        x, y = self.px(layer["x"], "x"), self.px(layer["y"], "y")
        w, h = self.px(layer["w"], "x"), self.px(layer["h"], "y")
        color = hex_to_rgba(layer.get("color", NAVIN["ink"]), float(layer.get("opacity", 1.0)))
        width = int(layer.get("width", 1))
        rows, cols = int(layer.get("rows", 0)), int(layer.get("cols", 0))
        for i in range(rows + 1) if rows else []:
            yy = y + round(i * h / rows)
            d.line([(x, yy), (x + w, yy)], fill=color, width=width)
        for j in range(cols + 1) if cols else []:
            xx = x + round(j * w / cols)
            d.line([(xx, y), (xx, y + h)], fill=color, width=width)
        self.commit(surface)

    def draw_dots(self, layer: dict) -> None:
        """A field of identical circles on a rows x cols lattice inside a box."""
        surface, d = self.layer_surface()
        x, y = self.px(layer["x"], "x"), self.px(layer["y"], "y")
        w, h = self.px(layer["w"], "x"), self.px(layer["h"], "y")
        rows, cols = max(1, int(layer.get("rows", 1))), max(1, int(layer.get("cols", 1)))
        r = self.radius(layer.get("r", 2))
        fill = hex_to_rgba(layer.get("fill", NAVIN["ink"]), float(layer.get("opacity", 1.0)))
        for i in range(rows):
            cy = y + round((i + 0.5) * h / rows)
            for j in range(cols):
                cx = x + round((j + 0.5) * w / cols)
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
        self.commit(surface)

    def draw_text(self, layer: dict) -> None:
        surface, d = self.layer_surface()
        text = str(layer.get("text", ""))
        size = int(layer.get("size", 48))
        font = self.font(layer.get("font"), size, layer.get("weight"))
        tracking = float(layer.get("tracking", 0))
        color = hex_to_rgba(layer.get("color", NAVIN["ink"]), float(layer.get("opacity", 1.0)))
        x, y = self.px(layer["x"], "x"), self.px(layer["y"], "y")
        align = layer.get("align", "left")
        anchor = layer.get("anchor", "ls")
        max_width = self.px(layer["max_width"], "x") if layer.get("max_width") is not None else None
        lines = self._wrap(text, font, tracking, max_width) if max_width else text.split("\n")
        line_height = int(layer.get("line_height", round(size * 1.15)))
        for n, line in enumerate(lines):
            width = self._text_width(line, font, tracking)
            if align == "center":
                sx = x - width // 2
            elif align == "right":
                sx = x - width
            else:
                sx = x
            sy = y + n * line_height
            self._draw_tracked(d, line, sx, sy, font, tracking, color, anchor)
        self.commit(surface)

    def _text_width(self, text: str, font, tracking: float) -> int:
        if not text:
            return 0
        if tracking == 0:
            return int(font.getlength(text))
        return int(sum(font.getlength(ch) for ch in text) + tracking * (len(text) - 1))

    def _wrap(self, text: str, font, tracking: float, max_width: int) -> list[str]:
        lines: list[str] = []
        for paragraph in text.split("\n"):
            words = paragraph.split()
            current = ""
            for word in words:
                trial = (current + " " + word).strip()
                if self._text_width(trial, font, tracking) <= max_width or not current:
                    current = trial
                else:
                    lines.append(current)
                    current = word
            lines.append(current)
        return lines

    def _draw_tracked(self, d, text, x, y, font, tracking, color, anchor) -> None:
        if tracking == 0:
            d.text((x, y), text, font=font, fill=color, anchor=anchor)
            return
        cursor = x
        # Letter-by-letter with a left anchor; vertical anchor is kept.
        vert = anchor[1] if len(anchor) == 2 else "s"
        for ch in text:
            d.text((cursor, y), ch, font=font, fill=color, anchor="l" + vert)
            cursor += font.getlength(ch) + tracking

    def render(self, spec: dict) -> Image.Image:
        handlers = {
            "rect": self.draw_rect,
            "line": self.draw_line,
            "ellipse": self.draw_ellipse,
            "grid": self.draw_grid,
            "dots": self.draw_dots,
            "text": self.draw_text,
        }
        for i, layer in enumerate(spec.get("layers", [])):
            kind = layer.get("type")
            if kind not in handlers:
                self.warnings.append(f"layer {i}: unknown type {kind!r}, skipped")
                continue
            handlers[kind](layer)
        return self.image


def save(image: Image.Image, out: Path, dpi: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    rgb = image.convert("RGB")
    if out.suffix.lower() == ".pdf":
        rgb.save(out, "PDF", resolution=dpi)
    else:
        rgb.save(out, dpi=(dpi, dpi))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", nargs="?", help="JSON spec file")
    parser.add_argument("out", nargs="?", help="output .png or .pdf")
    parser.add_argument("--spec-example", action="store_true", help="print an example spec and exit")
    args = parser.parse_args(argv)

    if args.spec_example:
        print(json.dumps(EXAMPLE_SPEC, indent=2))
        return 0
    if not args.spec or not args.out:
        parser.print_help()
        return 2
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    canvas = Canvas(spec)
    image = canvas.render(spec)
    out = Path(args.out)
    save(image, out, canvas.dpi)
    for w in canvas.warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"wrote {out} ({canvas.w}x{canvas.h}, {canvas.dpi} dpi, {len(spec.get('layers', []))} layers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
