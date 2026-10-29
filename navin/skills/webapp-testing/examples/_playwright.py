# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Shared helper: import Playwright lazily with a readable error."""

from __future__ import annotations

import sys

INSTALL_HINT = "playwright is not installed: pip install playwright && playwright install chromium"


def sync_playwright():
    try:
        from playwright.sync_api import sync_playwright as factory
    except ImportError:
        print(INSTALL_HINT, file=sys.stderr)
        sys.exit(2)
    return factory()
