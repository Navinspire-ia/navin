"""The browser honours tools.ssrf_protection like web/scrape already did.

Before this guard the browser was the bypass: with SSRF protection turned on,
web.fetch refused the cloud metadata endpoint but browser(action=navigate)
happily loaded it and returned the page body.
"""

from __future__ import annotations

import unittest

from navin.agent.tools.browser import BrowserTool
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


if __name__ == "__main__":
    unittest.main()
