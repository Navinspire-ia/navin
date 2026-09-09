# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Post-conversion checks that catch what looks bad in a finished deck.

A converter can be perfectly faithful and still hand over a poor deck, because
the filled template itself is poor: light gray copy on a light background, the
same decorative picture repeated in every column, a block left with sample text.
Those are the defects a reader notices first, so they are reported on stderr
where the agent driving the conversion can read and fix them.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

# WCAG's 3:1 floor for large text, applied to all slide copy. A deck is read
# from a distance, and its designs lean on discreet mentions (credits, slide
# numbers, watermark labels) that fail the 4.5:1 body-copy floor on purpose.
# Holding everything to 3:1 flags what is genuinely a smudge and stays quiet
# otherwise, which is the difference between a report that gets read and one
# that gets ignored.
MIN_CONTRAST = 3.0


def _luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for value in rgb:
        ratio = value / 255
        channels.append(ratio / 12.92 if ratio <= 0.04045 else ((ratio + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    """WCAG contrast ratio between two colors, 1.0 (same) to 21.0 (black/white)."""
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _hex_to_rgb(value: str | None) -> tuple[int, int, int] | None:
    if not value or not isinstance(value, str):
        return None
    text = value.lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


def _backdrop_color(
    backdrop: Path | None, box: tuple[float, float, float, float]
) -> tuple[int, int, int] | None:
    """Average color of the decor under a text box, sampled on the capture."""
    if backdrop is None or not backdrop.is_file():
        return None
    try:
        from PIL import Image

        with Image.open(backdrop) as image:
            crop = image.convert("RGB").crop(
                (
                    max(0, int(box[0])),
                    max(0, int(box[1])),
                    min(image.width, max(1, int(box[0] + box[2]))),
                    min(image.height, max(1, int(box[1] + box[3]))),
                )
            )
            if not crop.width or not crop.height:
                return None
            average = crop.resize((1, 1)).getpixel((0, 0))
    except Exception:
        return None
    return (int(average[0]), int(average[1]), int(average[2]))


def _covering_fill(
    shapes: Iterable[dict[str, Any]], box: tuple[float, float, float, float]
) -> tuple[int, int, int] | None:
    """Fill of the innermost opaque block a text sits on, if any.

    Cards and banners leave the decor as native shapes, so the capture behind
    them shows the page background instead. Comparing the text against that
    would flag white copy on a dark card as invisible.
    """
    center = (box[0] + box[2] / 2, box[1] + box[3] / 2)
    best: tuple[int, int, int] | None = None
    best_area = float("inf")
    for shape in shapes:
        fill = _hex_to_rgb(shape.get("fill"))
        if fill is None:
            continue
        left, top = float(shape.get("x", 0)), float(shape.get("y", 0))
        width, height = float(shape.get("w", 0)), float(shape.get("h", 0))
        if not (left <= center[0] <= left + width and top <= center[1] <= top + height):
            continue
        area = width * height
        if area < best_area:
            best, best_area = fill, area
    return best


def unreadable_texts(
    texts: Iterable[dict[str, Any]],
    backdrop: Path | None,
    shapes: Iterable[dict[str, Any]] = (),
) -> list[str]:
    """Texts whose color barely separates from what sits behind them."""
    shapes = list(shapes)
    found: list[str] = []
    for text in texts:
        runs = text.get("runs", [])
        sample = next((run for run in runs if str(run.get("text", "")).strip()), None)
        if sample is None:
            continue
        color = _hex_to_rgb(sample.get("color"))
        if color is None:
            continue
        box = (
            float(text.get("x", 0)),
            float(text.get("y", 0)),
            float(text.get("w", 0)),
            float(text.get("h", 0)),
        )
        behind = _covering_fill(shapes, box) or _backdrop_color(backdrop, box)
        if behind is None:
            continue
        ratio = contrast(color, behind)
        if ratio < MIN_CONTRAST:
            excerpt = str(sample.get("text", "")).strip()[:40]
            found.append(f'"{excerpt}" (contrast {ratio:.1f}:1, needs {MIN_CONTRAST:.1f}:1)')
    return found


def repeated_pictures(paths: Iterable[Path]) -> list[str]:
    """Identical picture files used more than once across the deck."""
    digests: Counter[str] = Counter()
    names: dict[str, str] = {}
    for path in paths:
        try:
            digest = hashlib.sha1(path.read_bytes()).hexdigest()
        except OSError:
            continue
        digests[digest] += 1
        names.setdefault(digest, path.name)
    return [
        f"{names[digest]} appears {count} times"
        for digest, count in digests.items()
        if count > 1
    ]
