#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Compose one GIF frame from simple primitives (text, shapes, pasted images).

A FrameComposer owns an RGBA canvas. Call the drawing methods in order, then
``to_image()`` to hand the frame to GifBuilder. Colors are hex strings
("#17171a") or RGB tuples.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

Color = str | tuple[int, int, int] | tuple[int, int, int, int]


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Turn "#rgb", "#rrggbb" or "rrggbb" into an (r, g, b) tuple."""
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"not a hex color: {value!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _rgba(color: Color, alpha: int = 255) -> tuple[int, int, int, int]:
    if isinstance(color, str):
        r, g, b = hex_to_rgb(color)
        return r, g, b, alpha
    if len(color) == 4:
        r, g, b, a = color
        return int(r), int(g), int(b), int(a)
    if len(color) == 3:
        r, g, b = color
        return int(r), int(g), int(b), alpha
    raise ValueError(f"color needs 3 or 4 components, got {color!r}")


def load_font(size: int, ttf_path: str | Path | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a TrueType font, falling back to Pillow's built-in bitmap font."""
    candidates: list[Path] = []
    if ttf_path:
        candidates.append(Path(ttf_path))
    candidates += [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ]
    for cand in candidates:
        if cand.is_file():
            try:
                return ImageFont.truetype(str(cand), size=size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow without the size argument
        return ImageFont.load_default()


class FrameComposer:
    """Draw a single frame on an RGBA canvas."""

    def __init__(self, width: int, height: int, background: Color = "#f4f3ee"):
        self.width = int(width)
        self.height = int(height)
        self.background = _rgba(background)
        self.image = Image.new("RGBA", (self.width, self.height), self.background)
        self.draw = ImageDraw.Draw(self.image)

    # -- primitives ---------------------------------------------------------

    def text(
        self,
        string: str,
        size: int = 32,
        color: Color = "#17171a",
        position: tuple[float, float] | None = None,
        ttf_path: str | Path | None = None,
        anchor: str = "mm",
    ) -> tuple[int, int]:
        """Draw text. Without ``position`` the text is centered on the canvas.

        Returns the rendered (width, height) of the text box.
        """
        font = load_font(size, ttf_path)
        if position is None:
            position = (self.width / 2, self.height / 2)
        try:
            self.draw.text(position, string, fill=_rgba(color), font=font, anchor=anchor)
            left, top, right, bottom = self.draw.textbbox(position, string, font=font, anchor=anchor)
        except (ValueError, TypeError):
            # Bitmap fonts do not support anchors: center manually.
            left, top, right, bottom = self.draw.textbbox((0, 0), string, font=font)
            tw, th = right - left, bottom - top
            xy = (position[0] - tw / 2, position[1] - th / 2)
            self.draw.text(xy, string, fill=_rgba(color), font=font)
        return int(right - left), int(bottom - top)

    def rect(
        self,
        box: tuple[float, float, float, float],
        fill: Color | None = None,
        outline: Color | None = None,
        width: int = 2,
        radius: int = 0,
    ) -> None:
        """Rectangle given as (x0, y0, x1, y1); ``radius`` rounds the corners."""
        kwargs = {
            "fill": _rgba(fill) if fill is not None else None,
            "outline": _rgba(outline) if outline is not None else None,
            "width": width,
        }
        if radius > 0:
            self.draw.rounded_rectangle(box, radius=radius, **kwargs)
        else:
            self.draw.rectangle(box, **kwargs)

    def circle(
        self,
        center: tuple[float, float],
        radius: float,
        fill: Color | None = None,
        outline: Color | None = None,
        width: int = 2,
    ) -> None:
        """Filled or outlined circle."""
        cx, cy = center
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
        self.draw.ellipse(
            box,
            fill=_rgba(fill) if fill is not None else None,
            outline=_rgba(outline) if outline is not None else None,
            width=width,
        )

    def line(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        color: Color = "#17171a",
        width: int = 3,
    ) -> None:
        """Straight line; keep ``width`` at 2 or more so it survives downscaling."""
        self.draw.line([start, end], fill=_rgba(color), width=width)

    def paste(self, image: Image.Image, xy: tuple[int, int] = (0, 0), alpha: float = 1.0) -> None:
        """Composite another image at ``xy`` with an overall opacity in [0, 1]."""
        layer = image.convert("RGBA")
        if alpha < 1.0:
            a = layer.getchannel("A").point(lambda v: int(v * max(0.0, alpha)))
            layer.putalpha(a)
        self.image.alpha_composite(layer, dest=(int(xy[0]), int(xy[1])))
        self.draw = ImageDraw.Draw(self.image)

    # -- output -------------------------------------------------------------

    def to_image(self, mode: str = "RGB") -> Image.Image:
        """Return the frame flattened on its background (RGB by default)."""
        if mode == "RGBA":
            return self.image.copy()
        flat = Image.new("RGBA", self.image.size, self.background)
        flat.alpha_composite(self.image)
        return flat.convert(mode)


if __name__ == "__main__":
    demo = FrameComposer(128, 128, "#17171a")
    demo.circle((64, 64), 40, fill="#c6e88a")
    demo.text("OK", size=36, color="#17171a")
    demo.to_image().save("/tmp/frame_composer_demo.png")
    print("wrote /tmp/frame_composer_demo.png")
