# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Pillow artwork for polished footage: cursor sprites, click ripples, framed layout.

Everything here is drawn from vector shapes at a supersampled size and reduced
with a Lanczos filter, so the sprites stay crisp at any device scale factor.
The PNGs under ``navin/montage/assets/`` are produced by :func:`write_assets`
and are only a convenience for other renderers; the footage pipeline always
draws the cursor at the exact pixel size it needs.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# Arrow outline in design units, hotspot (the tip) at (0, 0).
_ARROW = (
    (0.0, 0.0),
    (0.0, 17.0),
    (4.2, 13.2),
    (7.0, 19.6),
    (9.6, 18.5),
    (6.9, 12.2),
    (12.4, 12.2),
)
_ARROW_H = 19.6
_ARROW_W = 12.4

CURSOR_THEMES = ("light", "dark")

_THEME_COLORS = {
    # fill, outline
    "light": ((255, 255, 255, 255), (17, 17, 20, 255)),
    "dark": ((17, 17, 20, 255), (255, 255, 255, 255)),
}


def parse_color(value: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """``#RRGGBB`` / ``RRGGBB`` / ``#RGB`` to an RGB tuple; *default* when invalid."""
    text = (value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return default
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return default


@lru_cache(maxsize=16)
def render_cursor(theme: str = "light", height_px: int = 48) -> Image.Image:
    """Arrow cursor of *height_px* with outline and soft shadow.

    The returned RGBA image has the hotspot at :func:`cursor_hotspot` so callers
    can align the tip exactly.
    """
    fill, outline = _THEME_COLORS.get(theme, _THEME_COLORS["light"])
    ss = 4
    unit = height_px * ss / _ARROW_H
    outline_px = max(2, round(1.25 * unit))
    pad = outline_px + round(3.0 * unit)
    w = int(math.ceil(_ARROW_W * unit)) + 2 * pad
    h = int(math.ceil(_ARROW_H * unit)) + 2 * pad
    points = [(pad + x * unit, pad + y * unit) for x, y in _ARROW]

    body = Image.new("L", (w, h), 0)
    ImageDraw.Draw(body).polygon(points, fill=255)
    size = outline_px * 2 + 1
    ring = body.filter(ImageFilter.MaxFilter(size if size % 2 else size + 1))

    shadow = ring.filter(ImageFilter.GaussianBlur(radius=1.6 * unit))
    shadow = Image.eval(shadow, lambda v: int(v * 0.42))
    shifted = Image.new("L", (w, h), 0)
    shifted.paste(shadow, (0, round(0.9 * unit)))

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste((0, 0, 0, 255), (0, 0), shifted)
    out.paste(outline, (0, 0), ring)
    out.paste(fill, (0, 0), body)
    final = out.resize((max(1, w // ss), max(1, h // ss)), Image.Resampling.LANCZOS)
    final.info["hotspot"] = (pad / ss, pad / ss)
    return final


def cursor_hotspot(image: Image.Image) -> tuple[float, float]:
    spot = image.info.get("hotspot")
    if isinstance(spot, tuple) and len(spot) == 2:
        return float(spot[0]), float(spot[1])
    return 0.0, 0.0


@dataclass(frozen=True)
class SpriteSpec:
    """Square sprite canvas with the cursor hotspot at its center."""

    size: int
    cursor_height: int
    ripple_radius: float
    theme: str
    accent: tuple[int, int, int]


def sprite_spec(
    cursor_height: int, *, theme: str = "light", accent: tuple[int, int, int] = (79, 140, 255)
) -> SpriteSpec:
    cursor_height = max(8, int(cursor_height))
    radius = cursor_height * 1.05
    half = max(radius + cursor_height * 0.2, cursor_height * 1.35) + 4
    size = int(math.ceil(half)) * 2
    return SpriteSpec(
        size=size,
        cursor_height=cursor_height,
        ripple_radius=radius,
        theme=theme if theme in CURSOR_THEMES else "light",
        accent=accent,
    )


def render_sprite(spec: SpriteSpec, scale: float, ripples: tuple[float, ...]) -> Image.Image:
    """One cursor frame: ripples (progress 0..1) under the arrow scaled by *scale*."""
    size = spec.size
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    center = size / 2.0
    if ripples:
        ss = 3
        layer = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        for progress in ripples:
            p = min(1.0, max(0.0, progress))
            eased = 1.0 - (1.0 - p) ** 3
            radius = spec.ripple_radius * (0.25 + 0.75 * eased) * ss
            alpha = 0.6 * (1.0 - p) ** 1.4
            stroke = max(1, round(spec.cursor_height * 0.09 * (1.0 - 0.5 * p) * ss))
            c = center * ss
            box = (c - radius, c - radius, c + radius, c + radius)
            r, g, b = spec.accent
            draw.ellipse(box, fill=(r, g, b, int(255 * alpha * 0.28)))
            draw.ellipse(box, outline=(r, g, b, int(255 * alpha)), width=stroke)
        canvas.alpha_composite(layer.resize((size, size), Image.Resampling.LANCZOS))
    arrow = render_cursor(spec.theme, max(8, round(spec.cursor_height * 2)))
    target_h = max(4, round(arrow.height * 0.5 * scale))
    target_w = max(4, round(arrow.width * 0.5 * scale))
    hx, hy = cursor_hotspot(arrow)
    scaled = arrow.resize((target_w, target_h), Image.Resampling.LANCZOS)
    fx = hx * target_w / arrow.width
    fy = hy * target_h / arrow.height
    canvas.alpha_composite(scaled, (round(center - fx), round(center - fy)))
    return canvas


_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/inter/Inter-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow without FreeType sizing
        return ImageFont.load_default()


def _wrap(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and font.getlength(trial) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = textwrap.shorten(lines[-1] + " ...", width=max(8, len(lines[-1])))
    return lines


def _rounded_mask(
    size: tuple[int, int], box: tuple[int, int, int, int], radius: int
) -> Image.Image:
    ss = 3
    big = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    x0, y0, x1, y1 = box
    ImageDraw.Draw(big).rounded_rectangle(
        (x0 * ss, y0 * ss, x1 * ss - 1, y1 * ss - 1), radius=radius * ss, fill=255
    )
    return big.resize(size, Image.Resampling.LANCZOS)


def render_framed_overlay(
    width: int,
    height: int,
    inner: tuple[int, int, int, int],
    *,
    title: str = "",
    background: tuple[int, int, int] = (11, 16, 32),
    accent: tuple[int, int, int] = (79, 140, 255),
) -> Image.Image:
    """Branded background with a transparent rounded hole where the footage shows.

    *inner* is ``(x, y, w, h)``. The footage is composited under this layer, so
    the rounded corners, drop shadow and title come out exact without masking
    the video itself.
    """
    x, y, w, h = inner
    radius = max(8, round(min(w, h) * 0.035))
    top = background
    bottom = tuple(max(0, int(c * 0.55)) for c in background)
    gradient = Image.linear_gradient("L").resize((width, height))
    bg = Image.composite(
        Image.new("RGB", (width, height), bottom),
        Image.new("RGB", (width, height), top),
        gradient,
    ).convert("RGBA")

    glow = Image.new("L", (width, height), 0)
    gx, gy = x + w / 2, y + h / 2
    gr = max(w, h) * 0.62
    ImageDraw.Draw(glow).ellipse((gx - gr, gy - gr * 0.8, gx + gr, gy + gr * 0.8), fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=gr * 0.35))
    bg.paste(accent + (255,), (0, 0), Image.eval(glow, lambda v: int(v * 0.45)))

    hole = _rounded_mask((width, height), (x, y, x + w, y + h), radius)
    shadow = hole.filter(ImageFilter.GaussianBlur(radius=max(6, w * 0.025)))
    shadow_shift = Image.new("L", (width, height), 0)
    shadow_shift.paste(shadow, (0, round(h * 0.03)))
    bg.paste((0, 0, 0, 255), (0, 0), Image.eval(shadow_shift, lambda v: int(v * 0.55)))

    if title.strip():
        font_size = max(18, round(width * 0.062))
        font = load_font(font_size)
        lines = _wrap(title.strip(), font, int(width * 0.86))
        draw = ImageDraw.Draw(bg)
        line_h = round(font_size * 1.22)
        block_h = line_h * len(lines)
        start_y = max(round(height * 0.04), y - round(height * 0.035) - block_h)
        for idx, line in enumerate(lines):
            lw = font.getlength(line)
            draw.text(
                ((width - lw) / 2, start_y + idx * line_h),
                line,
                font=font,
                fill=(255, 255, 255, 255),
            )
        bar_w = round(width * 0.08)
        bar_y = start_y - round(font_size * 0.55)
        if bar_y > 4:
            draw.rounded_rectangle(
                ((width - bar_w) / 2, bar_y, (width + bar_w) / 2, bar_y + max(3, font_size // 9)),
                radius=4,
                fill=accent + (255,),
            )

    alpha = bg.getchannel("A")
    alpha = Image.composite(Image.new("L", (width, height), 0), alpha, hole)
    bg.putalpha(alpha)
    return bg


def render_caption(text: str, width: int, band_height: int) -> Image.Image:
    """Caption band: centered white text on a soft pill, transparent elsewhere."""
    band = Image.new("RGBA", (width, band_height), (0, 0, 0, 0))
    if not (text or "").strip():
        return band
    font_size = max(14, round(width * 0.048))
    font = load_font(font_size)
    lines = _wrap(" ".join(text.split()), font, int(width * 0.82))
    if not lines:
        return band
    line_h = round(font_size * 1.3)
    block_h = line_h * len(lines)
    block_w = max(font.getlength(line) for line in lines)
    pad_x, pad_y = round(font_size * 0.8), round(font_size * 0.45)
    x0 = (width - block_w) / 2 - pad_x
    y0 = max(0, round(band_height * 0.18))
    draw = ImageDraw.Draw(band)
    draw.rounded_rectangle(
        (x0, y0, x0 + block_w + 2 * pad_x, y0 + block_h + 2 * pad_y),
        radius=round(font_size * 0.6),
        fill=(0, 0, 0, 150),
    )
    for idx, line in enumerate(lines):
        lw = font.getlength(line)
        draw.text(
            ((width - lw) / 2, y0 + pad_y + idx * line_h),
            line,
            font=font,
            fill=(255, 255, 255, 255),
        )
    return band


def write_assets(directory: Path | None = None, *, height_px: int = 64) -> list[Path]:
    """Write ``cursor-light.png`` / ``cursor-dark.png`` (tip at the top-left pad)."""
    target = directory or ASSETS_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for theme in CURSOR_THEMES:
        path = target / f"cursor-{theme}.png"
        render_cursor(theme, height_px).save(path, optimize=True)
        written.append(path)
    return written


if __name__ == "__main__":  # pragma: no cover - asset build helper
    for item in write_assets():
        print(item)
