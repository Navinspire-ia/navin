# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Anti-bot challenges must hand the page back to the user, not stall the turn.

Reported flow: the agent opens a site behind Cloudflare (or Turnstile,
hCaptcha, reCAPTCHA), the challenge page loads, and the agent either grinds
on it or gives up silently. The fix: a snapshot on a challenge page carries
a clear directive - tell the user, let them solve it, re-snapshot to
confirm, and skip the target if it cannot clear. The agent never tries to
defeat the challenge itself.
"""

from __future__ import annotations

import unittest

from navin.agent.tools.browser import BrowserTool, BrowserToolConfig, _BrowserSession


class _FakePage:
    """Dispatches evaluate() between the snapshot JS and the challenge JS."""

    def __init__(self, challenge: dict | None = None) -> None:
        self._challenge = challenge or {}

    def locator(self, _selector: str):
        class _L:
            async def count(self) -> int:
                return 0

            async def is_visible(self) -> bool:
                return False

        return _L()

    async def evaluate(self, js: str) -> dict:
        if "challenges.cloudflare.com" in js:
            return dict(self._challenge)
        return {
            "title": "Just a moment...",
            "url": "https://example.test/dashboard",
            "elements": [],
            "text": "Checking your browser before accessing.",
        }


class ChallengeDetectionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tool = BrowserTool()
        self.session = _BrowserSession(BrowserToolConfig())

    async def _snapshot(self, challenge: dict) -> str:
        return await self.tool._snapshot(self.session, _FakePage(challenge))

    async def test_cloudflare_challenge_names_the_kind_and_the_user_handoff(self) -> None:
        snapshot = await self._snapshot({"cloudflare": True})
        self.assertIn("[challenge:cloudflare]", snapshot)
        self.assertIn("ask them to solve it", snapshot)
        self.assertIn("do not attempt to solve or bypass it programmatically", snapshot)
        self.assertIn("skip browser testing for this target", snapshot)
        # The note sits right under Page/URL.
        self.assertLess(
            snapshot.index("[challenge:cloudflare]"), snapshot.index("Checking your browser")
        )

    async def test_every_supported_kind_is_reported(self) -> None:
        cases = {
            "turnstile": {"turnstile": True},
            "hcaptcha": {"hcaptcha": True},
            "recaptcha": {"recaptcha": True},
        }
        for kind, markers in cases.items():
            with self.subTest(kind=kind):
                snapshot = await self._snapshot(markers)
                self.assertIn(f"[challenge:{kind}]", snapshot)

    async def test_no_challenge_no_note(self) -> None:
        snapshot = await self._snapshot({"cloudflare": False, "turnstile": False})
        self.assertNotIn("[challenge:", snapshot)

    async def test_evaluate_errors_are_swallowed(self) -> None:
        class _BrokenPage(_FakePage):
            # Real-world case: the page navigates away mid-snapshot, so the
            # challenge probe dies. The snapshot itself must still come back.
            async def evaluate(self, js: str) -> dict:
                if "challenges.cloudflare.com" in js:
                    raise RuntimeError("execution context destroyed")
                return await super().evaluate(js)

        snapshot = await self.tool._snapshot(self.session, _BrokenPage())
        self.assertNotIn("[challenge:", snapshot)
        self.assertIn("Page: Just a moment...", snapshot)

    async def test_detect_challenge_returns_first_matching_kind(self) -> None:
        page = _FakePage({"cloudflare": False, "turnstile": True, "hcaptcha": True})
        kind = await self.tool._detect_challenge(page)
        self.assertEqual(kind, "turnstile")


if __name__ == "__main__":
    unittest.main()
