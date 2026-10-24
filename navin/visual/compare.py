"""Render HTML and compare two images (pixel similarity, divergent zones).

Pillow is a core dependency. Playwright is optional (the ``browser`` extra):
``render_html`` raises a clear error when it is not installed.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops


def have_playwright() -> bool:
    return importlib.util.find_spec("playwright") is not None


def render_html(html: str, out_png: str | Path, width: int = 1280, height: int = 800) -> Path:
    """Render an HTML string to a PNG with Playwright."""
    if not have_playwright():
        raise RuntimeError("playwright is not installed")
    from playwright.sync_api import sync_playwright

    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(html)
            page.screenshot(path=str(out), full_page=False)
        finally:
            browser.close()
    return out


def render_url(url: str, out_png: str | Path, width: int = 1280, height: int = 800) -> Path:
    """Render a URL (local dev server) to a PNG with Playwright."""
    if not have_playwright():
        raise RuntimeError("playwright is not installed")
    from playwright.sync_api import sync_playwright

    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(url)
            page.screenshot(path=str(out), full_page=False)
        finally:
            browser.close()
    return out


def compare_images(
    ref_path: str | Path,
    got_path: str | Path,
    diff_path: str | Path | None = None,
    report_path: str | Path | None = None,
    grid: int = 8,
    tolerance: int = 16,
    zone_threshold: float = 0.1,
) -> dict[str, Any]:
    """Compare two images. Score is 1.0 for identical, 0.0 for fully different."""
    ref = Image.open(ref_path).convert("RGB")
    got = Image.open(got_path).convert("RGB")
    size_match = ref.size == got.size
    if not size_match:
        got = got.resize(ref.size)
    w, h = ref.size
    diff = ImageChops.difference(ref, got).convert("L")
    mask = diff.point(lambda v: 255 if v > tolerance else 0)
    total = max(w * h, 1)
    bad = mask.histogram()[255]
    score = 1.0 - bad / total
    zones = []
    for gy in range(grid):
        for gx in range(grid):
            x0, x1 = gx * w // grid, (gx + 1) * w // grid
            y0, y1 = gy * h // grid, (gy + 1) * h // grid
            if x1 <= x0 or y1 <= y0:
                continue
            cell = mask.crop((x0, y0, x1, y1))
            ratio = cell.histogram()[255] / ((x1 - x0) * (y1 - y0))
            if ratio >= zone_threshold:
                zones.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "diff_ratio": round(ratio, 4)})
    report: dict[str, Any] = {
        "score": round(score, 6),
        "size": [w, h],
        "size_match": size_match,
        "diff_pixels": bad,
        "zones": zones,
    }
    if diff_path:
        vis = got.copy()
        red = Image.new("RGB", (w, h), (255, 0, 0))
        vis.paste(red, mask=mask)
        Path(diff_path).parent.mkdir(parents=True, exist_ok=True)
        vis.save(diff_path)
        report["diff_image"] = str(diff_path)
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
