# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Check the real browser and native desktop bindings in every shipped sidecar.

Run with ``navin python -m navin.computer.smoke`` in a packaged installation.
Desktop permissions are reported without requesting access or sending input to
the user's desktop. Only the isolated headless browser receives test input.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from dataclasses import asdict

from navin.agent.tools.browser import BrowserToolConfig, _BrowserSession
from navin.agent.tools.computer import ComputerToolConfig
from navin.computer.detect import create_backend, detect_platform


async def check_runtime() -> dict:
    modules = {
        "win32": ("navin.computer.windows", "navin.computer.uia_windows"),
        "darwin": ("navin.computer.macos",),
    }.get(sys.platform, ("navin.computer.x11", "navin.computer.wayland", "Xlib.display"))
    for name in modules:
        importlib.import_module(name)
    choice = detect_platform()
    desktop = {"backend": choice.name, "reason": choice.reason, "checks": []}
    # A headless build host has no display. On native Windows/macOS, instantiate
    # the actual platform API bindings so missing DLLs/frameworks fail the build.
    if choice.name in {"windows", "macos"}:
        backend = create_backend(ComputerToolConfig())
        try:
            desktop["checks"] = [asdict(check) for check in await asyncio.to_thread(backend.doctor)]
        finally:
            backend.close()
    session = _BrowserSession(BrowserToolConfig(headless=True, live_view=False))
    try:
        page = await session.ensure_page()
        await page.set_content('<title>Navin browser check</title><input aria-label="Message">')
        await page.get_by_role("textbox", name="Message").fill("Navin é 🚀")
        if await page.get_by_role("textbox", name="Message").input_value() != "Navin é 🚀":
            raise RuntimeError("Browser keyboard input did not reach the local page")
        screenshot = await page.screenshot()
        if not screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Browser screenshot is not a PNG")
        browser = {"title": await page.title(), "unicode_input": True, "screenshot": True}
    finally:
        await session.close()
    return {"ok": True, "platform": sys.platform, "browser": browser,
            "computer": desktop, "desktop_input_tested": False}


def main() -> None:
    print(json.dumps(asyncio.run(check_runtime()), ensure_ascii=False))


if __name__ == "__main__":
    main()
