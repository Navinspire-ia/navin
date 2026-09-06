"""Server-side screenshot of a Dev preview URL.

The in-page probe cannot reliably rasterize a live app (tainted canvas as soon
as the page loads a cross-origin image or stylesheet). Instead the gateway
drives the same headless Chromium the browser tool and document renderers use,
navigates to the (loopback) preview URL, and returns a PNG.

Loopback only: the caller (ws_http) already gates this on
``workspace_controls_available`` and validates the URL host here as a second
line of defence.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from loguru import logger

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"})

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
NAV_TIMEOUT_MS = 15_000
SETTLE_MS = 600


class PreviewScreenshotError(Exception):
    """Screenshot could not be produced (bad URL, no browser, nav failure)."""


def is_loopback_preview_url(raw: str) -> bool:
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    return host.endswith(".localhost")


async def _launch_chromium():
    """Start a headless Chromium, preferring a system/cached binary."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on env
        raise PreviewScreenshotError(
            "playwright is not installed; cannot capture the preview server-side."
        ) from exc

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True)
        return pw, browser
    except Exception as first_error:
        # Playwright's own Chromium is missing: fall back to any Chrome/Chromium
        # already on the machine (same resolver the document renderers use).
        try:
            from navin.documents._chromium import find_chromium

            executable = find_chromium()
        except Exception:
            executable = None
        if not executable:
            await pw.stop()
            raise PreviewScreenshotError(
                "no Chromium available to capture the preview. Install a browser "
                "or run: playwright install chromium."
            ) from first_error
        try:
            browser = await pw.chromium.launch(
                headless=True, executable_path=executable
            )
            return pw, browser
        except Exception as exc:
            await pw.stop()
            raise PreviewScreenshotError(f"could not start Chromium: {exc}") from exc


async def capture_preview_png(
    url: str,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    full_page: bool = False,
) -> bytes:
    """Return PNG bytes of *url* rendered in headless Chromium."""
    if not is_loopback_preview_url(url):
        raise PreviewScreenshotError("only loopback preview URLs can be captured.")

    width = max(320, min(int(width or DEFAULT_WIDTH), 2560))
    height = max(240, min(int(height or DEFAULT_HEIGHT), 2000))

    pw, browser = await _launch_chromium()
    try:
        context = await browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
        )
        page = await context.new_page()
        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
            )
        except Exception as exc:
            raise PreviewScreenshotError(f"could not open {url}: {exc}") from exc
        # Give the app a beat to paint after DOMContentLoaded.
        with_suppress = asyncio.sleep(SETTLE_MS / 1000)
        await with_suppress
        try:
            return await page.screenshot(full_page=full_page, type="png")
        except Exception as exc:
            raise PreviewScreenshotError(f"screenshot failed: {exc}") from exc
    finally:
        try:
            await browser.close()
        except Exception:
            logger.debug("preview screenshot: browser close failed")
        try:
            await pw.stop()
        except Exception:
            logger.debug("preview screenshot: playwright stop failed")
