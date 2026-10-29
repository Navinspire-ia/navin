#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Load a page, optionally click something, and report console errors and failed requests.

Usage:
    console_errors.py URL [--click SELECTOR] [--settle-ms N] [--all-levels]

Output: JSON with console (level, text, location), page_errors (uncaught
exceptions) and failed_requests (method, url, status or failure text).
Exit code 1 when any error, page exception or failed request was seen.
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
    parser.add_argument("--click", metavar="SELECTOR", help="click this element after load")
    parser.add_argument("--settle-ms", type=int, default=500, help="wait after the action (default 500)")
    parser.add_argument("--all-levels", action="store_true", help="keep log/info messages too, not only warning and error")
    args = parser.parse_args(argv)

    console: list[dict] = []
    page_errors: list[str] = []
    failed: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def on_console(msg):
            if args.all_levels or msg.type in ("error", "warning"):
                loc = msg.location or {}
                where = f"{loc.get('url', '')}:{loc.get('lineNumber', '')}" if loc.get("url") else ""
                console.append({"level": msg.type, "text": msg.text, "location": where})

        page.on("console", on_console)
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("requestfailed", lambda req: failed.append({"method": req.method, "url": req.url, "failure": req.failure}))
        page.on("response", lambda res: failed.append({"method": res.request.method, "url": res.url, "status": res.status}) if res.status >= 400 else None)

        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        if args.click:
            page.click(args.click)
        page.wait_for_timeout(args.settle_ms)
        browser.close()

    errors = [c for c in console if c["level"] == "error"]
    report = {
        "url": args.url,
        "console": console,
        "page_errors": page_errors,
        "failed_requests": failed,
        "ok": not errors and not page_errors and not failed,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
