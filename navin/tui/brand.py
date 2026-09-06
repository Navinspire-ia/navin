"""Navin visual signature for the terminal UI.

The identity is a gray plate with the product name, then a hairline that
fades from charcoal to light gray. The CLI stays black, white and gray.

* the chip ``[ navin ]`` (white on the gray plate),
* the *tide line*: a hairline that fades from charcoal to light gray,
* a tide spinner made of block bars rising and falling like a wave.

Everything here is pure data / Rich helpers so widgets and the app share one
source of truth and nothing looks like another coding CLI.
"""

from __future__ import annotations

import math
from functools import lru_cache

from rich.markup import escape
from rich.text import Text

# Gray plate + wash (CLI is monochrome: black, white, gray).
BRAND_PLATE = "#4A4A4A"
BRAND_WASH = "#B0B0B0"
BRAND_INK = "#FFFFFF"

MARK = ""

_BARS = "▁▂▃▄▅▆▇█"


def brand_chip(name: str = "navin", *, mark: str = MARK) -> str:
    """Markup for the gray plate with the product name."""
    label = escape((name or "navin").strip().lower())
    glyph = (mark or "").strip()
    if glyph in {"≈", "~"}:
        glyph = ""
    inner = f"{escape(glyph)} {label}" if glyph else label
    return f"[b {BRAND_INK} on {BRAND_PLATE}] {inner} [/]"


def _blend(t: float) -> str:
    t = min(1.0, max(0.0, t))
    start = (0x4A, 0x4A, 0x4A)
    end = (0xB0, 0xB0, 0xB0)
    r, g, b = (round(s + (e - s) * t) for s, e in zip(start, end, strict=True))
    return f"#{r:02X}{g:02X}{b:02X}"


def tide_text(width: int, char: str = "─") -> Text:
    """A one-row gradient rule (charcoal -> light gray) *width* cells wide."""
    width = max(0, int(width))
    text = Text(no_wrap=True, overflow="crop")
    if width == 0:
        return text
    for i in range(width):
        text.append(char, style=_blend(i / max(1, width - 1)))
    return text


@lru_cache(maxsize=4)
def wave_frames(width: int = 3, steps: int = 12) -> tuple[str, ...]:
    """Frames of a small standing wave drawn with block bars."""
    frames: list[str] = []
    for step in range(steps):
        cells = []
        for i in range(width):
            phase = 2 * math.pi * (step / steps + i / width)
            level = (math.sin(phase) + 1) / 2  # 0..1
            cells.append(_BARS[min(len(_BARS) - 1, int(level * (len(_BARS) - 1) + 0.5))])
        frames.append("".join(cells))
    return tuple(frames)


def wave_frame(tick: int, *, width: int = 3) -> str:
    frames = wave_frames(width)
    return frames[tick % len(frames)]
