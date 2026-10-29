#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Scripted flow against a static page: load, screenshot, fill a form if present, submit, screenshot.

Usage:
    static_page.py URL [--out DIR] [--name-selector S] [--email-selector S] [--submit-selector S]

URL is usually file:///absolute/path.html. Selectors default to #name, #email
and button[type=submit]; missing fields are skipped and listed in the JSON
output rather than failing the run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _playwright import sync_playwright


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--out", default=".navin/test-evidence", help="folder for screenshots")
    parser.add_argument("--name-selector", default="#name")
    parser.add_argument("--email-selector", default="#email")
    parser.add_argument("--submit-selector", default="button[type=submit]")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    steps: list[dict] = []
    skipped: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(args.url)
        page.wait_for_load_state("load")
        before = out / "01-loaded.png"
        page.screenshot(path=str(before), full_page=True)
        steps.append({"step": "load", "title": page.title(), "screenshot": str(before)})

        for label, selector, value in (("name", args.name_selector, "Ada Lovelace"), ("email", args.email_selector, "ada@example.org")):
            if page.locator(selector).count():
                page.fill(selector, value)
                steps.append({"step": f"fill {label}", "selector": selector, "value": value})
            else:
                skipped.append(selector)

        if page.locator(args.submit_selector).count():
            page.click(args.submit_selector)
            page.wait_for_timeout(300)
            after = out / "02-submitted.png"
            page.screenshot(path=str(after), full_page=True)
            steps.append({"step": "submit", "selector": args.submit_selector, "screenshot": str(after), "body_excerpt": page.inner_text("body")[:300]})
        else:
            skipped.append(args.submit_selector)
        browser.close()

    print(json.dumps({"url": args.url, "steps": steps, "skipped_selectors": skipped}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
