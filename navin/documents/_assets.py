"""Turning page assets into files Office can embed."""

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import unquote, urlparse


def resolve_asset(src: str, page: Path, workdir: Path, index: int) -> Path | None:
    """Turn an image URL into a local file, or None when it cannot be inlined.

    Remote and vector sources return None on purpose: the measuring script
    leaves them visible in the page so they survive in the rendered decor
    instead of disappearing from the document.
    """
    if src.startswith("data:"):
        header, _, encoded = src.partition(",")
        if "base64" not in header:
            return None
        suffix = ".png"
        if "jpeg" in header or "jpg" in header:
            suffix = ".jpg"
        elif "svg" in header:
            return None
        target = workdir / f"__h2x_asset_{index}{suffix}"
        try:
            target.write_bytes(base64.b64decode(encoded))
        except (ValueError, OSError):
            return None
        return target
    if src.startswith("file://"):
        path = Path(unquote(urlparse(src).path))
    elif src.startswith(("http://", "https://")):
        return None
    else:
        path = (page.parent / unquote(src)).resolve()
    if path.suffix.lower() == ".svg":
        return None
    return path if path.is_file() else None


def flat_color(image_path: Path) -> str | None:
    """Return the single hex color of a uniform screenshot, else None."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow ships with Navin
        return None
    try:
        with Image.open(image_path) as image:
            colors = image.convert("RGB").getcolors(maxcolors=2)
    except OSError:
        return None
    if not colors or len(colors) != 1:
        return None
    _, (red, green, blue) = colors[0]
    return f"{red:02X}{green:02X}{blue:02X}"
