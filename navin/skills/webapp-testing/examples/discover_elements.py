#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""List the interactive elements of a page as JSON, to pick stable selectors.

Usage:
    discover_elements.py URL [--screenshot PATH] [--wait-ms N]

URL may be http(s)://... or file:///absolute/path.html. Output: a JSON object
with title, url, buttons, links, inputs and headings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _playwright import sync_playwright


def describe(locator, attrs: tuple[str, ...]) -> list[dict]:
    items = []
    for handle in locator.all():
        item = {"text": handle.inner_text().strip()[:120], "visible": handle.is_visible()}
        for attr in attrs:
            value = handle.get_attribute(attr)
            if value:
                item[attr] = value
        items.append(item)
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--screenshot", metavar="PATH", help="also save a full-page screenshot")
    parser.add_argument("--wait-ms", type=int, default=0, help="extra wait after networkidle")
    args = parser.parse_args(argv)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        if args.wait_ms:
            page.wait_for_timeout(args.wait_ms)
        report = {
            "title": page.title(),
            "url": page.url,
            "buttons": describe(page.locator("button, [role=button], input[type=submit]"), ("id", "name", "aria-label", "data-testid")),
            "links": describe(page.locator("a[href]"), ("href", "id", "aria-label")),
            "inputs": describe(page.locator("input, textarea, select"), ("id", "name", "type", "placeholder", "aria-label")),
            "headings": describe(page.locator("h1, h2, h3"), ("id",)),
        }
        if args.screenshot:
            page.screenshot(path=args.screenshot, full_page=True)
            report["screenshot"] = args.screenshot
        browser.close()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
