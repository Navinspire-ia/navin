# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The browser honours tools.ssrf_protection like web/scrape already did.

Before this guard the browser was the bypass: with SSRF protection turned on,
web.fetch refused the cloud metadata endpoint but browser(action=navigate)
happily loaded it and returned the page body.
"""

from __future__ import annotations

import asyncio
import threading
import unittest
from unittest import mock

from navin.agent.tools import browser as browser_mod
from navin.agent.tools.browser import BrowserTool, _BrowserSession
from navin.security.network import configure_ssrf_protection


class BrowserNavigationGuardTest(unittest.TestCase):
    def tearDown(self) -> None:
        configure_ssrf_protection(False)

    def test_protection_off_never_blocks_or_resolves(self) -> None:
        configure_ssrf_protection(False)
        # A hostname that does not resolve: with protection off the guard must
        # not even try DNS, so navigation stays permitted.
        self.assertIsNone(
            BrowserTool._guard_navigation_url("http://no-such-host.invalid/")
        )
        self.assertIsNone(
            BrowserTool._guard_navigation_url("http://169.254.169.254/latest/meta-data/")
        )

    def test_protection_on_blocks_metadata_endpoint(self) -> None:
        configure_ssrf_protection(True)
        refusal = BrowserTool._guard_navigation_url(
            "http://169.254.169.254/latest/meta-data/"
        )
        self.assertIsNotNone(refusal)
        self.assertIn("blocked", refusal)

    def test_protection_on_blocks_private_ranges(self) -> None:
        configure_ssrf_protection(True)
        self.assertIsNotNone(BrowserTool._guard_navigation_url("http://10.0.0.5/admin"))
        self.assertIsNotNone(BrowserTool._guard_navigation_url("http://192.168.1.1/"))

    def test_protection_on_keeps_loopback_for_dev_servers(self) -> None:
        configure_ssrf_protection(True)
        self.assertIsNone(BrowserTool._guard_navigation_url("http://127.0.0.1:5173/"))
        self.assertIsNone(BrowserTool._guard_navigation_url("http://localhost:3000/"))


class BrowserLoopSafetyTest(unittest.TestCase):
    """Slow or unbounded Playwright/DNS work must not sit on the gateway loop."""

    def tearDown(self) -> None:
        configure_ssrf_protection(False)

    def test_navigation_guard_async_runs_the_dns_check_off_the_loop(self) -> None:
        configure_ssrf_protection(True)
        seen: list[str] = []

        def spy(url: str) -> str | None:
            seen.append(threading.current_thread().name)
            return None

        with mock.patch.object(BrowserTool, "_guard_navigation_url", staticmethod(spy)):
            result = asyncio.run(BrowserTool._guard_navigation_url_async("http://10.0.0.5/"))
        self.assertIsNone(result)
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], "MainThread")

    def test_evaluate_is_bounded_when_the_page_script_never_returns(self) -> None:
        class HungPage:
            url = "http://localhost/"

            def is_closed(self) -> bool:
                return False

            async def evaluate(self, script, *args):
                await asyncio.sleep(3600)

        class FakeSession:
            def __init__(self) -> None:
                self.history = []

            def record_history(self, action, kwargs) -> None:
                pass

            async def ensure_page(self):
                return HungPage()

        tool = BrowserTool()
        with mock.patch.object(browser_mod, "_EVALUATE_TIMEOUT_S", 0.05):
            result = asyncio.run(tool._dispatch(FakeSession(), "evaluate", {"javascript": "while(true){}"}))
        self.assertTrue(result.is_error)
        self.assertIn("did not finish within", str(result))

    def test_session_close_does_not_wait_forever_on_a_dead_browser(self) -> None:
        class Hung:
            async def close(self) -> None:
                await asyncio.sleep(3600)

            async def stop(self) -> None:
                await asyncio.sleep(3600)

        session = _BrowserSession(browser_mod.BrowserToolConfig(live_view=False))
        session._context = Hung()
        session._browser = Hung()
        session._pw = Hung()
        with mock.patch.object(browser_mod, "_CLOSE_TIMEOUT_S", 0.05):
            asyncio.run(asyncio.wait_for(session.close(), 5))
        self.assertIsNone(session._browser)
        self.assertIsNone(session._pw)


if __name__ == "__main__":
    unittest.main()
